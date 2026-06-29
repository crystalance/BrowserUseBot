"""M4 — CDP screencast remote view.

Serves a single-tab live view of the agent's browser plus basic input
forwarding (click + type), so a human can complete a login. Connects to the
browser-use session's CDP endpoint, runs Page.startScreencast, relays frames to
a tiny web page, and forwards mouse/keyboard via Input.dispatch*.

Lighter than noVNC: one tab, reuses the existing CDP connection.
"""

from __future__ import annotations

import asyncio
import json
import logging

import aiohttp
from aiohttp import web

logger = logging.getLogger("browseruse_bot")

_PAGE = """<!doctype html><meta charset=utf-8><title>Login handoff</title>
<style>body{margin:0;background:#111}#c{max-width:100%;display:block;margin:auto}</style>
<canvas id=c></canvas><script>
const c=document.getElementById('c'),x=c.getContext('2d'),img=new Image();
const ws=new WebSocket((location.protocol=='https:'?'wss':'ws')+'://'+location.host+'/ws');
img.onload=()=>{c.width=img.width;c.height=img.height;x.drawImage(img,0,0)};
ws.onmessage=e=>{img.src='data:image/jpeg;base64,'+e.data};
c.onclick=e=>{const r=c.getBoundingClientRect();ws.send(JSON.stringify({t:'click',
x:e.offsetX*c.width/r.width,y:e.offsetY*c.height/r.height}))};
addEventListener('keydown',e=>{if(e.key.length===1||e.key=='Enter'||e.key=='Backspace')
ws.send(JSON.stringify({t:'key',k:e.key}))});
</script>"""


class RemoteView:
    def __init__(self, cdp_http: str, port: int = 8770) -> None:
        # cdp_http may be a ws browser endpoint (ws://host:port/devtools/...);
        # the /json/list REST API lives at http://host:port.
        from urllib.parse import urlparse

        u = urlparse(cdp_http)
        self._cdp_http = f"http://{u.hostname}:{u.port}"
        self._port = port
        self._runner: web.AppRunner | None = None

    async def _page_ws_url(self) -> str:
        async with aiohttp.ClientSession() as s, s.get(f"{self._cdp_http}/json/list") as r:
            for t in await r.json():
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                    return t["webSocketDebuggerUrl"]
        raise RuntimeError("no CDP page target found")

    async def _bridge(self, client_ws: web.WebSocketResponse) -> None:
        cdp = await aiohttp.ClientSession().ws_connect(await self._page_ws_url())
        _id = 0

        async def send(method: str, params: dict) -> None:
            nonlocal _id
            _id += 1
            await cdp.send_str(json.dumps({"id": _id, "method": method, "params": params}))

        await send("Page.startScreencast", {"format": "jpeg", "quality": 60, "everyNthFrame": 2})

        async def pump_cdp() -> None:
            async for msg in cdp:
                data = json.loads(msg.data)
                if data.get("method") == "Page.screencastFrame":
                    await client_ws.send_str(data["params"]["data"])
                    await send("Page.screencastFrameAck", {"sessionId": data["params"]["sessionId"]})

        async def pump_client() -> None:
            async for msg in client_ws:
                e = json.loads(msg.data)
                if e["t"] == "click":
                    for typ in ("mousePressed", "mouseReleased"):
                        await send("Input.dispatchMouseEvent",
                                   {"type": typ, "x": e["x"], "y": e["y"], "button": "left", "clickCount": 1})
                elif e["t"] == "key":
                    k = e["k"]
                    await send("Input.dispatchKeyEvent", {"type": "keyDown", "text": "" if len(k) > 1 else k,
                                                          "key": k})
                    await send("Input.dispatchKeyEvent", {"type": "keyUp", "key": k})

        await asyncio.gather(pump_cdp(), pump_client())

    async def _ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        try:
            await self._bridge(ws)
        except Exception as e:  # noqa: BLE001
            logger.warning("remote view bridge ended: %s", e)
        return ws

    async def start(self) -> str:
        app = web.Application()
        app.router.add_get("/", lambda r: web.Response(text=_PAGE, content_type="text/html"))
        app.router.add_get("/ws", self._ws)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        await web.TCPSite(self._runner, "127.0.0.1", self._port).start()
        return f"http://127.0.0.1:{self._port}"

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

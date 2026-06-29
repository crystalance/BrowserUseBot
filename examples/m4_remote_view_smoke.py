"""M4 smoke test: start a session, open RemoteView, fetch page + one frame."""

import asyncio

import aiohttp
from dotenv import load_dotenv

from browser_use import BrowserSession
from browseruse_bot.platform.remote_view import RemoteView


async def main() -> None:
    load_dotenv()
    s = BrowserSession(headless=True, user_data_dir="./workspace/profile_test")
    await s.start()
    await s.navigate_to("https://example.com")
    view = RemoteView(s.cdp_url, port=8771)
    url = await view.start()
    print("remote view at", url)
    async with aiohttp.ClientSession() as cs:
        async with cs.get(url) as r:
            print("page ok:", r.status, "html bytes:", len(await r.text()))
        async with cs.ws_connect(url.replace("http", "ws") + "/ws") as ws:
            msg = await asyncio.wait_for(ws.receive(), timeout=15)
            print("got frame:", len(msg.data), "base64 chars")
    await view.stop()
    await s.kill()


if __name__ == "__main__":
    asyncio.run(main())

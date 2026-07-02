"""noVNC login handoff (AWS/Linux).

Unlike ``RemoteView`` (a CDP screencast of a single tab), this exposes the whole
X display the agent's headful browser renders on, via ``x11vnc`` + ``websockify``.
The human then sees and controls the *actual* agent browser — so any login method
works (QR scan, password, IME, 2FA), and the agent continues with that session.

Assumes an X display (default ``:99``, e.g. an ``Xvfb``) already exists and the
bot launched its browser under it. Started on demand at a login wall, stopped on
``/done``. Serves ``vnc.html`` on ``0.0.0.0:<port>``; the public link is built
from ``PUBLIC_HOST`` so no tunnel is needed on a box with a public IP.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger("browseruse_bot")

# Mobile-friendly noVNC client params: auto-connect, scale to screen, reconnect.
_PARAMS = "autoconnect=true&resize=scale&reconnect=true&show_dot=true"


def _find_novnc_web() -> str | None:
    """Locate the noVNC static web dir (holds vnc.html)."""
    for p in ("/usr/share/novnc", "/usr/share/webapps/novnc"):
        if Path(p, "vnc.html").is_file():
            return p
    return None


class NoVncView:
    """On-demand x11vnc + websockify exposing the agent's X display."""

    def __init__(
        self,
        *,
        port: int = 8770,
        display: str | None = None,
        vnc_port: int = 5900,
        public_host: str | None = None,
        web_dir: str | None = None,
    ) -> None:
        self._port = port
        self._display = display or os.getenv("DISPLAY", ":99")
        self._vnc_port = vnc_port
        self._public_host = public_host or os.getenv("PUBLIC_HOST", "")
        self._web_dir = web_dir or _find_novnc_web()
        self._x11vnc: asyncio.subprocess.Process | None = None
        self._websockify: asyncio.subprocess.Process | None = None

    async def start(self) -> str:
        if not shutil.which("x11vnc") or not shutil.which("websockify"):
            raise RuntimeError("x11vnc/websockify not installed (apt install x11vnc websockify novnc)")
        if not self._web_dir:
            raise RuntimeError("noVNC web dir not found (apt install novnc)")

        self._x11vnc = await asyncio.create_subprocess_exec(
            "x11vnc", "-display", self._display, "-rfbport", str(self._vnc_port),
            "-nopw", "-forever", "-shared", "-quiet",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        self._websockify = await asyncio.create_subprocess_exec(
            "websockify", "--web", self._web_dir,
            f"0.0.0.0:{self._port}", f"localhost:{self._vnc_port}",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.sleep(1)  # let both bind before we hand out the link

        host = self._public_host or "127.0.0.1"
        return f"http://{host}:{self._port}/vnc.html?{_PARAMS}"

    async def stop(self) -> None:
        for proc in (self._websockify, self._x11vnc):
            if proc and proc.returncode is None:
                proc.terminate()
        self._websockify = None
        self._x11vnc = None

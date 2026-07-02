"""Cloudflare quick-tunnel helper: turn a local URL into a public https link.

If `cloudflared` is installed, start a free quick tunnel and parse its
trycloudflare.com URL. Otherwise fall back to the local URL (fine for local
testing on the same machine). No domain required.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from pathlib import Path

logger = logging.getLogger("browseruse_bot")


def _find_cloudflared() -> str | None:
    """Locate cloudflared on PATH or in known winget/install locations."""
    exe = shutil.which("cloudflared")
    if exe:
        return exe
    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "cloudflared" / "cloudflared.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "cloudflared" / "cloudflared.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "cloudflared.exe",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


class Tunnel:
    def __init__(self, port: int) -> None:
        self._port = port
        self._proc: asyncio.subprocess.Process | None = None

    async def open(self) -> str:
        cloudflared = _find_cloudflared()
        if not cloudflared:
            logger.info("cloudflared not installed → using local URL")
            return f"http://127.0.0.1:{self._port}"

        self._proc = await asyncio.create_subprocess_exec(
            cloudflared, "tunnel", "--url", f"http://127.0.0.1:{self._port}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        assert self._proc.stdout
        for _ in range(60):
            line = (await self._proc.stdout.readline()).decode(errors="ignore")
            m = re.search(r"https://[\w-]+\.trycloudflare\.com", line)
            if m:
                return m.group(0)
        return f"http://127.0.0.1:{self._port}"

    async def close(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()

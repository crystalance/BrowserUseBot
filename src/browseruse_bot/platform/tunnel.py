"""Cloudflare quick-tunnel helper: turn a local URL into a public https link.

If `cloudflared` is installed, start a free quick tunnel and parse its
trycloudflare.com URL. Otherwise fall back to the local URL (fine for local
testing on the same machine). No domain required.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil

logger = logging.getLogger("browseruse_bot")


class Tunnel:
    def __init__(self, port: int) -> None:
        self._port = port
        self._proc: asyncio.subprocess.Process | None = None

    async def open(self) -> str:
        if not shutil.which("cloudflared"):
            logger.info("cloudflared not installed → using local URL")
            return f"http://127.0.0.1:{self._port}"

        self._proc = await asyncio.create_subprocess_exec(
            "cloudflared", "tunnel", "--url", f"http://127.0.0.1:{self._port}",
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

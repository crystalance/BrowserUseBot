"""M1 policy + M2 login handoff — both ride browser-use's per-step hook.

A single ``on_step_start`` hook that:
  * M1: stops the agent if the current host isn't allow-listed.
  * M2: pauses on a login wall, emits ``login_required``, waits for a human
    ``resume`` signal, then continues — using the agent's pause/resume.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse

from browseruse_bot.core.policy import TaskPolicy

logger = logging.getLogger("browseruse_bot")

# Dumb v1 login detection: host/URL markers. Smarten only if it misfires.
_LOGIN_MARKERS = ("login", "signin", "sign-in", "auth", "sso")


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


class Handoff:
    """Stateful per-task hook holding policy + the human-resume signal."""

    def __init__(
        self,
        policy: TaskPolicy,
        on_login_required: Callable[[str, object], Awaitable[None]] | None = None,
    ) -> None:
        self.policy = policy
        self._on_login_required = on_login_required
        self._resume = asyncio.Event()
        self.needed_human = False
        self._handled = False  # only hand off once per task

    def signal_done(self) -> None:
        """Platform calls this when the human finished logging in (/done)."""
        self._resume.set()

    def _host_allowed(self, host: str) -> bool:
        if not self.policy.allowed_hosts:
            return True  # empty allow-list = unrestricted
        return any(host == h or host.endswith("." + h) for h in self.policy.allowed_hosts)

    def _is_login_host(self, host: str) -> bool:
        return any(host == h or host.endswith("." + h) for h in self.policy.login_hosts)

    async def _is_login_wall(self, url: str, agent) -> bool:
        if any(m in url.lower() for m in _LOGIN_MARKERS):
            return True
        if self._is_login_host(_host(url)):
            return True
        try:  # DOM check: a password field means a login form
            state = await agent.browser_session.get_browser_state_summary(cached=True)
            for el in (state.dom_state.selector_map or {}).values():
                if (el.attributes or {}).get("type") == "password":
                    return True
        except Exception:  # noqa: BLE001
            pass
        return False

    async def on_step_start(self, agent) -> None:
        url = await agent.browser_session.get_current_page_url()
        host = _host(url)

        # M1: allow-list enforcement
        if host and not self._host_allowed(host):
            logger.warning("⛔ host %s not in allow-list → stopping", host)
            agent.stop()
            return

        # M2: login handoff (once per task)
        if not self._handled and await self._is_login_wall(url, agent):
            self._handled = True
            self.needed_human = True
            self._resume.clear()
            logger.info("🔐 login_required at %s", url)
            if self._on_login_required:
                await self._on_login_required(url, agent)
            # Awaiting here pauses the agent between steps WITHOUT browser-use's
            # interactive pause() (which would block the event loop on stdin).
            await self._resume.wait()

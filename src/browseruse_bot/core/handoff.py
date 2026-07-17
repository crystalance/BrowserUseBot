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

# Site-AGNOSTIC "is a login wall blocking the page?" heuristic. There is no
# universal DOM signal for "logged in" (session cookies are httpOnly + named
# per-site), but a login *wall* generalizes: a password field, or a visible modal
# (ARIA dialog / login-classed container) whose text contains a login keyword in
# any major language. Keyed off the login UI, never off avatars/profile links
# (those belong to OTHER users on feed/search pages and falsely read as logged-in).
_LOGIN_WALL_JS = (
    "(() => {"
    "  if (document.querySelector('input[type=\"password\"]')) return true;"
    "  const kw = /(log\\s?in|sign\\s?in|log in to|sign in to|登[录入錄]|扫码登录|手机号登录|"
    "se connecter|anmelden|iniciar sesi|ログイン|로그인|войти)/i;"
    "  const vis = el => { const s = getComputedStyle(el); const r = el.getBoundingClientRect();"
    "    return s.display!=='none' && s.visibility!=='hidden' && (+s.opacity||1)>0 &&"
    "    r.width>200 && r.height>150; };"
    "  const cands = document.querySelectorAll('[role=\"dialog\"],[aria-modal=\"true\"],"
    "dialog[open],[class*=\"login\" i],[class*=\"signin\" i],[class*=\"sign-in\" i],"
    "[class*=\"auth\" i],[id*=\"login\" i]');"
    "  for (const el of cands) { if (vis(el) && kw.test(el.innerText||'')) return true; }"
    "  return false;"
    "})()"
)


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
        self._stopped = False  # set by signal_stop()
        self._agent = None  # current browser-use Agent (set in on_step_start)

    def signal_done(self) -> None:
        """Platform calls this when the human finished logging in (/done)."""
        self._resume.set()

    def signal_stop(self) -> None:
        """Platform calls this on /stop: release any login wait and mark stopped."""
        self._stopped = True
        self._resume.set()

    def _host_allowed(self, host: str) -> bool:
        if not self.policy.allowed_hosts:
            return True  # empty allow-list = unrestricted
        return any(host == h or host.endswith("." + h) for h in self.policy.allowed_hosts)

    def _is_login_host(self, host: str) -> bool:
        return any(host == h or host.endswith("." + h) for h in self.policy.login_hosts)

    async def _eval_bool(self, agent, expr: str) -> bool | None:
        """Evaluate a JS boolean expression in the page. None if it can't run."""
        try:
            cdp = await agent.browser_session.get_or_create_cdp_session()
            res = await cdp.cdp_client.send.Runtime.evaluate(
                params={"expression": expr, "returnByValue": True},
                session_id=cdp.session_id,
            )
            return bool(res.get("result", {}).get("value"))
        except Exception:  # noqa: BLE001
            return None

    async def _is_login_wall(self, url: str, agent) -> bool:
        # Definite auth pages by URL (site-agnostic).
        if any(m in url.lower() for m in _LOGIN_MARKERS):
            return True
        # Universal DOM heuristic: a login wall on ANY site (password field or a
        # visible login modal with login-keyword text in any major language).
        hit = await self._eval_bool(agent, _LOGIN_WALL_JS)
        if hit:
            return True
        # Explicit per-site safety net: if a task declares login_hosts and the
        # heuristic couldn't even run (eval failed), be safe and hand off.
        if hit is None and self._is_login_host(_host(url)):
            return True
        return False

    async def on_step_start(self, agent) -> None:
        self._agent = agent  # remembered so a tool (request_login) can hand off too
        if self._stopped:
            agent.stop()
            return
        url = await agent.browser_session.get_current_page_url()
        host = _host(url)

        # M1: allow-list enforcement
        if host and not self._host_allowed(host):
            logger.warning("⛔ host %s not in allow-list → stopping", host)
            agent.stop()
            return

        # M2: login handoff (once per task)
        if not self._handled and await self._is_login_wall(url, agent):
            await self._do_handoff(agent, url, reason="auto-detected login wall")

    async def _do_handoff(self, agent, url: str, *, reason: str) -> bool:
        """Run the human login handoff: notify, then pause until /done (or /stop).

        Returns True if the human completed login, False if stopped.
        """
        self._handled = True
        self.needed_human = True
        self._resume.clear()
        logger.info("🔐 login_required at %s (%s)", url, reason)
        if self._on_login_required:
            await self._on_login_required(url, agent)
        # Awaiting here pauses the agent between steps WITHOUT browser-use's
        # interactive pause() (which would block the event loop on stdin).
        await self._resume.wait()
        if self._stopped:
            agent.stop()
            return False
        return True

    async def request_login(self, reason: str = "") -> bool:
        """Agent-invoked login handoff (the LLM decided a login wall is blocking it).

        The universal path: works on ANY site/auth style the deterministic heuristic
        misses, because the LLM judges the page. Returns True once the human logs in.
        """
        agent = self._agent
        if agent is None:
            return False
        url = ""
        try:
            url = await agent.browser_session.get_current_page_url()
        except Exception:  # noqa: BLE001
            pass
        self._handled = False  # allow an agent request even after an earlier handoff
        return await self._do_handoff(agent, url, reason=f"agent-requested: {reason}"[:120])

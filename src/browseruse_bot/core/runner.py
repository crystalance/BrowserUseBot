"""BrowserAgentRunner — the only public entry point of the core.

M0: run a browser-use Agent with our LLM + a persistent profile, return a
``Result``. M1: enforce a ``TaskPolicy`` allow-list. M2: login handoff via a
per-step hook (pause → ask human → resume). The signature is final.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from browser_use import Agent, BrowserSession

from browseruse_bot.core.handoff import Handoff
from browseruse_bot.core.llm import build_llm
from browseruse_bot.core.policy import TaskPolicy
from browseruse_bot.core.result import Result
from browseruse_bot.core.store import RunStore

logger = logging.getLogger("browseruse_bot")


def _find_playwright_chromium() -> str | None:
    """Locate the full (headful-capable) Playwright Chromium, never Edge.

    Returns the highest-versioned ``chrome-win64\\chrome.exe`` under the
    ms-playwright cache, or ``None`` if not installed.
    """
    base = Path.home() / "AppData" / "Local" / "ms-playwright"
    candidates = sorted(base.glob("chromium-*/chrome-win64/chrome.exe"), reverse=True)
    return str(candidates[0]) if candidates else None


class BrowserAgentRunner:
    """Wraps browser-use. ``run_task(goal, policy) -> Result``."""

    def __init__(
        self,
        *,
        llm=None,
        profile_dir: str = "./workspace/profile_main",
        max_steps: int = 25,
        headless: bool = False,
        executable_path: str | None = None,
        use_vision: bool = False,
        on_login_required: Callable[[str, object], Awaitable[None]] | None = None,
    ) -> None:
        self._llm = llm
        self._profile_dir = str(Path(profile_dir).expanduser().resolve())
        self._max_steps = max_steps
        self._headless = headless
        self._executable_path = executable_path or _find_playwright_chromium()
        self._use_vision = use_vision
        self._on_login_required = on_login_required
        self.handoff: Handoff | None = None  # set per-run; platform calls signal_done()
        self.store = RunStore()

    async def run_task(self, goal: str, policy: TaskPolicy | None = None) -> Result:
        policy = policy or TaskPolicy()
        Path(self._profile_dir).mkdir(parents=True, exist_ok=True)

        self.handoff = Handoff(policy, on_login_required=self._on_login_required)
        session = BrowserSession(
            headless=self._headless,
            user_data_dir=self._profile_dir,
            executable_path=self._executable_path,
        )
        agent = Agent(
            task=goal,
            llm=self._llm or build_llm(),
            browser_session=session,
            use_vision=self._use_vision,
        )

        t0 = time.perf_counter()
        history = await agent.run(max_steps=self._max_steps, on_step_start=self.handoff.on_step_start)

        result = Result(
            ok=bool(history.is_done() and history.is_successful() is not False),
            summary=history.final_result() or "(no result returned)",
            steps=len(history.history),
            needed_human=self.handoff.needed_human,
        )
        self.store.record(
            goal=goal, ok=result.ok, steps=result.steps, needed_human=result.needed_human,
            latency_s=round(time.perf_counter() - t0, 1), summary=result.summary,
        )
        return result

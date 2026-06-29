"""BrowserAgentRunner — the only public entry point of the core.

M0: run a browser-use Agent with our LLM + a persistent profile, return a
``Result``. M1: enforce a ``TaskPolicy`` allow-list. M2: login handoff via a
per-step hook (pause → ask human → resume). The signature is final.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from browser_use import Agent, BrowserSession

from browseruse_bot.core.handoff import Handoff
from browseruse_bot.core.llm import build_llm
from browseruse_bot.core.policy import TaskPolicy
from browseruse_bot.core.result import Result

logger = logging.getLogger("browseruse_bot")


class BrowserAgentRunner:
    """Wraps browser-use. ``run_task(goal, policy) -> Result``."""

    def __init__(
        self,
        *,
        llm=None,
        profile_dir: str = "./workspace/profile",
        max_steps: int = 25,
        headless: bool = False,
        on_login_required: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._llm = llm
        self._profile_dir = str(Path(profile_dir).expanduser().resolve())
        self._max_steps = max_steps
        self._headless = headless
        self._on_login_required = on_login_required
        self.handoff: Handoff | None = None  # set per-run; platform calls signal_done()

    async def run_task(self, goal: str, policy: TaskPolicy | None = None) -> Result:
        policy = policy or TaskPolicy()
        Path(self._profile_dir).mkdir(parents=True, exist_ok=True)

        self.handoff = Handoff(policy, on_login_required=self._on_login_required)
        session = BrowserSession(headless=self._headless, user_data_dir=self._profile_dir)
        agent = Agent(task=goal, llm=self._llm or build_llm(), browser_session=session)

        history = await agent.run(max_steps=self._max_steps, on_step_start=self.handoff.on_step_start)

        return Result(
            ok=bool(history.is_done() and history.is_successful() is not False),
            summary=history.final_result() or "(no result returned)",
            steps=len(history.history),
            needed_human=self.handoff.needed_human,
        )

"""BrowserAgentRunner — the only public entry point of the core.

M0: run a browser-use Agent with our LLM + a persistent profile, return a
``Result``. M1: enforce a ``TaskPolicy`` allow-list. M2: login handoff via a
per-step hook (pause → ask human → resume). The signature is final.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from browser_use import Agent, BrowserSession

from browseruse_bot.core.handoff import Handoff
from browseruse_bot.core.llm import build_llm
from browseruse_bot.core.policy import TaskPolicy
from browseruse_bot.core.result import Result
from browseruse_bot.core.store import RunStore
from browseruse_bot.core.synthesizer import synthesize
from browseruse_bot.core.tools import CollectedStore, build_tools

logger = logging.getLogger("browseruse_bot")


def _find_playwright_chromium() -> str | None:
    """Locate a headful-capable Chromium binary, never Edge.

    Order: ``BROWSER_EXECUTABLE_PATH`` env override → Playwright cache
    (Windows or Linux) → ``None`` (let browser-use manage its own).
    """
    env = os.getenv("BROWSER_EXECUTABLE_PATH", "").strip()
    if env:
        return env
    base = Path.home() / "AppData" / "Local" / "ms-playwright"
    win = sorted(base.glob("chromium-*/chrome-win64/chrome.exe"), reverse=True)
    if win:
        return str(win[0])
    lin_base = Path.home() / ".cache" / "ms-playwright"
    lin = sorted(
        [
            *lin_base.glob("chromium-*/chrome-linux64/chrome"),
            *lin_base.glob("chromium-*/chrome-linux/chrome"),
        ],
        reverse=True,
    )
    return str(lin[0]) if lin else None


def _ensure_display() -> None:
    """On Linux, a headful Chromium needs an X server via ``DISPLAY``.

    When the bot is launched directly (not via ``scripts/run-aws.sh``),
    ``DISPLAY`` is often unset, so Chromium can't connect to any X server and
    ``BrowserStartEvent`` hangs until it times out. Default it to the virtual
    display the noVNC stack uses (``DISPLAY_NUM``, else ``:99``) so headful runs
    work regardless of entry point. No-op on non-Linux or when already set.
    """
    if not sys.platform.startswith("linux"):
        return
    if os.environ.get("DISPLAY", "").strip():
        return
    num = os.getenv("DISPLAY_NUM", "99").lstrip(":")
    display = f":{num}"
    os.environ["DISPLAY"] = display
    logger.warning("DISPLAY was unset; defaulting to %s for headful Chromium.", display)


def _save_transcript(goal: str, history, result: Result) -> None:
    """Dump the agent's full step history to workspace/logs/transcripts for replay.

    Best-effort: a transcript failure must never break a task.
    """
    try:
        import json
        from datetime import datetime, timezone

        log_dir = Path(os.getenv("LOG_DIR", "./workspace/logs")).expanduser() / "transcripts"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = log_dir / f"run-{stamp}.json"

        # Prefer browser-use's own serializer; fall back to a minimal summary.
        steps = None
        for attr in ("model_dump", "dict"):
            fn = getattr(history, attr, None)
            if callable(fn):
                try:
                    steps = fn()
                    break
                except Exception:  # noqa: BLE001
                    steps = None

        payload = {
            "goal": goal,
            "ok": result.ok,
            "steps": result.steps,
            "needed_human": result.needed_human,
            "summary": result.summary,
            "history": steps,
        }
        path.write_text(json.dumps(payload, default=str, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("\U0001f4dd transcript saved: %s", path)
    except Exception as e:  # noqa: BLE001
        logger.warning("could not save transcript: %s", e)


class BrowserAgentRunner:
    """Wraps browser-use. ``run_task(goal, policy) -> Result``."""

    def __init__(
        self,
        *,
        llm=None,
        profile_dir: str = "./workspace/browser-use-user-data-dir-main",
        max_steps: int = 40,
        headless: bool = False,
        executable_path: str | None = None,
        use_vision: bool = False,
        on_login_required: Callable[[str, object], Awaitable[None]] | None = None,
    ) -> None:
        self._llm = llm
        # NOTE: the dir name MUST contain "browser-use-user-data-dir-" so
        # browser-use treats it as already-persistent and does NOT copy it to a
        # throwaway temp dir each run (_copy_profile() does that for any
        # Chrome/Chromium executable, which silently discards saved logins).
        self._profile_dir = str(Path(profile_dir).expanduser().resolve())
        self._max_steps = max_steps
        self._headless = headless
        self._executable_path = executable_path or _find_playwright_chromium()
        self._use_vision = use_vision
        self._on_login_required = on_login_required
        self.handoff: Handoff | None = None  # set per-run; platform calls signal_done()
        self._agent: Agent | None = None  # active agent, for stop()
        self.store = RunStore()

    @property
    def use_vision(self) -> bool:
        return self._use_vision

    def set_vision(self, on: bool) -> None:
        """Toggle whether screenshots are sent to the LLM (takes effect next task)."""
        self._use_vision = on

    def stop(self) -> None:
        """Stop the current job immediately (idempotent; safe if nothing runs).

        Releases a login-wall wait so a paused agent can unwind, then asks the
        agent to stop. No-op when no task is active.
        """
        if self.handoff:
            self.handoff.signal_stop()
        agent = self._agent
        if agent is not None:
            try:
                agent.stop()
            except Exception:  # noqa: BLE001
                pass

    async def run_task(self, goal: str, policy: TaskPolicy | None = None) -> Result:
        policy = policy or TaskPolicy()
        Path(self._profile_dir).mkdir(parents=True, exist_ok=True)

        if not self._headless:
            _ensure_display()

        self.handoff = Handoff(policy, on_login_required=self._on_login_required)
        collected = CollectedStore()
        session = BrowserSession(
            headless=self._headless,
            user_data_dir=self._profile_dir,
            executable_path=self._executable_path,
            # On Linux servers the Chromium sandbox needs unprivileged user
            # namespaces, which Ubuntu blocks by default
            # (kernel.apparmor_restrict_unprivileged_userns=1). Without this
            # flag Chrome aborts on launch, so its CDP port never opens and
            # BrowserStartEvent times out after 30s. browser-use does not add
            # it by default; we must.
            args=(["--no-sandbox"] if sys.platform.startswith("linux") else None),
        )
        agent = Agent(
            task=goal,
            llm=self._llm or build_llm(),
            browser_session=session,
            use_vision=self._use_vision,
            tools=build_tools(collected),
        )
        self._agent = agent

        t0 = time.perf_counter()
        try:
            history = await agent.run(max_steps=self._max_steps, on_step_start=self.handoff.on_step_start)
        finally:
            self._agent = None

        # Phase 2: if the collector saved posts, synthesize the final answer from
        # their raw text (browser-less sub-agent). Else fall back to the agent's
        # own final result.
        posts = collected.load()
        if posts:
            try:
                summary = await synthesize(goal, posts, llm=self._llm)
            except Exception as e:  # noqa: BLE001
                logger.warning("synthesizer failed, using agent result: %s", e)
                summary = history.final_result() or "(no result returned)"
        else:
            summary = history.final_result() or "(no result returned)"

        result = Result(
            ok=bool(history.is_done() and history.is_successful() is not False) or bool(posts),
            summary=summary,
            steps=len(history.history),
            needed_human=self.handoff.needed_human,
        )
        self.store.record(
            goal=goal, ok=result.ok, steps=result.steps, needed_human=result.needed_human,
            latency_s=round(time.perf_counter() - t0, 1), summary=result.summary,
        )
        _save_transcript(goal, history, result)
        return result

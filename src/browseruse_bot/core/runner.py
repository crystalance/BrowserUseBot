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
from browseruse_bot.core.harvest import Company, HarvestSpec, build_chunk_brief
from browseruse_bot.core.ledger import VisitedLedger, harvest_base
from browseruse_bot.core.llm import build_llm
from browseruse_bot.core.mdwriter import save_company_md
from browseruse_bot.core.observability import end_run_trace, start_run_trace, wrap_llm
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


def _disable_screenshots(session: BrowserSession) -> None:
    """Force ``include_screenshot=False`` on every browser-state request.

    browser-use's step loop hardcodes ``include_screenshot=True`` even when
    ``use_vision`` is off (it keeps the image only for its cloud sync / run GIFs).
    With vision off the screenshot never reaches the LLM, so capturing it each step
    is wasted work — and a slow page can stall on the 15s ScreenshotWatchdog
    timeout. We wrap the session method to always skip capture.
    """
    orig = session.get_browser_state_summary

    async def _no_screenshot(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        # include_screenshot is the first positional/keyword param; force it off.
        if args:
            args = args[1:]
        kwargs.pop("include_screenshot", None)
        return await orig(*args, include_screenshot=False, **kwargs)

    # BrowserSession is a pydantic model with validate_assignment, so normal
    # attribute assignment is rejected; bypass it with object.__setattr__. The
    # instance attribute shadows the class method (functions are non-data
    # descriptors, so the instance dict wins on lookup).
    object.__setattr__(session, "get_browser_state_summary", _no_screenshot)


def _save_transcript(goal: str, history, result: Result, *, request_id: str | None = None) -> None:
    """Dump the agent's full step history to workspace/logs/transcripts for replay.

    Best-effort: a transcript failure must never break a task.
    """
    try:
        import json
        from datetime import datetime, timezone

        log_dir = Path(os.getenv("LOG_DIR", "./workspace/logs")).expanduser() / "transcripts"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        name = request_id or f"run-{stamp}"
        path = log_dir / f"{name}.json"

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
            "request_id": request_id,
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
        self._stop_requested = False  # set by stop(); breaks the harvest chunk loop
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
        self._stop_requested = True
        if self.handoff:
            self.handoff.signal_stop()
        agent = self._agent
        if agent is not None:
            try:
                agent.stop()
            except Exception:  # noqa: BLE001
                pass

    async def run_task(self, goal: str, policy: TaskPolicy | None = None,
                       *, request_id: str | None = None) -> Result:
        policy = policy or TaskPolicy()
        self._stop_requested = False
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
        # With vision off the LLM never sees the screenshot, yet browser-use
        # hardcodes include_screenshot=True every step (only for its cloud sync /
        # run GIFs). That capture is pure overhead here, and a slow page can stall
        # it up to the 15s ScreenshotWatchdog timeout. Coordinate conversion uses
        # page_info (not the screenshot), so skipping capture is safe.
        if not self._use_vision:
            _disable_screenshots(session)
        trace = start_run_trace(goal, metadata={"max_steps": self._max_steps}, request_id=request_id)
        base_llm = self._llm or build_llm()
        agent = Agent(
            task=goal,
            llm=wrap_llm(base_llm, trace, label="agent_step"),
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
                syn_llm = wrap_llm(self._llm or build_llm(), trace, label="synthesizer")
                summary = await synthesize(goal, posts, llm=syn_llm)
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
        end_run_trace(trace, summary, result.ok)
        self.store.record(
            goal=goal, ok=result.ok, steps=result.steps, needed_human=result.needed_human,
            latency_s=round(time.perf_counter() - t0, 1), summary=result.summary,
            request_id=request_id,
        )
        _save_transcript(goal, history, result, request_id=request_id)
        return result

    async def run_harvest(
        self,
        spec: "HarvestSpec",
        *,
        request_id: str | None = None,
        on_progress: Callable[[int, int, int], Awaitable[None]] | None = None,
        chunk_steps: int = 25,
        batch: int = 12,
        max_chunks: int = 20,
        stall_limit: int = 2,
    ) -> Result:
        """Long-horizon harvest of one company/scope's 面经 into a Markdown file.

        Runs the agent in bounded *chunks* — a fresh Agent (bounded context) each
        chunk over a SHARED, keep-alive browser session — driven by a persistent
        ledger (dedup + candidate queue). Each chunk is its own Langfuse trace under
        the shared session ``request_id``. Stops on target, no-progress, or the
        chunk ceiling. The `scope` (e.g. "26 ng sde") isolates on-disk output.
        """
        company = spec.company
        scope_slug = spec.scope_slug
        self._stop_requested = False
        policy = TaskPolicy()
        policy.allowed_hosts = ["xiaohongshu.com"]
        policy.login_hosts = ["xiaohongshu.com"]
        Path(self._profile_dir).mkdir(parents=True, exist_ok=True)
        if not self._headless:
            _ensure_display()

        self.handoff = Handoff(policy, on_login_required=self._on_login_required)
        ledger = VisitedLedger(company.key, scope=scope_slug)
        store = CollectedStore(path=harvest_base() / company.key / scope_slug / "raw.jsonl")
        session = BrowserSession(
            headless=self._headless,
            user_data_dir=self._profile_dir,
            executable_path=self._executable_path,
            keep_alive=True,  # survive across chunks (multiple Agent.run() calls)
            args=(["--no-sandbox"] if sys.platform.startswith("linux") else None),
        )
        if not self._use_vision:
            _disable_screenshots(session)
        base_llm = self._llm or build_llm()

        t0 = time.perf_counter()
        chunk = 0
        stalls = 0
        try:
            while ledger.saved_count() < company.target and chunk < max_chunks:
                if self._stop_requested:
                    logger.info("harvest: stop requested, ending after %d chunk(s)", chunk)
                    break
                chunk += 1
                before = ledger.saved_count()
                batch_items = ledger.next_candidates(batch)
                brief = build_chunk_brief(
                    spec, saved=before, pending=ledger.pending_count(), batch=batch_items,
                )
                ctrace = start_run_trace(
                    f"harvest {company.name} [{scope_slug}] chunk {chunk}",
                    metadata={"company": company.key, "scope": scope_slug, "chunk": chunk},
                    request_id=(f"{request_id}-c{chunk}" if request_id else None),
                    session_id=request_id,
                )
                agent = Agent(
                    task=brief,
                    llm=wrap_llm(base_llm, ctrace, label="agent_step"),
                    browser_session=session,
                    use_vision=self._use_vision,
                    tools=build_tools(store, ledger),
                )
                self._agent = agent
                try:
                    await agent.run(max_steps=chunk_steps, on_step_start=self.handoff.on_step_start)
                except Exception as e:  # noqa: BLE001
                    logger.warning("harvest chunk %d errored: %s", chunk, e)
                finally:
                    self._agent = None

                after = ledger.saved_count()
                end_run_trace(ctrace, f"saved={after} pending={ledger.pending_count()}", True)
                logger.info("harvest chunk %d: saved %d→%d, pending %d",
                            chunk, before, after, ledger.pending_count())
                if on_progress:
                    try:
                        await on_progress(chunk, after, ledger.pending_count())
                    except Exception:  # noqa: BLE001
                        pass

                if after == before:
                    stalls += 1
                    if ledger.pending_count() == 0 or stalls >= stall_limit:
                        logger.info("harvest: stopping (stalls=%d, pending=%d)",
                                    stalls, ledger.pending_count())
                        break
                else:
                    stalls = 0
        finally:
            try:
                await session.kill()
            except Exception:  # noqa: BLE001
                pass

        records = store.load()
        scope_label = spec.scope_raw or "all"
        summary = (f"{company.name} · {scope_label}: {len(records)} 面经 posts collected "
                   f"over {chunk} chunk(s).")
        try:
            md_path = await save_company_md(
                company, records, scope_slug=scope_slug, scope_label=spec.scope_raw,
                llm=(self._llm or build_llm()),
            )
            summary += f"\n📄 {md_path}"
        except Exception as e:  # noqa: BLE001
            logger.warning("harvest md organize failed: %s", e)

        result = Result(
            ok=bool(records),
            summary=summary,
            steps=chunk,
            needed_human=self.handoff.needed_human,
        )
        self.store.record(
            goal=f"harvest {company.name} [{scope_label}]", ok=result.ok, steps=chunk,
            needed_human=result.needed_human, latency_s=round(time.perf_counter() - t0, 1),
            summary=summary, request_id=request_id,
        )
        return result

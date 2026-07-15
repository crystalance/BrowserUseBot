"""Env-gated LLM observability (Langfuse).

Turns on when OBSERVABILITY=langfuse and the LANGFUSE_* keys are set. Everything
is best-effort: any tracing error is swallowed so it can never break a task.

Enable:
    OBSERVABILITY=langfuse
    LANGFUSE_HOST=http://localhost:3000
    LANGFUSE_PUBLIC_KEY=pk-lf-dev-browseruse
    LANGFUSE_SECRET_KEY=sk-lf-dev-browseruse
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger("browseruse_bot")

_client = None
_init_tried = False


def enabled() -> bool:
    return os.getenv("OBSERVABILITY", "").strip().lower() == "langfuse"


def _get_client():
    """Lazily build a cached Langfuse client, or None if unavailable."""
    global _client, _init_tried
    if _client is not None or _init_tried:
        return _client
    _init_tried = True
    if not enabled():
        return None
    try:
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
            secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
            host=os.getenv("LANGFUSE_HOST", "http://localhost:3000"),
        )
        logger.info("observability: Langfuse enabled (%s)", os.getenv("LANGFUSE_HOST"))
    except Exception as e:  # noqa: BLE001
        logger.warning("observability: Langfuse init failed, disabling: %s", e)
        _client = None
    return _client


def start_run_trace(goal: str, metadata: dict | None = None, *, request_id: str | None = None,
                    session_id: str | None = None):
    """Open a trace for a task run. Returns a trace handle or None.

    ``request_id`` (when given) becomes the trace id, so the trace is reachable at
    a deterministic URL and searchable by tag. ``session_id`` groups multiple
    traces of one long (chunked) task; it defaults to ``request_id``.
    """
    client = _get_client()
    if client is None:
        return None
    try:
        kwargs: dict = {"name": "task_run", "input": goal, "metadata": metadata or {}}
        if request_id:
            kwargs["id"] = request_id
            kwargs["tags"] = [request_id]
        if session_id or request_id:
            kwargs["session_id"] = session_id or request_id
        return client.trace(**kwargs)
    except Exception as e:  # noqa: BLE001
        logger.warning("observability: start trace failed: %s", e)
        return None


def trace_url(request_id: str | None) -> str | None:
    """Build a Langfuse deep link to a trace, or None if not resolvable.

    Needs OBSERVABILITY=langfuse, a request_id (used as the trace id), and
    LANGFUSE_PROJECT_ID for the project-scoped URL. Falls back to None so callers
    can instruct the user to search the id instead.
    """
    if not request_id or not enabled():
        return None
    host = os.getenv("LANGFUSE_HOST", "").rstrip("/")
    project = os.getenv("LANGFUSE_PROJECT_ID", "").strip()
    if not host or not project:
        return None
    return f"{host}/project/{project}/traces/{request_id}"


def end_run_trace(trace, output: str, ok: bool) -> None:
    if trace is None:
        return
    try:
        trace.update(output=output, metadata={"ok": ok})
        client = _get_client()
        if client is not None:
            client.flush()
    except Exception as e:  # noqa: BLE001
        logger.warning("observability: end trace failed: %s", e)


class _TracedLLM:
    """Proxy around a browser-use chat model that logs each ainvoke as a
    Langfuse *generation* (model, tokens, latency, errors). Delegates every other
    attribute to the wrapped model so browser-use sees a normal LLM."""

    def __init__(self, inner: Any, trace, label: str = "llm") -> None:
        self._inner = inner
        self._trace = trace
        self._label = label

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def ainvoke(self, messages, output_format=None, **kwargs):  # noqa: ANN001
        gen = None
        start = time.perf_counter()
        try:
            gen = self._trace.generation(
                name=self._label,
                model=str(getattr(self._inner, "model", "")),
                input=[getattr(m, "content", str(m)) for m in messages][-3:],
            )
        except Exception:  # noqa: BLE001
            gen = None
        try:
            resp = await self._inner.ainvoke(messages, output_format=output_format, **kwargs)
        except Exception as e:  # noqa: BLE001
            if gen is not None:
                try:
                    gen.end(level="ERROR", status_message=str(e)[:300])
                except Exception:  # noqa: BLE001
                    pass
            raise
        if gen is not None:
            try:
                usage = getattr(resp, "usage", None)
                usage_d = None
                if usage is not None:
                    usage_d = {
                        "input": getattr(usage, "prompt_tokens", None),
                        "output": getattr(usage, "completion_tokens", None),
                        "total": getattr(usage, "total_tokens", None),
                    }
                out = getattr(resp, "completion", resp)
                gen.end(
                    output=str(out)[:2000],
                    usage=usage_d,
                    metadata={"latency_s": round(time.perf_counter() - start, 2)},
                )
            except Exception:  # noqa: BLE001
                pass
        return resp


def wrap_llm(model: Any, trace, label: str = "llm") -> Any:
    """Wrap a chat model for tracing if a trace exists; else return it unchanged."""
    if trace is None or model is None:
        return model
    return _TracedLLM(model, trace, label)

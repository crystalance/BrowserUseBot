"""CodeAct-style tools for the agent.

`build_tools()` returns a browser-use ``Tools`` (all default actions) plus a
general **`run_js`** action: the agent writes a JavaScript snippet at runtime and
gets back a JSON-serializable result. This is the "write the loop as code"
capability — instead of acting on elements one-by-one (one LLM round-trip each),
the agent emits ONE snippet that, e.g., harvests title+href for every result
card at once. The agent itself decides when to use it (bulk/repetitive DOM work)
vs normal actions (navigation, clicks, login) — no separate router needed; the
tool description steers it.
"""

import json
import logging
import time
from pathlib import Path

from browser_use import Tools
from browser_use.agent.views import ActionResult
from browser_use.browser import BrowserSession
from pydantic import BaseModel, Field

logger = logging.getLogger("browseruse_bot")

_MAX_RESULT_CHARS = 8000

# Sentinel some models emit in save_items instead of the real extracted value,
# expecting the harness to backfill it from the previous run_js result.
_JS_SENTINEL = "__FROM_RUN_JS__"


async def _eval_js(browser_session: BrowserSession, code: str):
    """Evaluate JS in the current page. Returns (value, error_str); one is None."""
    cdp_session = await browser_session.get_or_create_cdp_session()
    try:
        result = await cdp_session.cdp_client.send.Runtime.evaluate(
            params={"expression": code, "returnByValue": True, "awaitPromise": True},
            session_id=cdp_session.session_id,
        )
    except Exception as e:  # noqa: BLE001
        return None, f"failed to execute: {e}"
    if result.get("exceptionDetails"):
        return None, f"JS error: {result['exceptionDetails'].get('text', 'unknown JS error')}"
    return result.get("result", {}).get("value"), None


def _to_text(value) -> str:
    try:
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _resolve_sentinels(items: list[dict], last_value) -> list[dict]:
    """Backfill any ``__FROM_RUN_JS__`` field values from the last run_js result.

    If a placeholdered record's fields map by key onto the last run_js object they
    are filled in; if the last result was a list of objects, a single placeholder
    record is expanded into that whole list.
    """
    if last_value is None:
        return items
    out: list[dict] = []
    for it in items:
        if isinstance(it, dict) and any(v == _JS_SENTINEL for v in it.values()):
            if isinstance(last_value, list):
                out.extend(d for d in last_value if isinstance(d, dict))
                continue
            src = last_value if isinstance(last_value, dict) else {}
            out.append({k: (src.get(k) if v == _JS_SENTINEL else v) for k, v in it.items()})
        else:
            out.append(it)
    return out

# Collector output: browser-use appends parsed posts here; the Phase-2
# synthesizer reads them. One JSONL file per runner instance.
COLLECTED_DIR = Path("workspace/collected")


class CollectedStore:
    """Append-only JSONL store of arbitrary structured records for one task run.

    Records are whatever the agent decides fits the task (posts, products, jobs,
    rows…). The Phase-2 synthesizer organises them per the user's request.
    """

    def __init__(self) -> None:
        COLLECTED_DIR.mkdir(parents=True, exist_ok=True)
        self.path = COLLECTED_DIR / f"run-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.jsonl"

    def append_items(self, items: list[dict]) -> int:
        with self.path.open("a", encoding="utf-8") as f:
            for it in items:
                if isinstance(it, dict):
                    f.write(json.dumps(it, ensure_ascii=False) + "\n")
        return self.count()

    def count(self) -> int:
        if not self.path.exists():
            return 0
        return sum(1 for _ in self.path.open(encoding="utf-8"))

    def load(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.open(encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return out

_RUN_JS_DESCRIPTION = (
    "Run a JavaScript snippet in the current page and return its JSON result. "
    "Use this for BULK or REPETITIVE DOM work instead of acting element-by-element: "
    "e.g. extract title+href for ALL result cards in one call, collect a list, or "
    "read many fields at once. Write an IIFE that RETURNS a JSON-serialisable value, "
    "e.g. (() => Array.from(document.querySelectorAll('a[href]')).map(a => "
    "({text: a.innerText.trim(), href: a.href}))). You may use await for async. "
    "Prefer this over repeating find_elements/extract many times."
)


class RunJsAction(BaseModel):
    code: str = Field(description="JavaScript to evaluate in the page; must return a JSON-serialisable value.")


class SaveItemsAction(BaseModel):
    items_json: str = Field(
        description=(
            "A JSON array of objects to collect, e.g. "
            '[{"title":"…","url":"…","body":"…full main text…"}]. Fields are free-form '
            "— shape them to the task (posts, products, jobs, rows…). Save the FULL raw "
            "text/values, not summaries; a later step organises them. You may call this "
            "multiple times; items accumulate."
        )
    )


_SAVE_ITEMS_DESCRIPTION = (
    "Collect structured records into the run's store for later synthesis. Pass a "
    "JSON array of objects with whatever fields fit the task, containing the ACTUAL "
    "values (never placeholders). Batch many items in one call. Save FULL raw text "
    "(e.g. a post's 正文 body), not summaries — a browser-less synthesis step turns the "
    "collected records into the final answer. To save the result of a run_js extraction, "
    "prefer extract_and_save. Returns the running total saved."
)

_EXTRACT_AND_SAVE_DESCRIPTION = (
    "Extract AND save in ONE step: run a JS snippet that RETURNS an object or an array "
    "of objects holding the actual field values (e.g. {title, url, body:'…full 正文…'}), "
    "and the returned record(s) are saved directly to the run's store. PREFER this over "
    "run_js + save_items when collecting content — it avoids re-typing and cannot lose "
    "data. The JS MUST return real values, never placeholders. Returns the running total."
)


def build_tools(store: "CollectedStore | None" = None) -> Tools:
    """Return a Tools registry with defaults + `run_js` (CodeAct) + `save_items`."""
    tools = Tools()

    # Holds the last run_js result so save_items can backfill placeholders.
    last_js: dict = {"value": None}

    @tools.action(_RUN_JS_DESCRIPTION, param_model=RunJsAction)
    async def run_js(params: RunJsAction, browser_session: BrowserSession):  # noqa: ANN202
        value, err = await _eval_js(browser_session, params.code)
        if err:
            return ActionResult(error=f"run_js {err}")
        last_js["value"] = value
        text = _to_text(value)
        truncated = len(text) > _MAX_RESULT_CHARS
        if truncated:
            text = text[:_MAX_RESULT_CHARS] + "\n…[truncated]"
        logger.info("run_js returned %d chars%s", len(text), " (truncated)" if truncated else "")
        return ActionResult(extracted_content=text, include_extracted_content_only_once=True)

    if store is not None:

        @tools.action(_EXTRACT_AND_SAVE_DESCRIPTION, param_model=RunJsAction)
        async def extract_and_save(params: RunJsAction, browser_session: BrowserSession):  # noqa: ANN202
            value, err = await _eval_js(browser_session, params.code)
            if err:
                return ActionResult(error=f"extract_and_save {err}")
            last_js["value"] = value
            items = value if isinstance(value, list) else [value]
            items = [it for it in items if isinstance(it, dict)]
            if not items:
                return ActionResult(
                    error="extract_and_save: JS must RETURN an object or array of objects "
                    "with the extracted fields (got nothing usable)."
                )
            total = store.append_items(items)
            logger.info("extract_and_save: +%d (total %d)", len(items), total)
            return ActionResult(
                extracted_content=f"Extracted & saved {len(items)} item(s); {total} collected so far.",
                include_extracted_content_only_once=True,
            )

        @tools.action(_SAVE_ITEMS_DESCRIPTION, param_model=SaveItemsAction)
        async def save_items(params: SaveItemsAction):  # noqa: ANN202
            try:
                data = json.loads(params.items_json)
            except json.JSONDecodeError as e:
                return ActionResult(error=f"save_items: items_json is not valid JSON: {e}")
            items = data if isinstance(data, list) else [data]
            items = [it for it in items if isinstance(it, dict)]
            if not items:
                return ActionResult(error="save_items: no JSON objects found in items_json")
            items = _resolve_sentinels(items, last_js["value"])
            if any(v == _JS_SENTINEL for it in items for v in it.values()):
                return ActionResult(
                    error="save_items: records still contain __FROM_RUN_JS__ placeholders. "
                    "Pass the ACTUAL extracted values, or use extract_and_save to save a "
                    "run_js result directly."
                )

            total = store.append_items(items)
            logger.info("save_items: +%d (total %d)", len(items), total)
            return ActionResult(
                extracted_content=f"Saved {len(items)} item(s); {total} collected so far.",
                include_extracted_content_only_once=True,
            )

    return tools

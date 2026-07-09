"""Phase-2 synthesizer: a browser-less sub-agent.

Takes the raw structured records the browser-use collector saved (arbitrary
fields — posts, products, jobs, rows…) and asks the LLM to organise them into the
final answer for the user. This deliberately separates browser automation
(Phase 1) from LLM synthesis (Phase 2): the collector only gathers data; the
synthesizer never touches the browser.
"""

from __future__ import annotations

import json
import logging

from browser_use.llm.messages import SystemMessage, UserMessage

from browseruse_bot.core.llm import build_llm

logger = logging.getLogger("browseruse_bot")

_SYS = (
    "You organise scraped structured records into a clear, faithful answer for the "
    "user. Use ONLY the provided records; never invent facts, values, or links. "
    "Preserve any URL/link fields exactly. Summarise long text fields into a few "
    "grounded sentences, drop irrelevant records, order sensibly, and answer in the "
    "user's language."
)


async def synthesize(goal: str, records: list[dict], llm=None) -> str:
    """Organise arbitrary collected records into the final user-facing answer."""
    if not records:
        return ""
    llm = llm or build_llm()
    blocks = []
    for i, r in enumerate(records, 1):
        # Trim very long text fields so the prompt stays bounded.
        trimmed = {k: (v[:4000] if isinstance(v, str) else v) for k, v in r.items()}
        blocks.append(f"[Record {i}]\n{json.dumps(trimmed, ensure_ascii=False, indent=2)}")
    user = (
        f"User request:\n{goal}\n\n"
        f"Collected records ({len(records)}):\n\n" + "\n\n".join(blocks) +
        "\n\nWrite the final answer for the user, grounded only in these records. "
        "Keep any links/URLs exactly as given."
    )
    resp = await llm.ainvoke([SystemMessage(content=_SYS), UserMessage(content=user)])
    text = getattr(resp, "completion", None) or str(resp)
    logger.info("synthesizer produced %d chars from %d records", len(text), len(records))
    return text

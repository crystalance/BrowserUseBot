"""Write a company's collected interview posts into a Markdown file on disk.

``organize_to_markdown`` chunks records into batches so synthesis scales past a
single LLM prompt (hundreds of posts), then concatenates the grounded sections.
"""

from __future__ import annotations

import logging
from pathlib import Path

from browseruse_bot.core.ledger import harvest_base
from browseruse_bot.core.synthesizer import synthesize

logger = logging.getLogger("browseruse_bot")

# Max records per synthesis prompt — keeps each call bounded regardless of total.
_BATCH = 20


def _goal(company, *, part: int | None = None) -> str:
    scope = f" (part {part})" if part else ""
    return (
        f"Organize these {company.name} interview-experience (面经) posts into a clean, "
        f"grouped Markdown section{scope}. Group by interview stage/role where possible "
        f"(OA / phone / onsite / behavioral / offer). Keep each post's original link, stay "
        f"faithful to the source — never invent details. Answer in Chinese."
    )


async def organize_to_markdown(company, records: list[dict], *, llm=None) -> str:
    """Turn collected records into an organized Markdown body (chunked synthesis)."""
    if not records:
        return "(no posts collected yet)"
    if len(records) <= _BATCH:
        return await synthesize(_goal(company), records, llm=llm)

    sections: list[str] = []
    for i in range(0, len(records), _BATCH):
        batch = records[i : i + _BATCH]
        part = (i // _BATCH) + 1
        try:
            sections.append(await synthesize(_goal(company, part=part), batch, llm=llm))
        except Exception as e:  # noqa: BLE001
            logger.warning("organize batch %d failed: %s", part, e)
    logger.info("organized %d posts into %d section(s)", len(records), len(sections))
    return "\n\n---\n\n".join(sections) if sections else "(synthesis failed)"


async def save_company_md(company, records: list[dict], *, scope_slug: str = "all",
                          scope_label: str = "", llm=None) -> Path:
    """Organize `records` into ``workspace/harvest/<key>/<scope>/面经.md`` and return it."""
    md_dir = harvest_base() / company.key / scope_slug
    md_dir.mkdir(parents=True, exist_ok=True)
    path = md_dir / "面经.md"
    body = await organize_to_markdown(company, records, llm=llm)
    heading = f"# {company.name} 面经"
    if scope_label:
        heading += f" · {scope_label}"
    path.write_text(f"{heading}\n\n_{len(records)} posts_\n\n{body}\n", encoding="utf-8")
    logger.info("harvest md written: %s (%d posts)", path, len(records))
    return path

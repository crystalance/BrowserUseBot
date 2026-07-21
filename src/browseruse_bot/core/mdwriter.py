"""Write a company's collected interview posts into a Markdown file on disk.

Faithful dump: each post's ORIGINAL title, date, link, and full body verbatim — no
LLM synthesis, grouping, or summarizing. (Cheaper + faster too: no extra LLM calls.)
"""

from __future__ import annotations

import logging
from pathlib import Path

from browseruse_bot.core.ledger import harvest_base

logger = logging.getLogger("browseruse_bot")


def render_records_md(company, records: list[dict], *, scope_label: str = "") -> str:
    """Render records as raw Markdown: title + date + link + full body, one per post."""
    heading = f"# {company.name} 面经"
    if scope_label:
        heading += f" · {scope_label}"
    lines: list[str] = [heading, "", f"_{len(records)} posts_", ""]
    if not records:
        lines.append("(no posts collected yet)")
        return "\n".join(lines) + "\n"

    for i, r in enumerate(records, 1):
        title = (str(r.get("title") or "")).strip() or "(untitled)"
        url = str(r.get("url") or r.get("href") or "").strip()
        date = str(r.get("date") or "").strip()
        body = str(r.get("body") or "").strip()
        lines.append(f"## {i}. {title}")
        meta = " · ".join(
            ([date] if date else []) + ([f"[原文链接]({url})"] if url else [])
        )
        if meta:
            lines.append(meta)
        lines.append("")
        lines.append(body if body else "(no body captured)")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines) + "\n"


async def save_company_md(company, records: list[dict], *, scope_slug: str = "all",
                          scope_label: str = "", llm=None) -> Path:
    """Write the raw records to ``workspace/harvest/<key>/<scope>/面经.md`` and return it.

    ``llm`` is accepted for call-site compatibility but unused (no synthesis).
    """
    md_dir = harvest_base() / company.key / scope_slug
    md_dir.mkdir(parents=True, exist_ok=True)
    path = md_dir / "面经.md"
    path.write_text(render_records_md(company, records, scope_label=scope_label), encoding="utf-8")
    logger.info("harvest md written: %s (%d posts, raw)", path, len(records))
    return path

"""Long-horizon company interview-experience harvester — config + chunk briefs.

This module holds the *pure* pieces of the harvester: the company registry and the
per-chunk brief builder. The chunked run loop that drives a fresh agent each chunk
lives in ``BrowserAgentRunner.run_harvest`` (it owns the browser session + llm).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("browseruse_bot")

COMPANIES_PATH = Path("config/companies.json")

# Sources we can harvest from. Default is Xiaohongshu; more can be added later.
SOURCE_ALIASES: dict[str, list[str]] = {
    "xiaohongshu": ["小红书", "xiaohongshu", "xhs", "rednote", "红书"],
}
DEFAULT_SOURCE = "xiaohongshu"

# Words that describe intent/plumbing, not the actual scope (year/level/role).
_STOP_WORDS = [
    "面经", "面试", "interview", "harvest", "整理", "收集", "帮我", "search", "搜索",
    "查找", "的", "在", "找", "一下", "post", "posts", "note", "notes",
]


@dataclass
class Company:
    key: str
    name: str
    aliases: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    target: int = 60
    source: str = "xiaohongshu"


@dataclass
class HarvestSpec:
    """A fully-resolved harvest request: which company, source, and scope."""
    company: Company
    source: str
    scope_raw: str          # e.g. "26 ng sde" (empty = all)
    scope_slug: str         # e.g. "26-ng-sde" (used for the on-disk folder)
    queries: list[str]
    target: int


def load_companies(path: Path | str = COMPANIES_PATH) -> dict[str, Company]:
    """Load the company registry keyed by ``key``. Empty dict if the file is missing."""
    p = Path(path)
    if not p.exists():
        logger.warning("companies config not found: %s", p)
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    out: dict[str, Company] = {}
    for d in data:
        c = Company(
            key=d["key"], name=d["name"], aliases=d.get("aliases", []),
            queries=d.get("queries", []), target=int(d.get("target", 60)),
            source=d.get("source", "xiaohongshu"),
        )
        out[c.key] = c
    return out


def match_company(text: str, companies: dict[str, Company]) -> Company | None:
    """Return the first company whose key/name/alias appears in the text."""
    t = (text or "").lower()
    for c in companies.values():
        for token in [c.key, c.name, *c.aliases]:
            if token and token.lower() in t:
                return c
    return None


def detect_source(text: str) -> str:
    """Return the source site named in the text, or the default."""
    t = (text or "").lower()
    for src, aliases in SOURCE_ALIASES.items():
        if any(a.lower() in t for a in aliases):
            return src
    return DEFAULT_SOURCE


def _slugify(text: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "-", (text or "").strip().lower()).strip("-")
    return s or "all"


def build_spec(company: Company, *, source: str = "xiaohongshu", scope: str = "",
               target: int | None = None) -> HarvestSpec:
    """Assemble a HarvestSpec (scoped search queries + on-disk slug) from parts.

    Shared by the LLM intent router and the keyword fallback — the *extraction* of
    company/scope differs, but turning them into queries is deterministic.
    """
    scope_raw = " ".join((scope or "").split()).strip()
    scope_slug = _slugify(scope_raw)
    names = [company.name, *company.aliases][:3]
    if scope_raw:
        queries = [f"{n} {scope_raw} 面经" for n in names]
        queries += [f"{q} {scope_raw}" for q in company.queries[:2]]
    else:
        queries = list(company.queries) or [f"{company.name} 面经"]
    seen: set[str] = set()
    queries = [q for q in queries if not (q in seen or seen.add(q))]
    return HarvestSpec(
        company=company, source=source or "xiaohongshu", scope_raw=scope_raw,
        scope_slug=scope_slug, queries=queries, target=target or company.target,
    )


def parse_harvest_request(goal: str, companies: dict[str, Company]) -> HarvestSpec | None:
    """Keyword fallback: resolve a `/new` goal into a HarvestSpec, or None.

    Used only if the LLM intent router is unavailable. Distinguishes source and
    scope by stripping known tokens — brittle, hence the router is preferred.
    """
    company = match_company(goal, companies)
    if not company:
        return None
    source = detect_source(goal)
    strip = [company.key, company.name, *company.aliases, *SOURCE_ALIASES.get(source, []), *_STOP_WORDS]
    scope = goal
    for tok in sorted((t for t in strip if t), key=len, reverse=True):
        scope = re.sub(re.escape(tok), " ", scope, flags=re.IGNORECASE)
    return build_spec(company, source=source, scope=scope, target=company.target)


def build_chunk_brief(spec: HarvestSpec, *, saved: int, pending: int) -> str:
    """Compact brief for one chunk. Carries counts only — the harness serves URLs."""
    company = spec.company
    scope = spec.scope_raw or "all roles/years"
    return (
        f"You are collecting Chinese interview-experience posts (面经) for "
        f"**{company.name}** on 小红书 (xiaohongshu.com), specifically for scope: "
        f"**{scope}**. Stay ON xiaohongshu.com; never use external search engines. Only save "
        f"posts that match this scope; skip clearly-unrelated ones.\n\n"
        f"Progress: {saved}/{spec.target} posts saved. {pending} posts queued to open. "
        f"Work efficiently; duplicates are auto-skipped so don't worry about overlap.\n\n"
        f"STEP 1 — SEARCH FIRST (if nothing is queued). Call `open_search` with a query like "
        f"'{spec.queries[0] if spec.queries else company.name + ' 面经'}' — it loads 小红书's "
        f"results page directly (do NOT type into the on-page search box; it fails). Then call "
        f"`discover_candidates` (JS returning an array of {{title, href}} with full xsec_token "
        f"URLs) to queue the RESULT cards. Example discover JS: "
        f"(() => Array.from(document.querySelectorAll('a[href*=\"/explore/\"], a[href*=\"/search_result/\"]'))"
        f".map(a => ({{title:(a.innerText||'').trim(), href:a.href}})).filter(x => x.href.includes('xsec_token'))). "
        f"Scroll 1–2× and discover again to grow the queue.\n\n"
        f"STEP 2 — DRAIN THE QUEUE. Call `open_next_post` — the harness navigates the browser "
        f"to the next queued post for you (never type or guess a post URL). Once it opens, call "
        f"`extract_and_save` with ONE run_js that RETURNS {{note_id, url, title, body, date}} "
        f"where body is the FULL 正文 text. If the opened post is clearly NOT about {company.name} "
        f"{scope}, skip it and call open_next_post again. Repeat until the queue is empty, then go "
        f"back to STEP 1 with another query variant ({', '.join(spec.queries[1:4])}).\n"
        f"  Extraction JS shape:\n"
        f"  (() => {{ const m = document.querySelector('#noteContainer, .note-detail-mask, "
        f"[class*=\"note-detail\"]') || document.body; return {{ url: location.href, "
        f"note_id: (location.pathname.match(/([0-9a-zA-Z]+)(?:$|\\?)/)||[])[1], "
        f"title: (m.querySelector('#detail-title, .title, h1')?.innerText||document.title||'').trim(), "
        f"body: (m.querySelector('#detail-desc, .note-content, .desc, article')?.innerText||m.innerText||'').trim().slice(0,6000), "
        f"date: (m.querySelector('.date, time, [class*=\"date\"]')?.innerText||'').trim() }}; }})\n\n"
        f"If a LOGIN wall / popup ever blocks you, call `request_login` (do NOT try to close or "
        f"bypass it yourself). Do NOT summarize — a later step organizes everything. Keep going "
        f"until you've made solid progress this round."
    )

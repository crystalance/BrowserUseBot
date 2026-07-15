"""LLM intent router for `/new` requests.

Instead of keyword/regex parsing, an LLM reads the free-text request and decides
whether it's a company interview-experience **harvest** (collect many 面经 posts)
or a normal one-off **browse** task — and, for a harvest, extracts the company,
source site, and scope (year/level/role). This is robust to phrasing: "帮我收集
谷歌明年入职后端的面经" resolves the same as "google 26 ng backend 面经".

Returns a ``HarvestSpec`` for a harvest, or ``None`` to fall through to browsing.
"""

from __future__ import annotations

import logging

from browser_use.llm.messages import SystemMessage, UserMessage
from pydantic import BaseModel, Field

from browseruse_bot.core.harvest import Company, HarvestSpec, build_spec
from browseruse_bot.core.llm import build_llm

logger = logging.getLogger("browseruse_bot")


class RouteDecision(BaseModel):
    """Structured intent extracted from the user's request."""

    task_type: str = Field(
        description="'harvest' if the user wants to collect MANY interview-experience "
        "(面经/interview) posts for a company from a social site; otherwise 'browse'."
    )
    company_key: str = Field(
        default="", description="the matching known company key, or '' if none match"
    )
    company_name: str = Field(
        default="", description="the company's display name (for harvests of unlisted companies)"
    )
    source: str = Field(
        default="xiaohongshu", description="source site to harvest from, e.g. 'xiaohongshu'"
    )
    scope: str = Field(
        default="", description="year/level/role qualifier in the user's words, e.g. "
        "'2026 new grad SDE', '实习', '27ng backend'; '' if unspecified"
    )
    target: int = Field(default=0, description="requested number of posts, 0 if unspecified")


_SYS = (
    "You are the intent router for a supervised browser-automation assistant. Read the "
    "user's request and classify it.\n"
    "- task_type='harvest' ONLY when the user wants to COLLECT/ORGANISE MANY interview-"
    "experience posts (面经 / interview experience / 面试经验) for a specific company from a "
    "social site (default 小红书/xiaohongshu). One company per request.\n"
    "- Otherwise task_type='browse' (single actions, searches, logins, one post, etc.).\n"
    "For a harvest, map the company to one of the known keys below when possible; if the "
    "company is real but not listed, still return task_type='harvest' with company_key='' and "
    "fill company_name. Put any year/level/role words into 'scope' verbatim (do not invent). "
    "Detect the source site if named."
)


async def route_request(goal: str, companies: dict[str, Company], llm=None) -> HarvestSpec | None:
    """LLM-route a request. Returns a HarvestSpec for a harvest, else None (browse)."""
    llm = llm or build_llm()
    known = "\n".join(
        f"- {c.key}: {c.name} ({', '.join(c.aliases)})" for c in companies.values()
    )
    sys = SystemMessage(content=f"{_SYS}\n\nKnown companies:\n{known}")
    resp = await llm.ainvoke([sys, UserMessage(content=goal)], output_format=RouteDecision)
    r: RouteDecision = resp.completion

    if (r.task_type or "").strip().lower() != "harvest":
        return None

    company = companies.get((r.company_key or "").strip().lower())
    if company is None and (r.company_name or "").strip():
        # A real company the registry doesn't list yet — harvest it ad-hoc.
        from browseruse_bot.core.harvest import _slugify

        name = r.company_name.strip()
        company = Company(key=_slugify(name), name=name)
    if company is None:
        return None

    target = r.target if r.target and r.target > 0 else None
    spec = build_spec(company, source=(r.source or "xiaohongshu"), scope=(r.scope or ""), target=target)
    logger.info("router: harvest %s scope=%r source=%s target=%d",
                company.key, spec.scope_raw, spec.source, spec.target)
    return spec

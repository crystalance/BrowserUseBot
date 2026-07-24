"""Sequential batch probe runner — the capability/ability-boundary tester.

Runs a curated list of probes (``tests/batch/probes.jsonl``) ONE AT A TIME through
the same ``BrowserAgentRunner`` used in production, so login handoff, tracing, and
the ledger all behave exactly as they do for a real ``/new`` task. No Docker /
concurrency: this measures COVERAGE, not throughput.

Each probe is routed (harvest vs browse) exactly like ``/new``, run, then scored
against its ``expect`` block. Results are written to
``workspace/probes/<ts>/results.jsonl`` + a ``summary.md`` table.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from browseruse_bot.core.harvest import load_companies
from browseruse_bot.core.ids import new_request_id
from browseruse_bot.core.ledger import harvest_base
from browseruse_bot.core.observability import trace_url
from browseruse_bot.core.policy import TaskPolicy
from browseruse_bot.core.router import route_request

logger = logging.getLogger("browseruse_bot")

PROBES_PATH = Path("tests/batch/probes.jsonl")


def load_probes(path: str | Path | None = None, *, tier: int | None = None) -> list[dict]:
    """Load probe rows from a JSONL file, optionally filtered to one tier."""
    p = Path(path or PROBES_PATH)
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        probe = json.loads(line)
        if tier is not None and probe.get("tier") != tier:
            continue
        out.append(probe)
    return out


def _count_records(spec) -> int | None:
    """Count saved records for a harvest (lines in its raw.jsonl)."""
    try:
        raw = harvest_base() / spec.company.key / spec.scope_slug / "raw.jsonl"
        if not raw.exists():
            return 0
        return sum(1 for ln in raw.read_text(encoding="utf-8").splitlines() if ln.strip())
    except Exception:  # noqa: BLE001
        return None


def _score(probe: dict, row: dict) -> bool:
    """Lenient pass/fail against the probe's ``expect`` block.

    This is exploration, not strict CI: the raw row is always kept so a human can
    judge. 'pass' captures the coarse signal (ran, routed correctly, hit min records).
    """
    exp = probe.get("expect", {}) or {}
    etype = exp.get("type")
    if row.get("error"):
        # some probes are designed to fail cleanly; a crash there is still a pass
        return etype in ("harvest_or_clean_fail", "clarify_or_sensible")
    if etype == "harvest":
        need = exp.get("min_records")
        if need is not None and isinstance(row.get("records"), int):
            return row["records"] >= need
        return bool(row.get("ok"))
    if etype == "browse":
        return bool(row.get("ok")) and row.get("routed") == "browse"
    # clarify_or_sensible / action / harvest_or_clean_fail → pass if it ran w/o crashing
    return True


@dataclass
class ProbeReport:
    results: list[dict] = field(default_factory=list)
    out_dir: Path | None = None
    summary_path: Path | None = None

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.get("pass"))

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def summary_line(self) -> str:
        return f"{self.passed}/{self.total} probes passed"


async def run_probe_suite(
    runner,
    *,
    probes: list[dict],
    companies: dict | None = None,
    on_event: Callable[[str], Awaitable[None]] | None = None,
) -> ProbeReport:
    """Run probes sequentially through ``runner``; write + return a report."""
    companies = companies or load_companies()
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path("workspace/probes") / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"
    summary_path = out_dir / "summary.md"

    async def _emit(msg: str) -> None:
        logger.info(msg)
        if on_event:
            await on_event(msg)

    results: list[dict] = []
    n = len(probes)
    for i, probe in enumerate(probes, 1):
        pid = probe.get("id", f"P{i}")
        goal = probe["goal"]
        request_id = new_request_id()
        await _emit(f"▶️ [{i}/{n}] {pid} · {goal}")
        t0 = time.monotonic()
        row: dict = {
            "probe_id": pid,
            "tier": probe.get("tier"),
            "request_id": request_id,
            "goal": goal,
            "expect": probe.get("expect", {}),
            "routed": None,
            "ok": False,
            "records": None,
            "wall_ms": None,
            "error": None,
            "summary": "",
            "trace_url": trace_url(request_id),
        }
        try:
            try:
                spec = await route_request(goal, companies, llm=runner._llm)
            except Exception as e:  # noqa: BLE001
                logger.warning("probe %s route failed: %s", pid, e)
                spec = None
            if spec:
                row["routed"] = "harvest"
                result = await runner.run_harvest(spec, request_id=request_id)
                row["records"] = _count_records(spec)
            else:
                row["routed"] = "browse"
                result = await runner.run_task(goal, TaskPolicy(), request_id=request_id)
            row["ok"] = bool(getattr(result, "ok", False))
            row["summary"] = (getattr(result, "summary", "") or "")[:800]
        except Exception as e:  # noqa: BLE001
            row["error"] = f"{type(e).__name__}: {e}"
            logger.exception("probe %s failed", pid)
        row["wall_ms"] = int((time.monotonic() - t0) * 1000)
        row["pass"] = _score(probe, row)
        results.append(row)
        with results_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        mark = "✅" if row["pass"] else "❌"
        extra = f" · {row['records']} recs" if row["records"] is not None else ""
        await _emit(f"{mark} {pid} · {row['routed']} · {row['wall_ms'] // 1000}s{extra}")

    _write_summary(summary_path, results)
    report = ProbeReport(results=results, out_dir=out_dir, summary_path=summary_path)
    await _emit(f"🏁 {report.summary_line} · {summary_path}")
    return report


def _write_summary(path: Path, results: list[dict]) -> None:
    passed = sum(1 for r in results if r.get("pass"))
    lines = [
        f"# Probe run — {datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
        f"**{passed}/{len(results)} passed**",
        "",
        "| # | pass | routed | recs | secs | goal | error |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        recs = r["records"] if r["records"] is not None else "-"
        secs = (r["wall_ms"] or 0) // 1000
        err = (r["error"] or "").replace("|", "/")[:60]
        goal = r["goal"].replace("|", "/")[:40]
        mark = "✅" if r.get("pass") else "❌"
        lines.append(
            f"| {r['probe_id']} | {mark} | {r['routed']} | {recs} | {secs} | {goal} | {err} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

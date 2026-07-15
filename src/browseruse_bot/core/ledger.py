"""Per-company harvest ledger: visited posts (dedup/resume) + candidate queue.

One SQLite file per company at ``workspace/harvest/<company>/ledger.sqlite``.

Two tables:
- ``visited``    — every post id we've processed and its outcome (dedup + resume).
- ``candidates`` — discovered post URLs not yet processed (durable queue so a fresh
  chunk can resume by opening URLs directly instead of re-scrolling).

Dedup is enforced here (not in the LLM prompt), so the crawl scales to hundreds of
posts without the agent context growing.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit

# Harvest output root. The per-environment sub-folder (APP_ENV: "production" on
# the server, "dev"/"test" locally) keeps prod and test data separate so both can
# be committed to git without clobbering each other. Resolved lazily so it picks
# up APP_ENV after .env is loaded.
_HARVEST_ROOT = Path("workspace/harvest")


def harvest_base() -> Path:
    return _HARVEST_ROOT / os.getenv("APP_ENV", "dev")


# Backward-compatible module attribute (evaluated once at import; prefer
# harvest_base() where the value must reflect a late-set APP_ENV).
HARVEST_DIR = harvest_base()

# Xiaohongshu note URLs: /explore/<id>, /search_result/<id>, /discovery/item/<id>
_NOTE_ID_RE = re.compile(r"/(?:explore|search_result|discovery/item)/([0-9a-zA-Z]+)")


def parse_note_id(url: str | None) -> str | None:
    """Return a stable canonical id for a post URL, or None.

    Prefers the Xiaohongshu note-id path segment; falls back to the last path
    segment (site-agnostic) so dedup still works for other sources.
    """
    if not url:
        return None
    m = _NOTE_ID_RE.search(url)
    if m:
        return m.group(1)
    seg = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    return seg or None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS visited (
    note_id TEXT PRIMARY KEY,
    url     TEXT,
    title   TEXT,
    status  TEXT NOT NULL,   -- saved | empty | failed | skipped
    ts      REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS candidates (
    note_id  TEXT PRIMARY KEY,
    href     TEXT NOT NULL,  -- full url WITH token (openable directly)
    title    TEXT,
    added_ts REAL NOT NULL
);
"""


class VisitedLedger:
    """Durable dedup + candidate queue for one company's harvest."""

    def __init__(self, company: str, scope: str | None = None,
                 base_dir: Path | str | None = None) -> None:
        self.company = company
        self.scope = scope
        base = Path(base_dir) if base_dir is not None else harvest_base()
        self.dir = base / company
        if scope:
            self.dir = self.dir / scope
        self.dir.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.dir / "ledger.sqlite")
        self._db.executescript(_SCHEMA)
        self._db.commit()

    # --- visited -----------------------------------------------------------
    def is_saved(self, note_id: str | None) -> bool:
        if not note_id:
            return False
        row = self._db.execute(
            "SELECT 1 FROM visited WHERE note_id=? AND status='saved'", (note_id,)
        ).fetchone()
        return row is not None

    def mark(self, note_id: str, *, url: str = "", title: str = "", status: str = "saved") -> None:
        self._db.execute(
            "INSERT INTO visited (note_id, url, title, status, ts) VALUES (?,?,?,?,?) "
            "ON CONFLICT(note_id) DO UPDATE SET url=excluded.url, title=excluded.title, "
            "status=excluded.status, ts=excluded.ts",
            (note_id, url, title, status, time.time()),
        )
        self._db.commit()

    def saved_count(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM visited WHERE status='saved'").fetchone()[0]

    # --- candidate queue ---------------------------------------------------
    def add_candidates(self, items: list[dict]) -> int:
        """Insert discovered {note_id|url/href, title} rows; ignore dups. Returns added."""
        added = 0
        for it in items:
            if not isinstance(it, dict):
                continue
            href = it.get("href") or it.get("url") or ""
            note_id = it.get("note_id") or parse_note_id(href)
            if not note_id or not href:
                continue
            cur = self._db.execute(
                "INSERT OR IGNORE INTO candidates (note_id, href, title, added_ts) VALUES (?,?,?,?)",
                (note_id, href, it.get("title", ""), time.time()),
            )
            added += cur.rowcount or 0
        self._db.commit()
        return added

    def next_candidates(self, limit: int = 15) -> list[dict]:
        """Return up to `limit` candidates not yet saved (durable resume cursor)."""
        rows = self._db.execute(
            "SELECT c.note_id, c.href, c.title FROM candidates c "
            "LEFT JOIN visited v ON v.note_id = c.note_id AND v.status='saved' "
            "WHERE v.note_id IS NULL ORDER BY c.added_ts LIMIT ?",
            (limit,),
        ).fetchall()
        return [{"note_id": r[0], "href": r[1], "title": r[2]} for r in rows]

    def pending_count(self) -> int:
        return self._db.execute(
            "SELECT COUNT(*) FROM candidates c LEFT JOIN visited v "
            "ON v.note_id = c.note_id AND v.status='saved' WHERE v.note_id IS NULL"
        ).fetchone()[0]

    def close(self) -> None:
        self._db.close()

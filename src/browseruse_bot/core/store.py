"""M5 — reliability store. A single SQLite table of run records + metrics.

No new deps (stdlib sqlite3). One file at workspace/runs.db. Every task writes a
record; metrics are plain SQL. browser-use's step history is persisted as the
trace artifact path.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    goal TEXT NOT NULL,
    ok INTEGER NOT NULL,
    steps INTEGER NOT NULL,
    needed_human INTEGER NOT NULL,
    latency_s REAL NOT NULL,
    summary TEXT
);
"""


@dataclass
class Metrics:
    total: int
    success: int
    handoffs: int
    avg_latency_s: float
    success_rate: float


class RunStore:
    def __init__(self, db_path: str = "./workspace/runs.db") -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path)
        self._db.execute(_SCHEMA)
        self._db.commit()

    def record(self, *, goal: str, ok: bool, steps: int, needed_human: bool,
               latency_s: float, summary: str) -> None:
        self._db.execute(
            "INSERT INTO runs (ts, goal, ok, steps, needed_human, latency_s, summary)"
            " VALUES (?,?,?,?,?,?,?)",
            (time.time(), goal, int(ok), steps, int(needed_human), latency_s, summary),
        )
        self._db.commit()

    def metrics(self) -> Metrics:
        row = self._db.execute(
            "SELECT COUNT(*), COALESCE(SUM(ok),0), COALESCE(SUM(needed_human),0),"
            " COALESCE(AVG(latency_s),0) FROM runs"
        ).fetchone()
        total, success, handoffs, avg = row
        return Metrics(total, success, handoffs, round(avg, 1),
                       round(success / total, 2) if total else 0.0)

    def close(self) -> None:
        self._db.close()

"""Request/task identifiers.

A ``request_id`` is generated the instant a task is accepted and threaded through
the whole run: it becomes the Langfuse trace id + session id (so every LLM call is
findable under one id), the SQLite run record key, and the transcript filename.
Format: ``req-YYYYMMDD-xxxxxx`` — date-sortable and collision-safe for our volume.
"""

from __future__ import annotations

import secrets
import time


def new_request_id() -> str:
    """Return a fresh request id like ``req-20260714-9fa3c1``."""
    day = time.strftime("%Y%m%d", time.gmtime())
    suffix = secrets.token_hex(3)  # 6 hex chars
    return f"req-{day}-{suffix}"

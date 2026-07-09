"""Central logging setup: console + rotating file, level from env.

``setup_logging()`` is idempotent and called once at startup. Controlled by:
  * ``LOG_LEVEL``            — our logger level (default INFO; e.g. DEBUG).
  * ``BROWSER_USE_LOGGING_LEVEL`` — browser-use's own verbosity (passed through).
  * ``LOG_DIR``             — where the rotating file lives (default workspace/logs).
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED = False
_FMT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"


def setup_logging() -> Path:
    """Configure console + rotating file handlers once. Returns the log file path."""
    global _CONFIGURED
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").strip().upper(), logging.INFO)

    log_dir = Path(os.getenv("LOG_DIR", "./workspace/logs")).expanduser()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "bot.log"

    if _CONFIGURED:
        return log_file

    fmt = logging.Formatter(_FMT)
    root = logging.getLogger()
    root.setLevel(level)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    # 5 MB per file, keep 5 backups → durable logs on any host.
    file_handler = RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # Our own logger honours LOG_LEVEL explicitly (root may be noisier/quieter).
    logging.getLogger("browseruse_bot").setLevel(level)

    _CONFIGURED = True
    logging.getLogger("browseruse_bot").info("Logging to %s (level=%s)", log_file, logging.getLevelName(level))
    return log_file

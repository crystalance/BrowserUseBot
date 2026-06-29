"""Platform config: load Telegram + LLM env (reuses the PersonalKnowledgeBase .env)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_KB_ENV = Path.home() / "Documents" / "PersonalKnowledgeBase" / ".env"
load_dotenv(_KB_ENV)
load_dotenv()  # local .env overrides

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")  # restrict to your chat

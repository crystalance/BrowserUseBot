"""M0 hello-task: drive a real browser and return the H1 from example.com.

Run from the repo root:  python -m examples.m0_hello
LLM: Azure OpenAI via `az login` (no key) if AZURE_OPENAI_ENDPOINT is set,
else Anthropic via ANTHROPIC_API_KEY.
"""

import asyncio
from pathlib import Path

from dotenv import load_dotenv

from browseruse_bot import BrowserAgentRunner

# Reuse the PersonalKnowledgeBase Azure config (az login, no API key)
_KB_ENV = Path.home() / "Documents" / "PersonalKnowledgeBase" / ".env"


async def main() -> None:
    load_dotenv(_KB_ENV)
    load_dotenv()  # local .env overrides, if present
    runner = BrowserAgentRunner(headless=False)
    result = await runner.run_task("Go to https://example.com and return the page's H1 heading.")
    print(f"\nok={result.ok} steps={result.steps}\nsummary: {result.summary}")


if __name__ == "__main__":
    asyncio.run(main())

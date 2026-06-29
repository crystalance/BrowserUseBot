"""M1 + M2 demo.

M1: a TaskPolicy allow-list — the agent may only visit example.com; if it
strays elsewhere the runner stops it.
M2: login handoff — when a login wall is detected, the runner emits
login_required and pauses; here we simulate the human pressing /done after a
short delay (the platform will wire this to Telegram).

Run:  python examples/m1_m2_demo.py
"""

import asyncio
from pathlib import Path

from dotenv import load_dotenv

from browseruse_bot import BrowserAgentRunner, TaskPolicy

_KB_ENV = Path.home() / "Documents" / "PersonalKnowledgeBase" / ".env"


async def main() -> None:
    load_dotenv(_KB_ENV)
    load_dotenv()

    runner = BrowserAgentRunner(headless=False)

    async def on_login_required(url: str) -> None:
        print(f"\n🔐 LOGIN NEEDED at {url} — open the remote view and log in.")
        # Platform would send a Telegram link; here we auto-resume after 20s.
        await asyncio.sleep(20)
        if runner.handoff:
            runner.handoff.signal_done()
        print("▶️  /done received, resuming.")

    runner._on_login_required = on_login_required

    policy = TaskPolicy(allowed_hosts=["example.com"])
    result = await runner.run_task(
        "Go to https://example.com and return the page's H1 heading.", policy
    )
    print(f"\nok={result.ok} steps={result.steps} needed_human={result.needed_human}")
    print(f"summary: {result.summary}")


if __name__ == "__main__":
    asyncio.run(main())

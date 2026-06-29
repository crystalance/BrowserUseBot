# BrowserUseBot — Computer-Use Core (M0)

A thin `BrowserAgentRunner` wrapping [browser-use](https://github.com/browser-use/browser-use)
for the supervised on-call agent. See `designDoc/` for the full design.

## Status
- **M0 — Hello task**: `run_task(goal) -> Result` driving a real browser with a
  persistent profile. ✅
- Next: M1 policy allow-list, M2 login handoff, M3 wire to Telegram.

## Setup (local)
```pwsh
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e .
pip install -e C:\src\browser-use   # local browser-use checkout
Copy-Item .env.example .env          # then add ANTHROPIC_API_KEY
python -m examples.m0_hello
```

The persistent Chrome profile lives in `./workspace/profile` so sessions/cookies
survive across runs. On AWS this same path lives on the persistent disk.

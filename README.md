# BrowserUseBot — Computer-Use Core (M0)

A thin `BrowserAgentRunner` wrapping [browser-use](https://github.com/browser-use/browser-use)
for the supervised on-call agent. See `designDoc/` for the full design.

## Status
- **M0** Hello task: `run_task(goal) -> Result` with persistent profile. ✅
- **M1** Policy allow-list (host enforcement). ✅
- **M2** Login handoff (pause → ask human → resume). ✅
- **M3** Telegram gateway (`/new`, `/done`, `/status`). ✅
- **M4** CDP-screencast remote view + Cloudflare tunnel link. ✅
- Next: M5 reliability/eval.

## Run the Telegram bot
```pwsh
.\.venv\Scripts\Activate.ps1
$env:TELEGRAM_BOT_TOKEN = "<from @BotFather>"   # or add to .env
python -m browseruse_bot.platform.telegram_gateway
```
Then message `/new go to example.com and return the H1`.

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

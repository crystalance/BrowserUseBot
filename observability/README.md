# Observability (Langfuse) — local dev

LLM tracing for the bot: every run becomes a **trace** with per-step
**generations** (model, tokens, latency, cost, errors). Runs locally via Docker
Compose — portable across Windows/macOS. Off by default; enable via env.

## Prerequisite
**Docker Desktop** (uses the WSL2 backend on Windows). Install once per machine.

## Start the stack
```bash
docker compose -f observability/docker-compose.yml up -d
# UI:    http://localhost:3000
# login: dev@local  /  langfuse-dev
```
Langfuse v2 = 2 containers (web + Postgres). It auto-provisions a project and
**fixed API keys** (`LANGFUSE_INIT_*`), so no manual dashboard setup is needed.

## Enable tracing in the bot
Uncomment in `.env` (keys already match the compose):
```
OBSERVABILITY=langfuse
LANGFUSE_HOST=http://localhost:3000
LANGFUSE_PUBLIC_KEY=pk-lf-dev-browseruse
LANGFUSE_SECRET_KEY=sk-lf-dev-browseruse
```
Install the SDK (once): `pip install "langfuse<3"` (also in
`pip install -e ".[observability]"`). Restart the bot, run a `/new` task, then
open the UI — you'll see the run trace with agent-step vs synthesizer cost/latency.

## Stop
```bash
docker compose -f observability/docker-compose.yml down        # keep data
docker compose -f observability/docker-compose.yml down -v     # wipe data
```

## Moving to another machine (portability)
1. Install Docker Desktop. 2. `git clone`. 3. `docker compose -f
observability/docker-compose.yml up -d`. 4. restore `.env`. Identical everywhere.

## Notes
- Tracing is **best-effort**: if the server is down or `OBSERVABILITY` is unset,
  the bot runs normally (no crash).
- Dev-only credentials — do not reuse in production. For a shared/cloud deploy,
  put Langfuse behind TLS + auth and rotate `NEXTAUTH_SECRET`/`SALT`/keys.
- The same instrumentation works against Langfuse Cloud or a remote self-hosted
  instance — only `LANGFUSE_HOST` + keys change.

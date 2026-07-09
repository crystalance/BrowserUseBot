# CI/CD — Push-to-Deploy for BrowserUseBot

How code gets from your laptop to the running bot on AWS, why it's set up this
way, and what it buys us.

## What we built

A **push-to-deploy** pipeline: committing to `main` on GitHub automatically
updates and restarts the bot on the EC2 server — no manual SSH, no editing on
the server.

```
local edit → git push → GitHub Actions → SSH into EC2 → pull + install + restart
```

### Pieces

| Piece | File / location | Role |
|-------|-----------------|------|
| Workflow | `.github/workflows/deploy.yml` | Triggers on push to `main` (or manual run); SSHes into EC2 and runs the deploy. |
| Deploy script | `scripts/deploy.sh` | On the server: fetch → `git reset --hard origin/main` → `pip install -e .` → `playwright install chromium` → restart service. |
| Service | `systemd` unit `browseruse` | Keeps the bot running 24/7, auto-restarts on crash/deploy. |
| Run script | `scripts/run-aws.sh` | The unit's entrypoint: starts `Xvfb :99` + WM, then the gateway with `HANDOFF=novnc`. |
| Line endings | `.gitattributes` | Pins `*.sh` to LF so scripts run on Linux. |

### The workflow (essentials)
```yaml
on:
  push: { branches: [main] }
  workflow_dispatch: {}          # manual "Run workflow" too
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.EC2_HOST }}
          username: ${{ secrets.EC2_USER }}
          key: ${{ secrets.EC2_SSH_KEY }}
          script: |
            cd ~/buBot/BrowserUseBot
            git fetch --all --tags --prune
            git reset --hard origin/main
            bash scripts/deploy.sh
```

### One-time setup that makes it work
- **GitHub repo secrets:** `EC2_HOST` (3.24.123.30), `EC2_USER` (ubuntu),
  `EC2_SSH_KEY` (a dedicated `~/.ssh/gha_deploy` private key whose public half is
  in the server's `authorized_keys`).
- **systemd service** `browseruse` running `scripts/run-aws.sh`.
- **Passwordless sudo** for just the restart:
  `ubuntu ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart browseruse`.

(Full commands are in `deployToAWS.md` §10.7.)

## Problems hit and fixed along the way
- `unexpected input 'script_stop'` → removed; `deploy.sh` uses `set -euo
  pipefail` so failures already propagate.
- `missing server host` → the `EC2_*` repo secrets weren't set yet.
- `ssh: no key found` → the full private key (incl. BEGIN/END lines) must be
  pasted into `EC2_SSH_KEY`.
- `./scripts/deploy.sh: No such file or directory` → the server's clone was
  stale (predated the script). Fixed by having the workflow `git reset --hard
  origin/main` **before** running the script, and invoking it via `bash`.
- CRLF risk on scripts → `.gitattributes` forces LF.

## Effect achieved
- **No more developing on the server.** Edit locally, push, done.
- **Deterministic deploys.** `git reset --hard origin/main` guarantees the box
  matches the repo exactly; `.env`, `workspace/`, and the browser profile are
  git-ignored and survive.
- **Easy rollback / versioning.** Deploy any tag (`bash scripts/deploy.sh
  v1.2.0`); revert by deploying the previous tag.
- **Self-healing runtime.** systemd restarts the bot on crash and on each deploy.
- **Reproducible env without Docker.** venv + pinned deps; Docker deferred until
  multi-box scale.

## How to use it
- **Normal:** `git push` to `main` → auto-deploys.
- **Manual:** GitHub → Actions → *Deploy to AWS* → *Run workflow*.
- **Specific version on the box:** `bash scripts/deploy.sh v1.2.0`.
- **Watch logs:** `journalctl -u browseruse -f` on the server.

## Dev/prod parity — "will my change work in production?"

Dev is Windows, prod is Linux (EC2), so a local change might behave differently
on the server. How we cope, without Docker:

### Trigger scope
Only pushes to **`main`** deploy (`on: push: branches: [main]`). Feature
branches don't deploy — merge to `main` when ready. `concurrency` cancels an
older in-flight deploy if you push again quickly.

### Testing locally without colliding with prod
The bot uses Telegram **long-polling**, and a token allows only **one** poller —
two at once → `409 Conflict`. So when testing on Windows:
- **Use a dedicated "dev" bot token** (chosen approach). Create a second bot via
  @BotFather and put its token in your **local `.env`**; the **server `.env`**
  keeps the prod token. Since `.env` is git-ignored and per-machine, local and
  prod never collide — no code change needed.
- (Alternative: `sudo systemctl stop browseruse` on the box while testing, then
  `start` again.)

### Defense layer 1 — test in a prod-like OS before deploying (IMPLEMENTED)
`.github/workflows/deploy.yml` has a **`test` job on `ubuntu-latest`** (same OS
family as prod) that installs the package and runs an import smoke test. The
`deploy` job has `needs: test`, so **a Linux failure blocks the deploy**. Grow
it into real `pytest` over time.

### Defense layer 2 — health check + auto-rollback (IMPLEMENTED)
`scripts/deploy.sh` records the current commit, deploys, restarts, then checks
`systemctl is-active browseruse`. If the new version fails to start, it
**rolls back to the previous commit**, reinstalls, and restarts — so a bad
deploy self-heals instead of leaving the bot dead.

### Defense layer 3 — shrink the gap (ongoing)
- **Pin dependencies** (commit a lockfile) so every env installs identically.
- **Guard OS-specific code** — already done (`sys.platform`-gated `--no-sandbox`,
  Windows vs Linux Chromium paths). Keep this pattern.

### How industry solves this (and the Docker question)
Common toolbox: **containers (Docker)** for identical dev/CI/prod images; **CI on
a prod-like OS**; **dependency lockfiles**; **dev containers / WSL** for a Linux
dev env on Windows; **staging environments**; **12-factor** config-via-env.

**Do we need Docker?** Not for a solo single-box project. It's *the* standard fix
for dev/prod parity and would eliminate the Windows-vs-Linux bugs we hit
(`--no-sandbox`, snap chromium, `chrome-linux64` path), but the login handoff
(Xvfb + noVNC + headful Chrome) makes containerizing more work. Trade-off:

| | Without Docker (current) | With Docker |
|---|---|---|
| Parity | Partial (code guards + CI Linux tests) | Full (identical image) |
| Setup | Low (working today) | Medium (X/noVNC in container is fiddly) |
| OS-drift bugs | Possible | Essentially gone |

**Decision:** stay Docker-free for now; the CI Linux test gate + health-check
rollback + lockfile cover the risk. If the OS gap keeps costing time, adopt a
**WSL2 dev environment** (Linux-on-Windows, lighter than full Docker) before
reaching for containers. Revisit Docker at multi-box / multi-service scale.

## Possible next improvements
- PR check workflow (lint / import smoke test) so broken code never reaches
  `main`.
- Move secrets to a GitHub *environment* with required reviewers for production.
- WSL2 dev environment for closer local↔prod parity.

#!/usr/bin/env bash
# Deploy the bot on the AWS box. Run by CI (GitHub Actions) or by hand:
#   ./scripts/deploy.sh            # deploy latest main
#   ./scripts/deploy.sh v1.2.0     # deploy a specific tag
#
# Forces the working tree to match the repo (deterministic deploys). Local edits
# on the server are discarded — always commit changes to the repo instead.
# .env / workspace / profile are git-ignored, so they survive.
set -euo pipefail

REF="${1:-origin/main}"
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PREV="$(git rev-parse HEAD)"   # remember current commit for rollback

echo "==> Fetching…"
git fetch --all --tags --prune

# Normalise a bare tag/branch name to something checkout-able.
if git rev-parse -q --verify "origin/$REF" >/dev/null; then
  REF="origin/$REF"
fi
echo "==> Checking out $REF"
git reset --hard "$REF"

install_and_restart() {
  if [ -x .venv/bin/pip ]; then
    .venv/bin/pip install -e . -q
    .venv/bin/python -m playwright install chromium >/dev/null
  else
    echo "!! .venv missing; run: python3 -m venv .venv && .venv/bin/pip install -e . playwright" >&2
    exit 1
  fi
  sudo systemctl restart browseruse
}

echo "==> Installing deps + restarting"
install_and_restart

# Health check: confirm the service actually came up; else roll back.
sleep 5
if systemctl is-active --quiet browseruse; then
  echo "deployed $(git describe --tags --always)"
else
  echo "!! service failed to start — rolling back to $PREV" >&2
  git reset --hard "$PREV"
  install_and_restart
  sleep 5
  systemctl is-active --quiet browseruse \
    && echo "rolled back to $(git describe --tags --always)" \
    || echo "!! rollback also failed — check: journalctl -u browseruse -n 50" >&2
  exit 1
fi

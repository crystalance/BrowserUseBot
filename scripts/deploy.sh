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

echo "==> Fetching…"
git fetch --all --tags --prune

# Normalise a bare tag/branch name to something checkout-able.
if git rev-parse -q --verify "origin/$REF" >/dev/null; then
  REF="origin/$REF"
fi
echo "==> Checking out $REF"
git reset --hard "$REF"

echo "==> Installing deps"
if [ -x .venv/bin/pip ]; then
  .venv/bin/pip install -e . -q
  .venv/bin/python -m playwright install chromium >/dev/null
else
  echo "!! .venv missing; run: python3 -m venv .venv && .venv/bin/pip install -e . playwright" >&2
  exit 1
fi

echo "==> Restarting service"
sudo systemctl restart browseruse

echo "deployed $(git describe --tags --always)"

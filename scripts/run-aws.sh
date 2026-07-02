#!/usr/bin/env bash
# Launch the bot on AWS with an X display + noVNC login handoff.
#
# Starts a persistent Xvfb (so the agent's headful Chromium renders on it), a
# minimal WM, then runs the Telegram gateway with HANDOFF=novnc. When a login
# wall is hit, NoVncView exposes THIS display so you drive the agent's own
# browser from your phone; the profile persists so logins are rare.
#
#   PUBLIC_HOST=<EC2-public-IP> ./scripts/run-aws.sh
#
# Requires (once):  sudo apt install -y xvfb x11vnc novnc websockify fluxbox
#                   playwright install --with-deps chromium
set -euo pipefail

DISPLAY_NUM="${DISPLAY_NUM:-99}"
SCREEN_W="${SCREEN_W:-800}"
SCREEN_H="${SCREEN_H:-1280}"
export DISPLAY=":$DISPLAY_NUM"
export HANDOFF=novnc

if [ -z "${PUBLIC_HOST:-}" ]; then
  PUBLIC_HOST="$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4 || true)"
  export PUBLIC_HOST
fi
echo "PUBLIC_HOST=$PUBLIC_HOST  DISPLAY=$DISPLAY"

# Start Xvfb + WM if not already running on this display.
if ! pgrep -f "Xvfb :$DISPLAY_NUM" >/dev/null; then
  echo "==> Starting Xvfb :$DISPLAY_NUM (${SCREEN_W}x${SCREEN_H})"
  Xvfb ":$DISPLAY_NUM" -screen 0 "${SCREEN_W}x${SCREEN_H}x24" >/tmp/xvfb.log 2>&1 &
  sleep 1
  fluxbox >/tmp/fluxbox.log 2>&1 &
  sleep 1
fi

echo "==> Starting bot (HANDOFF=novnc)"
exec python -m browseruse_bot.platform.telegram_gateway

#!/usr/bin/env bash
# Manual noVNC smoke test for the browser-agent login handoff.
# Run on the AWS Ubuntu box. Opens a Chromium on a virtual display and exposes
# it via noVNC on port 8770, so you can view+control it from your phone at:
#     http://<EC2-PUBLIC-IP>:8770/vnc.html
#
# Prereq: security-group inbound rule -> Custom TCP 8770 from your IP/0.0.0.0.
# Tear down with: ./setup-novnc.sh stop
set -euo pipefail

DISPLAY_NUM=99
VNC_PORT=5900
WEB_PORT=8770
URL="${TEST_URL:-https://www.xiaohongshu.com/}"
NOVNC_DIR="/usr/share/novnc"
# Portrait geometry that matches a phone screen so scaling wastes little space.
SCREEN_W="${SCREEN_W:-800}"
SCREEN_H="${SCREEN_H:-1280}"

start() {
  echo "==> Installing packages (first run only)…"
  export DEBIAN_FRONTEND=noninteractive
  sudo -E apt-get update -y
  sudo -E apt-get install -y xvfb x11vnc novnc websockify fluxbox curl wget

  # Chromium on Ubuntu is a snap (often unreachable on servers). Use the
  # Google Chrome .deb instead — a real package, no snap store needed.
  if ! command -v google-chrome >/dev/null 2>&1 \
     && ! command -v chromium-browser >/dev/null 2>&1 \
     && ! command -v chromium >/dev/null 2>&1; then
    echo "==> Installing Google Chrome (.deb, no snap)"
    wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -O /tmp/chrome.deb
    sudo -E apt-get install -y /tmp/chrome.deb
  fi

  # Pick whatever chrome/chromium binary exists.
  CHROME_BIN="$(command -v google-chrome || command -v chromium-browser || command -v chromium || true)"
  if [ -z "$CHROME_BIN" ]; then
    echo "!! No chrome/chromium found. Install one and re-run." >&2
    exit 1
  fi

  echo "==> Starting Xvfb on :$DISPLAY_NUM (${SCREEN_W}x${SCREEN_H})"
  Xvfb ":$DISPLAY_NUM" -screen 0 "${SCREEN_W}x${SCREEN_H}x24" >/tmp/xvfb.log 2>&1 &
  sleep 1
  export DISPLAY=":$DISPLAY_NUM"

  echo "==> Starting fluxbox window manager"
  fluxbox >/tmp/fluxbox.log 2>&1 &
  sleep 1

  echo "==> Launching browser (fullscreen) -> $URL"
  "$CHROME_BIN" --no-sandbox --no-first-run --disable-gpu \
    --start-fullscreen --kiosk --window-position=0,0 \
    --window-size="${SCREEN_W},${SCREEN_H}" "$URL" >/tmp/chromium.log 2>&1 &
  sleep 2

  echo "==> Starting x11vnc on :$VNC_PORT"
  x11vnc -display ":$DISPLAY_NUM" -rfbport "$VNC_PORT" -nopw -forever -shared \
    >/tmp/x11vnc.log 2>&1 &
  sleep 1

  echo "==> Starting websockify/noVNC on 0.0.0.0:$WEB_PORT"
  websockify --web="$NOVNC_DIR" "0.0.0.0:$WEB_PORT" "localhost:$VNC_PORT" \
    >/tmp/websockify.log 2>&1 &
  sleep 1

  IP="$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4 || echo '<EC2-PUBLIC-IP>')"
  # Mobile-friendly noVNC params: auto-connect, scale to fit, auto-reconnect,
  # and show a cursor dot for touch precision.
  PARAMS="autoconnect=true&resize=scale&reconnect=true&show_dot=true"
  echo
  echo "======================================================================"
  echo " Open on your phone:  http://$IP:$WEB_PORT/vnc.html?$PARAMS"
  echo " (auto-connects, scales to your screen; tap the keyboard icon to type)"
  echo "======================================================================"
}

stop() {
  echo "==> Stopping noVNC test stack"
  pkill -f websockify || true
  pkill -f x11vnc || true
  pkill -f chromium || true
  pkill -f chrome || true
  pkill -f fluxbox || true
  pkill -f "Xvfb :$DISPLAY_NUM" || true
  echo "done."
}

case "${1:-start}" in
  start) start ;;
  stop)  stop ;;
  *) echo "usage: $0 [start|stop]"; exit 1 ;;
esac

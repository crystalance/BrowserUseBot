# Deploy BrowserUseBot to AWS

Runbook for running the supervised browser-agent bot on a persistent AWS host,
reachable from China. The key win: serving the remote-view login link directly
from the EC2 public IP bypasses `*.trycloudflare.com` DNS pollution on China
mobile networks.

## 1. Pick the right region & instance
- **Region:** `ap-southeast-1` (Singapore) or `ap-northeast-1` (Tokyo) — low
  latency from China and reachable without ICP. **Do not** use AWS China
  (Beijing/Ningxia); those need an ICP license.
- **Instance:** `t3.small` (2 vCPU / 2 GB) minimum — Chromium + Python need
  headroom. `t3.medium` is comfier.
- **OS:** Ubuntu 24.04 LTS.
- **Storage:** 20 GB gp3.

## 2. Security group (this is what makes the login link work)
Inbound rules:

| Port | Source | Purpose |
|------|--------|---------|
| 22 | your IP only | SSH |
| 8770 | your phone's IP (or 0.0.0.0/0 if dynamic) | remote-view login link |

Outbound: allow all (Telegram polling + Azure + websites).

The login link becomes `http://<EC2-public-IP>:8770` — no tunnel, no
trycloudflare, so China DNS pollution is bypassed entirely.

## 3. Base setup on the box
```bash
sudo apt update && sudo apt install -y python3.12 python3.12-venv git
git clone <your-repo> BrowserUseBot && cd BrowserUseBot
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e .
```

## 4. Browser + headless display
```bash
python -m playwright install --with-deps chromium   # Chromium + all system libs
```
Run the agent **headless** on the server (CDP screencast still works, so the
remote-view login flow is unaffected). If a site blocks headless, install a
virtual display instead:
```bash
sudo apt install -y xvfb
# then run under: xvfb-run -a python -m browseruse_bot.platform.telegram_gateway
```

## 4b. Login handoff on AWS — use noVNC, not the CDP screencast

The current `remote_view.py` (CDP screencast) only forwards single ASCII
keystrokes + clicks. That is too primitive for real logins:

| Login method | CDP screencast | noVNC desktop |
|--------------|----------------|----------------|
| QR scan (小红书) | ❌ nothing to "type" | ✅ shows QR, scan with phone app |
| Username + password | ⚠️ ASCII only, no paste/IME | ✅ full keyboard + clipboard |
| Chinese IME | ❌ | ✅ |
| SMS / 2FA / captcha | ❌ | ✅ |

So for AWS the login link should open a **real interactive remote desktop**
(noVNC over the Xvfb display), which handles *every* login method exactly like
sitting at the machine.

### Resource cost on 2 vCPU / 4 GB — fine
noVNC itself is cheap (~150 MB, light CPU only while you're actively viewing).
The browser is the real cost and you pay that anyway. Run the VNC stack
**on-demand** (start at a login handoff, stop on `/done`) so steady-state waste
≈ 0. Combined with the persistent profile, logins are rare (once per site), so
noVNC is active only a few minutes per month.

### Setup
```bash
sudo apt install -y xvfb x11vnc novnc websockify fluxbox
# launch order (on-demand, when a login handoff starts):
#   Xvfb :99 -screen 0 1280x800x24 &
#   DISPLAY=:99 fluxbox &              # minimal WM
#   DISPLAY=:99 <run the agent so Chromium renders here>
#   x11vnc -display :99 -rfbport 5900 -nopw -forever &
#   websockify --web=/usr/share/novnc 8770 localhost:5900 &
# phone opens: http://<EC2-public-IP>:8770/vnc.html?path=...&token=<secret>
```
The bot should template the `http://<PUBLIC_HOST>:8770/...<token>` URL and send
it over Telegram, same as today — only the server side changes from screencast
to noVNC.

### Lighter alternative: QR-relay (small sites / 小红书 only)
If you only ever log into QR sites, skip noVNC: grab the QR `<img>` from the page
and send the **image** to Telegram; you scan it with your phone. ~0 extra RAM,
but fragile (QR rotation, follow-up captchas) and QR-only. noVNC is the general
answer.

## 5. Azure auth without interactive `az login`
> **Simpler now:** if you use an **OpenAI API key** (`LLM_PROVIDER=openai`,
> `OPENAI_API_KEY=...`), it works on the server directly — skip this whole
> section and the Azure service principal. The Azure path below is only needed
> if you specifically want Azure OpenAI.

On a server there's no interactive `az login`, so `AzureCliCredential` won't
work. Create an **Azure AD service principal** and switch to client-secret auth:
```bash
# on your workstation, once:
az ad sp create-for-rbac --name browseruse-bot
# note appId, password, tenant; grant it access to your Azure OpenAI resource
```
Then set these in the server `.env`:
```
AZURE_CLIENT_ID=...
AZURE_TENANT_ID=...
AZURE_CLIENT_SECRET=...
```
`build_llm()` needs a one-line swap from `AzureCliCredential()` to
`DefaultAzureCredential()` (which picks up those env vars automatically).

## 6. Config + persistent profile
Server `.env`:
```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
# --- LLM: OpenAI is simplest on a server ---
LLM_PROVIDER=openai
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.4
USE_VISION=true
# --- Login handoff (AWS) ---
HANDOFF=novnc                  # use noVNC remote desktop, not the CDP screencast
PUBLIC_HOST=<EC2-public-IP>    # builds the http://<ip>:8770/vnc.html login link
BROWSER_EXECUTABLE_PATH=       # optional; leave empty to let Playwright manage
```
The persistent profile is already handled in code (the profile dir is named
`workspace/browser-use-user-data-dir-main`, which stops browser-use from copying
it to a temp dir), so logins survive across tasks on the EC2 disk.

## 7. Code is AWS-ready
The handoff, LLM, and profile pieces are implemented:
- **LLM:** `LLM_PROVIDER=openai` + `OPENAI_API_KEY` (no Azure service principal).
- **Login handoff:** `HANDOFF=novnc` → `NoVncView` exposes the agent's X display
  via x11vnc + websockify on `0.0.0.0:8770`; the link is
  `http://$PUBLIC_HOST:8770/vnc.html?autoconnect=true&resize=scale&...` (no
  tunnel needed on a public IP). You drive the agent's *own* browser, so any
  login method works (QR/password/IME/2FA).
- **Persistent profile:** automatic (see §6).

## 8. Run 24/7 with systemd
Use `scripts/run-aws.sh` — it starts `Xvfb :99` + a WM (so the headful Chromium
renders on the display noVNC exposes) and launches the gateway with
`HANDOFF=novnc`.

`/etc/systemd/system/browseruse.service`:
```ini
[Unit]
Description=BrowserUse Telegram Bot
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/BrowserUseBot
ExecStart=/home/ubuntu/BrowserUseBot/.venv/bin/bash scripts/run-aws.sh
Restart=always
RestartSec=5
EnvironmentFile=/home/ubuntu/BrowserUseBot/.env

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload && sudo systemctl enable --now browseruse
journalctl -u browseruse -f   # live logs
```

## 9. Security notes
- Restrict port 8770 to your phone's IP if possible; the remote-view exposes a
  live browser. Consider adding a random token in the URL path before going wide.
- Keep `TELEGRAM_CHAT_ID` set so only your chat can drive the bot.
- The service-principal secret lives only in the server `.env` (chmod 600).

## 10. Deployment workflow (git, versions, venv, CI/CD)

For one small bot on a single box, the elegant setup is **git tags + uv +
systemd** — no Docker needed.

### 10.1 Pull a private repo with a deploy key
Don't put personal credentials on the server. Use a read-only **deploy key**:
```bash
# on the EC2 box, once:
ssh-keygen -t ed25519 -f ~/.ssh/deploy_key -N ""
cat ~/.ssh/deploy_key.pub
# → GitHub repo → Settings → Deploy keys → Add (read-only)
git clone git@github.com:<you>/BrowserUseBot.git
```

### 10.2 Change versions / rollback with tags
```bash
git fetch --tags
git checkout v1.2.0     # deploy a specific version
git checkout main       # latest
# rollback = check out the previous tag and restart the service
```

### 10.3 Virtual env: use `uv` (reproducible, no Docker)
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv
uv sync                 # installs exact versions from uv.lock
uv run python -m browseruse_bot.platform.telegram_gateway
```
Commit a **`uv.lock`** so the server installs exactly what you tested — this is
the "reproducible env without a container" benefit.

### 10.4 Why not Docker (yet)
The login handoff needs **Xvfb + x11vnc + noVNC + headful Chromium**, which is
fiddly to run in a container (X passthrough, sandbox flags, image size). On a
single box, `uv` + `systemd` already give reproducibility and clean rollback.
Revisit Docker only when running multiple bots or moving to a cluster.

### 10.5 One-command deploy script
`scripts/deploy.sh`:
```bash
set -euo pipefail
cd ~/BrowserUseBot
git fetch --all --tags
git checkout "${1:-main}"          # ./deploy.sh v1.2.0   OR   ./deploy.sh
git pull --ff-only || true
uv sync
uv run playwright install chromium
sudo systemctl restart browseruse
echo "deployed $(git describe --tags --always)"
```

### 10.6 CI/CD: push-to-deploy with GitHub Actions (optional, later)
```yaml
# .github/workflows/deploy.yml
on: { push: { branches: [main] } }
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.EC2_HOST }}
          username: ubuntu
          key: ${{ secrets.EC2_SSH_KEY }}
          script: cd ~/BrowserUseBot && ./scripts/deploy.sh
```
Store `EC2_HOST` + an SSH key as GitHub repo **secrets**. Push to `main` →
auto-deploy.

### Recommended order
1. **Now:** deploy key + `uv sync` + systemd + `scripts/deploy.sh` (manual).
2. **When stable:** add the GitHub Actions workflow for push-to-deploy.
3. **Docker:** defer until you outgrow one box.

---

The code is already AWS-ready (LLM via OpenAI key, `HANDOFF=novnc` login handoff,
persistent profile). Follow steps 1–10 on the box to go live.


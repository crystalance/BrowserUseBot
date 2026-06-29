"""M3 — Telegram gateway wiring the bot to the BrowserAgentRunner.

Commands:
  /new <goal>   start an ad-hoc task
  /done         signal a login handoff is complete
  /status       current task status
  /start        show chat id + help

On a login wall the runner emits login_required → we message you the remote-view
link (placeholder until M4's CDP screencast); /done resumes the agent.
One task at a time (concurrency guard).
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from browseruse_bot import BrowserAgentRunner, TaskPolicy
from browseruse_bot.platform.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from browseruse_bot.platform.remote_view import RemoteView
from browseruse_bot.platform.tunnel import Tunnel

logger = logging.getLogger("browseruse_bot")


class TelegramGateway:
    def __init__(self) -> None:
        self.runner = BrowserAgentRunner(headless=False, on_login_required=self._login_required)
        self._busy = False
        self._chat_id: str | None = TELEGRAM_CHAT_ID or None
        self._app: Application | None = None
        self._view: RemoteView | None = None
        self._tunnel: Tunnel | None = None

    def _authorized(self, update: Update) -> bool:
        if not TELEGRAM_CHAT_ID:
            return True
        return str(update.effective_chat.id) == str(TELEGRAM_CHAT_ID)

    async def _login_required(self, url: str, agent: object) -> None:
        cdp_http = getattr(agent.browser_session, "cdp_url", None)  # type: ignore[attr-defined]
        port = 8770
        self._view = RemoteView(cdp_http, port) if cdp_http else None
        link = await self._view.start() if self._view else "(remote view unavailable)"
        if self._view:
            self._tunnel = Tunnel(port)
            link = await self._tunnel.open()
        if self._app and self._chat_id:
            await self._app.bot.send_message(
                self._chat_id,
                f"🔐 Login needed at {url}\nOpen: {link}\nReply /done when finished.",
            )

    async def cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        self._chat_id = str(update.effective_chat.id)
        await update.message.reply_text(
            f"👋 Browser agent ready. Chat ID: {self._chat_id}\n"
            "/new <goal> · /done · /status"
        )

    async def cmd_new(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        if self._busy:
            await update.message.reply_text("⏳ Busy with a task — try again later.")
            return
        goal = " ".join(ctx.args) if ctx.args else ""
        if not goal:
            await update.message.reply_text("Usage: /new <task description>")
            return
        self._busy = True
        self._chat_id = str(update.effective_chat.id)
        await update.message.reply_text(f"▶️ Running: {goal}")
        try:
            result = await self.runner.run_task(goal, TaskPolicy())
            await update.message.reply_text(
                f"{'✅' if result.ok else '⚠️'} {result.summary[:3500]}"
            )
        except Exception as e:  # noqa: BLE001
            await update.message.reply_text(f"❌ task failed: {e}")
        finally:
            self._busy = False

    async def cmd_done(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        if self.runner.handoff:
            self.runner.handoff.signal_done()
            await update.message.reply_text("▶️ Resuming.")
        else:
            await update.message.reply_text("Nothing waiting.")
        if self._tunnel:
            await self._tunnel.close()
            self._tunnel = None
        if self._view:
            await self._view.stop()
            self._view = None

    async def cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("busy" if self._busy else "idle")

    async def cmd_stats(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        m = self.runner.store.metrics()
        await update.message.reply_text(
            f"📊 runs={m.total} ok={m.success} ({m.success_rate:.0%}) "
            f"handoffs={m.handoffs} avg={m.avg_latency_s}s"
        )

    def run(self) -> None:
        self._app = Application.builder().token(TELEGRAM_BOT_TOKEN).concurrent_updates(True).build()
        self._app.add_handler(CommandHandler("start", self.cmd_start))
        self._app.add_handler(CommandHandler("new", self.cmd_new))
        self._app.add_handler(CommandHandler("done", self.cmd_done))
        self._app.add_handler(CommandHandler("status", self.cmd_status))
        self._app.add_handler(CommandHandler("stats", self.cmd_stats))
        logger.info("Telegram gateway polling…")
        self._app.run_polling()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN in the environment/.env")
    TelegramGateway().run()


if __name__ == "__main__":
    main()

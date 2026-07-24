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
import os

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from browseruse_bot import BrowserAgentRunner, TaskPolicy
from browseruse_bot.core.chat import classify_message
from browseruse_bot.core.harvest import load_companies, parse_harvest_request
from browseruse_bot.core.ids import new_request_id
from browseruse_bot.core.ledger import harvest_base
from browseruse_bot.core.logging_setup import setup_logging
from browseruse_bot.core.observability import trace_url
from browseruse_bot.core.probe import load_probes, run_probe_suite
from browseruse_bot.core.router import route_request
from browseruse_bot.core.skills import SkillStore
from browseruse_bot.platform.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from browseruse_bot.platform.novnc_view import NoVncView
from browseruse_bot.platform.remote_view import RemoteView
from browseruse_bot.platform.tunnel import Tunnel

logger = logging.getLogger("browseruse_bot")


class TelegramGateway:
    def __init__(self) -> None:
        use_vision = os.getenv("USE_VISION", "").strip().lower() in ("1", "true", "yes")
        self.runner = BrowserAgentRunner(
            headless=False,
            use_vision=use_vision,
            max_steps=int(os.getenv("MAX_STEPS", "40")),
            on_login_required=self._login_required,
        )
        self._busy = False
        self._chat_id: str | None = TELEGRAM_CHAT_ID or None
        self._app: Application | None = None
        self._view: RemoteView | None = None
        self._novnc: NoVncView | None = None
        self._tunnel: Tunnel | None = None
        self.skills = SkillStore()
        self.companies = load_companies()

    def _authorized(self, update: Update) -> bool:
        if not TELEGRAM_CHAT_ID:
            return True
        return str(update.effective_chat.id) == str(TELEGRAM_CHAT_ID)

    async def _login_required(self, url: str, agent: object) -> None:
        # HANDOFF=novnc: expose the agent's X display (AWS/Linux, public IP).
        if os.getenv("HANDOFF", "").strip().lower() == "novnc":
            self._novnc = NoVncView(port=8770)
            link = await self._novnc.start()
        else:
            # Default: CDP screencast of the current tab, published via a tunnel.
            cdp_http = getattr(agent.browser_session, "cdp_url", None)  # type: ignore[attr-defined]
            port = 8770
            self._view = RemoteView(cdp_http, port) if cdp_http else None
            link = await self._view.start() if self._view else "(remote view unavailable)"
            if self._view:
                self._tunnel = Tunnel(port)
                link = await self._tunnel.open()
        logger.info("🔗 login handoff link: %s", link)
        if self._app and self._chat_id:
            await self._app.bot.send_message(
                self._chat_id,
                f"🔐 Login needed at {url}\nOpen: {link}\nReply /done when finished.",
            )

    async def cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        self._chat_id = str(update.effective_chat.id)
        await update.message.reply_text(
            f"👋 Browser agent ready. Chat ID: {self._chat_id}\n"
            "Tasks run ONLY via /new <goal>. Plain messages are just chat.\n"
            "/new <goal> · /done · /stop · /status · /stats · /probe [tier]\n"
            "/skills · /skill add <name> <instructions> · /vision on|off · or send a .md file"
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

        # A request id ties this task's Telegram message → Langfuse trace → SQLite
        # row → transcript. Echo it (and a trace link, if resolvable) up front.
        request_id = new_request_id()
        link = trace_url(request_id)
        header = f"🆔 {request_id}"
        if link:
            header += f"\n🔎 trace: {link}"

        # Route A — LLM intent router: is this a company 面经 harvest? It reads the
        # request and extracts company/source/scope (robust to phrasing). Falls back
        # to keyword parsing only if the router errors.
        try:
            spec = await route_request(goal, self.companies, llm=self.runner._llm)
        except Exception as e:  # noqa: BLE001
            logger.warning("intent router failed, using keyword fallback: %s", e)
            spec = parse_harvest_request(goal, self.companies)
        if spec:
            company = spec.company
            scope = spec.scope_raw or "all"
            await update.message.reply_text(
                f"{header}\n🏢 {company.name} 面经 · scope: {scope} · source: {spec.source} "
                f"(target {spec.target})…"
            )

            async def _progress(chunk: int, saved: int, pending: int) -> None:
                await update.message.reply_text(
                    f"📈 chunk {chunk} · saved {saved}/{spec.target} · pending {pending}"
                )

            try:
                result = await self.runner.run_harvest(
                    spec, request_id=request_id, on_progress=_progress
                )
                await update.message.reply_text(
                    f"{'✅' if result.ok else '⚠️'} {request_id}\n{result.summary[:3500]}"
                )
                # Send the collected 面经.md back as a downloadable file.
                md_path = harvest_base() / company.key / spec.scope_slug / "面经.md"
                if md_path.exists() and md_path.stat().st_size > 0:
                    try:
                        with md_path.open("rb") as f:
                            await update.message.reply_document(
                                document=f,
                                filename=f"{company.key}-{spec.scope_slug}-面经.md",
                                caption=f"{company.name} 面经 · {scope}",
                            )
                    except Exception as e:  # noqa: BLE001
                        logger.warning("could not send harvest file: %s", e)
            except Exception as e:  # noqa: BLE001
                await update.message.reply_text(f"❌ {request_id} harvest failed: {e}")
            finally:
                self._busy = False
            return

        # Route B — auto-match a skill and steer the task with its instructions + policy.
        skill = self.skills.match(goal)
        policy = TaskPolicy()
        task = goal
        if skill:
            task = skill.build_task(goal)
            if skill.allowed_hosts:
                policy.allowed_hosts = skill.allowed_hosts
            if skill.login_hosts:
                policy.login_hosts = skill.login_hosts
            await update.message.reply_text(f"{header}\n🧩 Using skill: {skill.name}\n▶️ Running: {goal}")
        else:
            await update.message.reply_text(f"{header}\n▶️ Running: {goal}")
        try:
            result = await self.runner.run_task(task, policy, request_id=request_id)
            await update.message.reply_text(
                f"{'✅' if result.ok else '⚠️'} {request_id}\n{result.summary[:3500]}"
            )
        except Exception as e:  # noqa: BLE001
            await update.message.reply_text(f"❌ {request_id} task failed: {e}")
        finally:
            self._busy = False

    async def _teardown_handoff(self) -> None:
        """Stop whichever handoff backend is active (tunnel/view or noVNC)."""
        if self._tunnel:
            await self._tunnel.close()
            self._tunnel = None
        if self._view:
            await self._view.stop()
            self._view = None
        if self._novnc:
            await self._novnc.stop()
            self._novnc = None

    async def cmd_done(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        if self.runner.handoff:
            self.runner.handoff.signal_done()
            await update.message.reply_text("▶️ Resuming.")
        else:
            await update.message.reply_text("Nothing waiting.")
        await self._teardown_handoff()

    async def cmd_stop(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        if not self._busy:
            await update.message.reply_text("Nothing running.")
            return
        self.runner.stop()
        await update.message.reply_text("🛑 Stopping current task…")
        await self._teardown_handoff()

    async def cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("busy" if self._busy else "idle")

    async def cmd_probe(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Run the batch capability suite (tests/batch/probes.jsonl) SEQUENTIALLY.

        Login walls during any probe reuse the normal handoff: you get a noVNC
        link and reply /done to resume. Optional arg filters to one tier.
        """
        if not self._authorized(update):
            return
        if self._busy:
            await update.message.reply_text("⏳ Busy with a task — try again later.")
            return
        tier: int | None = None
        if ctx.args:
            try:
                tier = int(ctx.args[0])
            except ValueError:
                tier = None
        try:
            probes = load_probes(tier=tier)
        except Exception as e:  # noqa: BLE001
            await update.message.reply_text(f"❌ Could not load probes: {e}")
            return
        if not probes:
            await update.message.reply_text(
                f"No probes match{f' tier {tier}' if tier is not None else ''}."
            )
            return
        self._busy = True
        self._chat_id = str(update.effective_chat.id)
        scope = f" (tier {tier})" if tier is not None else ""
        await update.message.reply_text(f"🧪 Running {len(probes)} probes{scope} sequentially…")

        async def _event(msg: str) -> None:
            await update.message.reply_text(msg)

        try:
            report = await run_probe_suite(self.runner, probes=probes, on_event=_event)
            await update.message.reply_text(f"🏁 {report.summary_line}")
            if report.summary_path and report.summary_path.exists():
                with report.summary_path.open("rb") as f:
                    await update.message.reply_document(
                        document=f, filename="probe-summary.md", caption=report.summary_line
                    )
        except Exception as e:  # noqa: BLE001
            await update.message.reply_text(f"❌ probe suite failed: {e}")
        finally:
            self._busy = False

    async def cmd_vision(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        arg = (ctx.args[0].lower() if ctx.args else "")
        if arg in ("on", "off"):
            self.runner.set_vision(arg == "on")
            await update.message.reply_text(
                f"👁 Vision {'ON' if self.runner.use_vision else 'OFF'} (applies to next task)."
            )
        else:
            state = "ON" if self.runner.use_vision else "OFF"
            await update.message.reply_text(f"👁 Vision is {state}. Usage: /vision on|off")

    async def cmd_stats(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        m = self.runner.store.metrics()
        await update.message.reply_text(
            f"📊 runs={m.total} ok={m.success} ({m.success_rate:.0%}) "
            f"handoffs={m.handoffs} avg={m.avg_latency_s}s"
        )

    async def cmd_skills(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        self.skills.reload()
        skills = self.skills.list()
        if not skills:
            await update.message.reply_text(
                "No skills yet. Add one with /skill add <name> <instructions>, "
                "send me a .md file, or drop files in the skills/ folder."
            )
            return
        lines = [f"• {s.name} — {s.description or 'no description'}" for s in skills]
        await update.message.reply_text("🧩 Skills:\n" + "\n".join(lines))

    async def cmd_skill(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        args = ctx.args or []
        if len(args) >= 3 and args[0].lower() == "add":
            name = args[1]
            body = " ".join(args[2:])
            skill = self.skills.add(name, body)
            await update.message.reply_text(f"✅ Saved skill: {skill.name}")
        else:
            await update.message.reply_text(
                "Usage: /skill add <name> <instructions…>\n"
                "Or send me a .md file, or use /skills to list."
            )

    async def on_text(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Plain (non-command) message = chat. Never runs a task.

        The LLM decides: small talk → answer it; an implied task → do NOT run it,
        nudge the user to resend as /new <goal>.
        """
        if not self._authorized(update):
            return
        text = (update.message.text or "").strip()
        if not text:
            return
        try:
            decision = await classify_message(text, llm=self.runner._llm)
        except Exception as e:  # noqa: BLE001
            logger.warning("chat classify failed: %s", e)
            await update.message.reply_text("(couldn't parse that — to run a task use /new <goal>)")
            return
        if (decision.intent or "").strip().lower() == "task":
            cmd = decision.suggested_command.strip() or f"/new {text}"
            await update.message.reply_text(
                "💡 Sounds like a task. I only run tasks via a command — send:\n" + cmd
            )
        else:
            await update.message.reply_text(decision.reply.strip() or "🙂")

    async def on_document(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        doc = update.message.document
        if not doc or not doc.file_name.endswith((".md", ".txt")):
            return
        f = await doc.get_file()
        content = bytes(await f.download_as_bytearray())
        self.skills.save_file(doc.file_name, content)
        await update.message.reply_text(f"✅ Imported skill file: {doc.file_name}")

    def run(self) -> None:
        self._app = Application.builder().token(TELEGRAM_BOT_TOKEN).concurrent_updates(True).build()
        self._app.add_handler(CommandHandler("start", self.cmd_start))
        self._app.add_handler(CommandHandler("new", self.cmd_new))
        self._app.add_handler(CommandHandler("done", self.cmd_done))
        self._app.add_handler(CommandHandler("stop", self.cmd_stop))
        self._app.add_handler(CommandHandler("status", self.cmd_status))
        self._app.add_handler(CommandHandler("probe", self.cmd_probe))
        self._app.add_handler(CommandHandler("vision", self.cmd_vision))
        self._app.add_handler(CommandHandler("stats", self.cmd_stats))
        self._app.add_handler(CommandHandler("skills", self.cmd_skills))
        self._app.add_handler(CommandHandler("skill", self.cmd_skill))
        self._app.add_handler(MessageHandler(filters.Document.ALL, self.on_document))
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
        logger.info("Telegram gateway polling…")
        self._app.run_polling()


def main() -> None:
    setup_logging()
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN in the environment/.env")
    TelegramGateway().run()


if __name__ == "__main__":
    main()

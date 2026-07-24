"""Front-desk intent classifier: chat vs. task.

The gateway only *runs* browser work when the user sends a ``/new`` command. A
plain text message is treated as conversation. This module asks the LLM whether a
plain message is (a) small talk to answer directly, or (b) an implied task — in
which case we do NOT run it, we nudge the user to resend it as ``/new <goal>``.
"""

from __future__ import annotations

from browser_use.llm.messages import SystemMessage, UserMessage
from pydantic import BaseModel, Field

from browseruse_bot.core.llm import build_llm


class ChatDecision(BaseModel):
    """What the front desk decides to do with a plain (non-command) message."""

    intent: str = Field(
        description="'task' if the user wants the browser agent to DO something "
        "(collect 面经, open/search a site, log in, fill a form, buy, summarize an "
        "account/inbox, etc.); otherwise 'chat' (greetings, questions about the bot, "
        "small talk, thanks, help)."
    )
    reply: str = Field(
        default="",
        description="a short, friendly conversational reply in the user's language "
        "IF intent=='chat'; empty when intent=='task'.",
    )
    suggested_command: str = Field(
        default="",
        description="IF intent=='task', the exact command to run it, preserving the "
        "user's language, e.g. '/new 收集 Amazon 的 SDE 面经'; empty when intent=='chat'.",
    )


_SYS = (
    "You are the front desk of a supervised browser-automation assistant. The assistant "
    "ONLY performs browser tasks when the user sends a /new command; plain messages are "
    "conversation. Read the user's plain message and decide:\n"
    "- intent='task': the user is asking the agent to DO a browser action (collect "
    "interview posts/面经, open or search a site, log in, fill a form, buy something, "
    "summarize an account or inbox, etc.). Do NOT run it. Put the command to run it in "
    "suggested_command as '/new <goal>', preserving the user's wording and language. "
    "Leave reply empty.\n"
    "- intent='chat': greetings, questions about what you can do, small talk, thanks, "
    "help. Answer briefly and helpfully in 'reply', in the user's language; when relevant, "
    "mention they can start a task with /new. Leave suggested_command empty.\n"
    "Keep everything short."
)


async def classify_message(text: str, llm=None) -> ChatDecision:
    """Classify a plain message as chat or an implied task."""
    llm = llm or build_llm()
    resp = await llm.ainvoke(
        [SystemMessage(content=_SYS), UserMessage(content=text)],
        output_format=ChatDecision,
    )
    return resp.completion

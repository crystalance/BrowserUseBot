"""Computer-Use Core — the "hands" of the on-call agent.

A thin wrapper around browser-use (reused for the LLM loop, tools, CDP, DOM),
exposing a single ``run_task(goal, policy) -> Result`` entry point.

This is M0: get a real browser run going with a persistent profile and a
clean result. Policy enforcement and login handoff come in later milestones.
"""

from browseruse_bot.core.result import Result
from browseruse_bot.core.policy import TaskPolicy
from browseruse_bot.core.handoff import Handoff
from browseruse_bot.core.runner import BrowserAgentRunner

__all__ = ["Result", "TaskPolicy", "Handoff", "BrowserAgentRunner"]

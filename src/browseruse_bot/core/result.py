"""Result: the compact summary the platform sends back to chat."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Result:
    ok: bool
    summary: str
    artifacts: list[str] = field(default_factory=list)  # file paths in the workspace
    needed_human: bool = False  # did we hand off for login?
    steps: int = 0

"""TaskPolicy: a plain allow-list. No policy engine — just a dataclass + checks.

Not enforced in M0; defined now so the runner signature is stable for later
milestones (M1 adds host checks, M2 adds approval gating).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TaskPolicy:
    allowed_hosts: list[str] = field(default_factory=list)
    require_approval_for_writes: bool = True

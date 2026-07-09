"""User-importable skills: reusable task recipes that steer the agent.

A *skill* is a Markdown file in the skills dir. On ``/new`` the request is
matched against each skill's triggers; the best match's instructions are
prepended to the task so regular jobs follow a proven path instead of the agent
improvising.

Skill file format (Markdown)::

    # Skill Name
    One-line description.

    Triggers: xiaohongshu, 小红书, 面经
    Allowed-Hosts: xiaohongshu.com        (optional)
    Login-Hosts: xiaohongshu.com          (optional)

    ## Instructions
    - Step-by-step guidance the agent should follow…
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("browseruse_bot")

SKILLS_DIR = Path("skills")


def _split_csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


@dataclass
class Skill:
    name: str
    description: str = ""
    triggers: list[str] = field(default_factory=list)
    instructions: str = ""
    allowed_hosts: list[str] = field(default_factory=list)
    login_hosts: list[str] = field(default_factory=list)
    path: Path | None = None

    def score(self, request: str) -> int:
        """How many triggers appear in the request (case-insensitive)."""
        r = request.lower()
        hits = sum(1 for t in self.triggers if t and t.lower() in r)
        # Also match on the skill name words as a weak fallback.
        if not hits and self.name.lower() in r:
            hits = 1
        return hits

    def build_task(self, request: str) -> str:
        """Prepend the skill instructions to the user's request."""
        return (
            f"Follow this skill '{self.name}' to accomplish the user's request.\n"
            f"{self.instructions.strip()}\n\n"
            f"User request: {request}"
        )


def parse_skill(text: str, path: Path | None = None) -> Skill | None:
    """Parse a Markdown skill. Returns None if there's no name heading."""
    lines = text.splitlines()
    name = ""
    for i, line in enumerate(lines):
        m = re.match(r"^#\s+(.+)$", line)
        if m:
            name = m.group(1).strip()
            lines = lines[i + 1 :]
            break
    if not name:
        return None

    # Split header block from the Instructions section.
    instr_idx = next(
        (i for i, l in enumerate(lines) if re.match(r"^##\s+instructions\s*$", l.strip(), re.I)),
        None,
    )
    header = lines[:instr_idx] if instr_idx is not None else lines
    instructions = "\n".join(lines[instr_idx + 1 :]).strip() if instr_idx is not None else ""

    skill = Skill(name=name, instructions=instructions, path=path)
    desc_lines: list[str] = []
    for line in header:
        s = line.strip()
        if not s:
            continue
        key = s.lower()
        if key.startswith("triggers:"):
            skill.triggers = _split_csv(s.split(":", 1)[1])
        elif key.startswith("allowed-hosts:"):
            skill.allowed_hosts = _split_csv(s.split(":", 1)[1])
        elif key.startswith("login-hosts:"):
            skill.login_hosts = _split_csv(s.split(":", 1)[1])
        else:
            desc_lines.append(s)
    skill.description = " ".join(desc_lines).strip()
    return skill


class SkillStore:
    """Loads skills from a directory and matches requests against them."""

    def __init__(self, skills_dir: Path | str = SKILLS_DIR) -> None:
        self.dir = Path(skills_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._skills: dict[str, Skill] = {}
        self.reload()

    def reload(self) -> None:
        self._skills.clear()
        for f in sorted(self.dir.glob("*.md")):
            try:
                skill = parse_skill(f.read_text(encoding="utf-8"), path=f)
                if skill:
                    self._skills[skill.name.lower()] = skill
            except Exception as e:  # noqa: BLE001
                logger.warning("skill %s failed to load: %s", f.name, e)
        logger.info("loaded %d skill(s): %s", len(self._skills), ", ".join(self._skills) or "none")

    def list(self) -> list[Skill]:
        return list(self._skills.values())

    def match(self, request: str) -> Skill | None:
        """Best-matching skill for the request, or None."""
        best, best_score = None, 0
        for skill in self._skills.values():
            s = skill.score(request)
            if s > best_score:
                best, best_score = skill, s
        return best

    def add(self, name: str, body: str) -> Skill:
        """Create/overwrite a skill from a name + instruction text."""
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "skill"
        text = body if body.lstrip().startswith("#") else f"# {name}\n\n## Instructions\n{body}"
        path = self.dir / f"{slug}.md"
        path.write_text(text, encoding="utf-8")
        self.reload()
        return self._skills[name.lower()] if name.lower() in self._skills else parse_skill(text, path)  # type: ignore[return-value]

    def save_file(self, filename: str, content: bytes | str) -> None:
        """Save an uploaded skill file into the skills dir, then reload."""
        name = Path(filename).name
        if not name.endswith(".md"):
            name += ".md"
        data = content.decode("utf-8") if isinstance(content, bytes) else content
        (self.dir / name).write_text(data, encoding="utf-8")
        self.reload()

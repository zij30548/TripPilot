"""Trusted local Skill prompt loading, adapted from EchoMind SkillManager.

Skills supplement a role; they cannot register Python, URLs or permissions.
No user-controlled path or runtime upload/reload endpoint is exposed.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Skill:
    name: str
    content: str
    sha256: str


class SkillManager:
    def __init__(self, root_dir: str | Path | None = None, max_prompt_chars: int = 5000) -> None:
        self.root_dir = (Path(root_dir) if root_dir is not None else Path(__file__).parent.parent / "skills").resolve()
        if not 1 <= max_prompt_chars <= 5000:
            raise ValueError("invalid_skill_limit")
        self.max_prompt_chars = max_prompt_chars
        self._skills: tuple[Skill, ...] = ()

    def load(self) -> list[Skill]:
        skills: list[Skill] = []
        total = 0
        # Fixed file names keep newly dropped local files from gaining authority.
        for name in ("travel_safety", "place_confirmation", "planning_boundaries"):
            path = self.root_dir / name / "SKILL.md"
            if not path.exists():
                continue
            if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != self.root_dir.parent):
                raise ValueError("skill_symlink")
            if not path.is_file() or path.stat().st_size > 12_000:
                raise ValueError("skill_file")
            data = path.read_bytes()
            content = data.decode("utf-8").strip()
            total += len(content)
            if not content or total > self.max_prompt_chars:
                raise ValueError("skill_limit")
            skills.append(Skill(name, content, hashlib.sha256(data).hexdigest()))
        self._skills = tuple(skills)
        return list(self._skills)

    def prompt_for(self, message: str, agent_type: str | None = None) -> str:
        # All three tiny travel invariants apply to every role; no keyword-based
        # suppression of safety rules and no unsafe truncation through a clause.
        return "\n\n".join(f"[可信旅行规则：{skill.name}]\n{skill.content}" for skill in self._skills)

    def summary(self) -> dict[str, object]:
        return {"count": len(self._skills), "skills": [{"name": skill.name, "sha256": skill.sha256} for skill in self._skills]}

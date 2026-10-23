"""Frozen-per-task travel rules using the preserved SkillManager algorithms.

Trusted files can be reloaded between tasks, but cannot change tools/authority
or split a running task across two rule versions. No HTTP upload/reload API.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
from typing import Any, Iterable

from echomind_core.diagnostics import quarantine_upstream_logging
from echomind_core.execution import ExecutionError
from echomind_core.upstream.core.skill_loader import SkillManager

NAMES = ("travel_safety", "place_confirmation", "planning_boundaries")
ROOT = Path(__file__).resolve().parents[1] / "skills"


class _FixedDiscovery(SkillManager):
    def _discover_files(self, root_dir: Path) -> Iterable[Path]:
        # New arbitrary Markdown cannot silently gain prompt authority.
        return tuple(root_dir / name / "SKILL.md" for name in NAMES)


@dataclass(frozen=True)
class FrozenTravelSkills:
    _manager: SkillManager
    hashes: tuple[tuple[str, str], ...]
    version: str

    def prompt_for(self, message: str, agent_type: str | None = None) -> str:
        return self._manager.prompt_for(message, agent_type)

    def summary(self) -> dict[str, Any]:
        return {"version": self.version, "skills": [{"name": n, "sha256": h} for n, h in self.hashes]}


class TravelSkillCatalog:
    def __init__(self, root: Path = ROOT):
        if not isinstance(root, Path) or not root.is_absolute() or ".." in root.parts:
            raise ExecutionError("travel_skills")
        self.root = root
        quarantine_upstream_logging()

    def capture(self) -> FrozenTravelSkills:
        hashes = []
        for name in NAMES:
            path = self.root / name / "SKILL.md"
            if any(p.is_symlink() for p in (path, *path.parents)):
                raise ExecutionError("travel_skills")
            try:
                info = path.stat()
                if (not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, os.getuid()}
                        or info.st_mode & 0o6022 or not 1 <= info.st_size <= 12_000):
                    raise ExecutionError("travel_skills")
                raw = path.read_bytes()
            except OSError:
                raise ExecutionError("travel_skills") from None
            hashes.append((name, hashlib.sha256(raw).hexdigest()))
        manager = _FixedDiscovery(str(self.root), max_prompt_chars=5000)
        loaded = manager.load()  # Original discovery/load/parse/matching preserved.
        if (manager.errors or len(loaded) != len(NAMES)
                or any(not item.enabled or item.keywords or item.agents
                       or not item.content or len(item.content) > 3000 for item in loaded)):
            raise ExecutionError("travel_skills")
        # Capture after reading as well, so a file change during load does not
        # attach an old hash to newly read rules. No auto-retry on change.
        for name, digest in hashes:
            try:
                actual = hashlib.sha256((self.root / name / "SKILL.md").read_bytes()).hexdigest()
            except OSError:
                raise ExecutionError("travel_skills_changed") from None
            if actual != digest:
                raise ExecutionError("travel_skills_changed")
        frozen = object.__new__(SkillManager)
        frozen.root_dir, frozen.max_prompt_chars = self.root, 5000
        frozen._skills, frozen._errors = deepcopy(loaded), []
        text = frozen.prompt_for("", "general")
        # All invariants are global and small. Refuse instead of truncating a
        # condition through the original per-skill/whole-prompt size behavior.
        if len(text) > 5000 or sum(len(item.to_prompt_block()) for item in loaded) > 5000:
            raise ExecutionError("travel_skills")
        version = hashlib.sha256("\n".join(f"{n}:{h}" for n, h in hashes).encode()).hexdigest()
        return FrozenTravelSkills(frozen, tuple(hashes), version)

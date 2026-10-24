"""Explicit, frozen travel Skill package using the original loader algorithms.

Nothing here changes the existing three-Skill catalog or grants tool authority.
Only the eight reviewed files may be captured. Parsing/matching/prompt assembly
are the preserved SkillManager methods, operating on checked in-memory bytes.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any

from echomind_core.diagnostics import quarantine_upstream_logging
from echomind_core.execution import ExecutionError
from echomind_core.travel_domain import TravelRole
from echomind_core.upstream.core.skill_loader import Skill, SkillManager

SKILL_VERSION = "travel-domain-skills-v2"
SKILL_ROOT = Path(__file__).resolve().parents[1] / "skills" / "travel_domain_v2"
MAX_FILE_BYTES = 12_000
MAX_TOTAL_BYTES = 64_000
MAX_SKILL_PROMPT_CHARS = 5000
SKILL_ROLES: tuple[tuple[str, tuple[TravelRole, ...]], ...] = (
    ("travel_intake", (TravelRole.COORDINATOR, TravelRole.PLANNING)),
    ("place_discovery_confirmation", (TravelRole.COORDINATOR, TravelRole.PLACE_REFERENCE)),
    ("schedule_constraints", (TravelRole.COORDINATOR, TravelRole.PLANNING)),
    ("budget_review", (TravelRole.COORDINATOR, TravelRole.BUDGET)),
    ("weather_material_grounding", (TravelRole.COORDINATOR, TravelRole.PLANNING, TravelRole.PLACE_REFERENCE)),
    ("adjustment_adoption", (TravelRole.COORDINATOR, TravelRole.PLANNING)),
    ("archive_media_navigation", (TravelRole.COORDINATOR, TravelRole.PLANNING, TravelRole.PLACE_REFERENCE)),
    ("memory_and_failure_governance", tuple(TravelRole)),
)


def _checked_info(path: Path, *, directory: bool) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError:
        raise ExecutionError("travel_package_skills_unavailable") from None
    kind = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if (not kind or info.st_uid not in {0, os.getuid()} or info.st_mode & 0o7022
            or not directory and info.st_nlink != 1):
        raise ExecutionError("travel_package_skills_permissions")
    return info


def _private_directory_descriptor(path: Path) -> int:
    """Walk directory descriptors without following any ancestor symlink."""
    descriptor = -1
    try:
        for component in path.parts:
            following = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                **({"dir_fd": descriptor} if descriptor >= 0 else {}))
            if descriptor >= 0:
                os.close(descriptor)
            descriptor = following
            info = os.fstat(descriptor)
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, os.getuid()}
                    or info.st_mode & 0o7022):
                raise ExecutionError("travel_package_skills_permissions")
        result, descriptor = descriptor, -1
        return result
    except OSError:
        raise ExecutionError("travel_package_skills_unavailable") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_checked(path: Path) -> bytes:
    before = _checked_info(path, directory=False)
    if not 1 <= before.st_size <= MAX_FILE_BYTES:
        raise ExecutionError("travel_package_skills_limit")
    descriptor, parent = -1, -1
    try:
        parent = _private_directory_descriptor(path.parent)
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_mode, opened.st_uid, opened.st_nlink) != (
                before.st_dev, before.st_ino, before.st_mode, before.st_uid, before.st_nlink):
            raise ExecutionError("travel_package_skills_changed")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            raw = stream.read(MAX_FILE_BYTES + 1)
            after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ExecutionError("travel_package_skills_changed")
        if not 1 <= len(raw) <= MAX_FILE_BYTES:
            raise ExecutionError("travel_package_skills_limit")
        raw.decode("utf-8", errors="strict")
        return raw
    except (OSError, UnicodeError):
        raise ExecutionError("travel_package_skills_unavailable") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent >= 0:
            os.close(parent)


@dataclass(frozen=True)
class _CapturedPath:
    """Private read-only facade consumed by the original text parser."""
    path: Path
    text: str

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def parent(self) -> Path:
        return self.path.parent

    @property
    def stem(self) -> str:
        return self.path.stem

    @property
    def suffix(self) -> str:
        return self.path.suffix

    def read_text(self, encoding: str = "utf-8") -> str:
        if encoding != "utf-8":
            raise ExecutionError("travel_package_skills_invalid")
        return self.text

    def __str__(self) -> str:
        return str(self.path)


class _CapturedLoader(SkillManager):
    def __init__(self, root: Path, files: tuple[_CapturedPath, ...]):
        super().__init__(str(root), max_prompt_chars=MAX_SKILL_PROMPT_CHARS)
        self._captured_files = files

    def _discover_files(self, root_dir: Path) -> tuple[_CapturedPath, ...]:
        return self._captured_files


def _manager(serialized: bytes) -> SkillManager:
    # A new private manager prevents caller mutation or a later reload from
    # changing a captured task. The original matching and prompt code is used.
    manager = object.__new__(SkillManager)
    manager.root_dir, manager.max_prompt_chars = SKILL_ROOT, MAX_SKILL_PROMPT_CHARS
    manager._skills = [Skill(**row) for row in json.loads(serialized)]
    manager._errors = []
    return manager


@dataclass(frozen=True)
class FrozenTravelPackageSkills:
    _serialized: bytes
    hashes: tuple[tuple[str, str], ...]
    version: str

    def prompt_for(self, role: TravelRole, message: str) -> str:
        if (type(role) is not TravelRole or type(message) is not str or len(message) > 4000
                or any(ord(char) < 32 and char not in "\n\t" for char in message)):
            raise ExecutionError("travel_package_input")
        return _manager(self._serialized).prompt_for(message, role.value)

    def summary(self) -> dict[str, Any]:
        return {"schema_version": SKILL_VERSION, "version": self.version,
                "skills": [{"id": row["name"], "sha256": dict(self.hashes)[row["name"]],
                            "roles": list(row["agents"])} for row in json.loads(self._serialized)]}


class TravelPackageSkillCatalog:
    """Capture trusted local rules; no path, registration or reload HTTP API."""

    def __init__(self, root: Path = SKILL_ROOT):
        if (type(root) is not type(SKILL_ROOT) or not root.is_absolute()
                or ".." in root.parts):
            raise ExecutionError("travel_package_skills_root")
        self.root = root

    def capture(self) -> FrozenTravelPackageSkills:
        quarantine_upstream_logging()
        for parent in reversed((self.root, *self.root.parents)):
            _checked_info(parent, directory=True)
        expected = {name for name, _ in SKILL_ROLES}
        try:
            if {path.name for path in self.root.iterdir()} != expected:
                raise ExecutionError("travel_package_skills_files")
            captured, hashes = [], []
            for name, _ in SKILL_ROLES:
                folder = self.root / name
                _checked_info(folder, directory=True)
                if {path.name for path in folder.iterdir()} != {"SKILL.md"}:
                    raise ExecutionError("travel_package_skills_files")
                path = folder / "SKILL.md"
                raw = _read_checked(path)
                hashes.append((name, hashlib.sha256(raw).hexdigest()))
                captured.append(_CapturedPath(path, raw.decode("utf-8")))
            if sum(len(row.text.encode()) for row in captured) > MAX_TOTAL_BYTES:
                raise ExecutionError("travel_package_skills_limit")
            # Preserve original discovery, but admit only the prechecked fixed
            # set. Parsing below reads the captured bytes, not mutable files.
            discovery = object.__new__(SkillManager)
            discovered = tuple(SkillManager._discover_files(discovery, self.root))
            if set(discovered) != {row.path for row in captured} or len(discovered) != len(captured):
                raise ExecutionError("travel_package_skills_files")
            by_path = {row.path: row for row in captured}
            loader = _CapturedLoader(self.root, tuple(by_path[path] for path in discovered))
            loaded = loader.load()
        except OSError:
            raise ExecutionError("travel_package_skills_unavailable") from None
        if loader.errors or len(loaded) != len(SKILL_ROLES):
            raise ExecutionError("travel_package_skills_invalid")
        entries = {row.name: row for row in loaded}
        if set(entries) != expected:
            raise ExecutionError("travel_package_skills_invalid")
        rows = []
        for name, roles in SKILL_ROLES:
            item = entries[name]
            if (not item.enabled or item.keywords or item.agents != [role.value for role in roles]
                    or not item.content or len(item.content) > 2800 or len(item.description) > 160
                    or any(ord(char) < 32 and char not in "\n\t" for char in item.content)):
                raise ExecutionError("travel_package_skills_invalid")
            rows.append({"name": name, "description": item.description, "content": item.content,
                         "path": name + "/SKILL.md", "keywords": [], "agents": list(item.agents), "enabled": True})
        serialized = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        manager = _manager(serialized)
        for role in TravelRole:
            # Original loader truncates oversized prompts. Refuse instead of
            # silently losing a condition or forbidden-action clause.
            size = sum(len(item.to_prompt_block()) for item in manager.skills if item.matches("", role.value))
            if size > MAX_SKILL_PROMPT_CHARS:
                raise ExecutionError("travel_package_skills_limit")
        for name, digest in hashes:
            if hashlib.sha256(_read_checked(self.root / name / "SKILL.md")).hexdigest() != digest:
                raise ExecutionError("travel_package_skills_changed")
        version = hashlib.sha256(json.dumps([SKILL_VERSION, hashes], separators=(",", ":")).encode()).hexdigest()
        return FrozenTravelPackageSkills(serialized, tuple(hashes), version)

"""Local artifact inspection, dependency tightening and public-code publication.

No shell, chown, symlink traversal during walks, content writes or rollback.
The CLI caller fixes the root/owner and serializes deployment operations.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
from typing import Callable
from uuid import uuid4


class ArtifactError(ValueError):
    """Only fixed diagnostic codes, never file contents or configuration."""


@dataclass(frozen=True)
class Entry:
    path: str
    kind: str
    device: int
    inode: int
    uid: int
    gid: int
    mode: int
    links: int
    size: int
    mtime_ns: int
    target: str | None = None


def _require(value: bool, code: str) -> None:
    if not value:
        raise ArtifactError(code)


def _root_fd(path: Path) -> int:
    """Open every absolute path component without following a symlink."""
    _require(path.is_absolute() and ".." not in path.parts, "absolute_artifact_path_required")
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _entry(relative: str, info: os.stat_result, owner: tuple[int, int], allow_writable: bool) -> Entry:
    _require((info.st_uid, info.st_gid) == owner, "artifact_unexpected_owner")
    mode = stat.S_IMODE(info.st_mode)
    _require(not mode & 0o7000, "artifact_special_permissions")
    if stat.S_ISDIR(info.st_mode):
        kind = "directory"
    elif stat.S_ISREG(info.st_mode):
        kind = "file"
        _require(info.st_nlink == 1, "artifact_hardlinked_file")
    elif stat.S_ISLNK(info.st_mode):
        kind = "symlink"
    else:
        raise ArtifactError("artifact_special_file")
    if kind != "symlink" and not allow_writable:
        _require(not mode & 0o022, "artifact_group_or_world_writable")
    return Entry(relative, kind, info.st_dev, info.st_ino, info.st_uid, info.st_gid,
                 mode, info.st_nlink, info.st_size, info.st_mtime_ns)


def scan_tree(root: Path, owner: tuple[int, int], *, allow_writable: bool = False,
              skip_cache: bool = False) -> list[Entry]:
    """Inspect the entire copied scope, including its root; never walk links."""
    descriptor = _root_fd(root)
    try:
        root_entry = _entry(".", os.fstat(descriptor), owner, allow_writable)
        _require(root_entry.kind == "directory", "artifact_directory_required")
        entries = [root_entry]

        def walk(parent: int, prefix: str) -> None:
            for name in sorted(os.listdir(parent)):
                if skip_cache and not prefix and name == "cache":
                    continue  # Same .next/cache exclusion as the copied artifact.
                relative = f"{prefix}/{name}" if prefix else name
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                item = _entry(relative, info, owner, allow_writable)
                if item.kind == "symlink":
                    target = os.readlink(name, dir_fd=parent)
                    _require(not os.path.isabs(target), "external_artifact_symlink")
                    lexical = Path(os.path.abspath(root / relative / ".." / target))
                    _require(lexical.is_relative_to(root), "external_artifact_symlink")
                    try:
                        resolved = (root / relative).resolve(strict=True)
                    except (OSError, RuntimeError, ValueError):
                        raise ArtifactError("artifact_symlink_target_invalid") from None
                    _require(resolved.is_relative_to(root), "external_artifact_symlink")
                    entries.append(replace(item, target=target))
                else:
                    entries.append(item)
                    if item.kind == "directory":
                        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                        try:
                            _require(_entry(relative, os.fstat(child), owner, allow_writable) == item,
                                     "artifact_changed_during_inspection")
                            walk(child, relative)
                        finally:
                            os.close(child)

        walk(descriptor, "")
        _require(os.fstat(descriptor).st_ino == root.lstat().st_ino
                 and os.fstat(descriptor).st_dev == root.lstat().st_dev,
                 "artifact_root_changed")
        return entries
    finally:
        os.close(descriptor)


def _save_manifest(directory: Path, value: dict[str, object], *, prefix: str = "dependency-permissions") -> Path:
    _require(prefix in {"dependency-permissions", "release-permissions"}, "permission_manifest_prefix_invalid")
    descriptor = _root_fd(directory)
    try:
        info = os.fstat(descriptor)
        _require((info.st_uid, info.st_gid) == (os.geteuid(), os.getegid())
                 and stat.S_IMODE(info.st_mode) == 0o700, "permission_manifest_directory_permissions")
        name = f"{prefix}-{uuid4().hex}.json"
        file = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
        with os.fdopen(file, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(descriptor)
        return directory / name
    finally:
        os.close(descriptor)


def _chmod_entry(root: int, entry: Entry, known: dict[str, Entry], *, mode: int | None = None) -> None:
    """Use anchored descriptors, not a path-based chmod that could follow links."""
    parent = os.dup(root)
    opened: int | None = None
    try:
        parts = Path(entry.path).parts if entry.path != "." else ()
        prefix: list[str] = []
        for part in parts[:-1]:
            prefix.append(part)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
            expected = known["/".join(prefix)]
            _require(_entry(expected.path, os.fstat(parent), (expected.uid, expected.gid), True) == expected,
                     "artifact_changed_before_permission_update")
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        if entry.kind == "directory":
            flags |= os.O_DIRECTORY
        opened = os.open(parts[-1], flags, dir_fd=parent) if parts else os.dup(root)
        actual = _entry(entry.path, os.fstat(opened), (entry.uid, entry.gid), True)
        _require(actual == entry, "artifact_changed_before_permission_update")
        os.fchmod(opened, entry.mode & ~0o022 if mode is None else mode)
    finally:
        if opened is not None:
            os.close(opened)
        os.close(parent)


def runtime_mode(entry: Entry) -> int:
    """Only copied public code: add reading/traversal, never shared writing.

    Original executable files stay executable for the service identity. Files
    with no execute bits do not become executable. Symlinks are never chmod'ed.
    The scanner independently rejects unsafe writing/special permissions.
    """
    if entry.kind == "symlink":
        return entry.mode
    return entry.mode | (0o555 if entry.kind == "directory" or entry.mode & 0o111 else 0o444)


def publish_readable(root: Path, owner: tuple[int, int], plan: list[Entry], *, skip_cache: bool = False) -> int:
    """Publish a verified copied tree; existing-release callers must audit first.

    Never use on configuration/data/source. Stage uses newly created targets;
    the separate explicit repair command writes a private manifest first.
    """
    _require(scan_tree(root, owner, skip_cache=skip_cache) == plan, "artifact_changed_before_permission_update")
    descriptor = _root_fd(root)
    try:
        _require(_entry(".", os.fstat(descriptor), owner, False) == plan[0], "artifact_root_changed")
        known = {entry.path: entry for entry in plan}
        changed = 0
        for entry in plan:
            mode = runtime_mode(entry)
            if entry.kind != "symlink" and mode != entry.mode:
                _chmod_entry(descriptor, entry, known, mode=mode)
                known[entry.path] = replace(entry, mode=mode)
                changed += 1
        _require(scan_tree(root, owner, skip_cache=skip_cache) == [known[e.path] for e in plan],
                 "artifact_changed_after_permission_update")
        return changed
    finally:
        os.close(descriptor)


def save_publication_manifest(directory: Path, value: dict[str, object]) -> Path:
    return _save_manifest(directory, value, prefix="release-permissions")


def remove_write_bits(root: Path, owner: tuple[int, int], plan: list[Entry], directory: Path,
                      fingerprint: Callable[[], dict[str, object]], *, commit: str) -> tuple[Path, int]:
    """Complete preflight + immutable private manifest precede any chmod.

    Errors leave the manifest and any already-converged modes intact. The caller
    must stop npm/build/other writers; deployment locking alone cannot stop them.
    """
    _require(scan_tree(root, owner, allow_writable=True) == plan, "artifact_changed_before_permission_update")
    before = fingerprint()  # Existing source/lock/build/static/dependency hashes.
    _require(scan_tree(root, owner, allow_writable=True) == plan, "artifact_changed_before_permission_update")
    manifest = _save_manifest(directory, {
        "schema_version": "dependency-permissions-v1", "state": "prepared_not_completion_receipt",
        "recorded_at": datetime.now(timezone.utc).isoformat(), "commit": commit, "root": str(root),
        "owner_uid": owner[0], "owner_gid": owner[1], "content_before": before,
        "operation": "mode & ~0o022 on regular files/directories only", "entries": [asdict(e) for e in plan],
    })
    print(f"Original dependency permissions saved privately: {manifest}", flush=True)
    descriptor = _root_fd(root)
    try:
        _require(_entry(".", os.fstat(descriptor), owner, True) == plan[0], "artifact_root_changed")
        known = {e.path: e for e in plan}
        changed = 0
        for entry in plan:
            if entry.kind != "symlink" and entry.mode & 0o022:
                _chmod_entry(descriptor, entry, known)
                known[entry.path] = replace(entry, mode=entry.mode & ~0o022)
                changed += 1
        expected = [known[e.path] for e in plan]
        _require(scan_tree(root, owner) == expected, "artifact_changed_after_permission_update")
        _require(fingerprint() == before, "artifact_content_changed_after_permission_update")
        return manifest, changed
    finally:
        os.close(descriptor)

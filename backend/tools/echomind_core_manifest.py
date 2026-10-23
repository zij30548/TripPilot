"""Verify the complete, mechanically rebased EchoMind core without importing it.

This standard-library-only verifier performs no model, database, Redis, Chroma,
network or subprocess operation.  The uploaded reference is optional after
delivery: delivered hashes are always checked; when present, reference bytes
must independently reconstruct every imported file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any


SCHEMA_VERSION = "echomind-core-provenance-v1"
SOURCE_COMMIT = "ef847eb5b719ef98be9d5673f3429e62cd88109e"
CORE_DIRECTORIES = ("agents", "core", "tooling", "memory", "monitor", "evaluation")
TRANSFORM_ALLOWLIST = (
    "rebase_internal_from_imports",
    "rebase_internal_module_imports",
    "bind_anthropic_messages_to_qwen_adapter",
    "rebase_upstream_test_helper_imports",
    "rebase_two_test_importlib_module_names",
)
EXCLUDED_TESTS = {
    "tests/test_chat_reliability.py": "Requires the original API, auth and chat transaction runtime; not a portable core unit test.",
    "tests/verify_chat_atomic.py": "Standalone original API/Redis integration launcher; must not run against current services.",
    "tests/verify_memory_concurrency.py": "Standalone original Redis integration launcher; requires a separately scoped isolated service test.",
    "tests/verify_memory_keys.py": "Standalone original Redis integration launcher; requires a separately scoped isolated service test.",
    "tests/verify_request_concurrency.py": "Standalone original API/Redis/Chroma integration launcher; must not run against current services.",
}


class ManifestError(ValueError):
    """Safe, bounded provenance failure; never includes file contents."""


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _path(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 240 or re.fullmatch(r"[A-Za-z0-9_./-]+", value) is None:
        raise ManifestError("invalid_manifest_path")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(p in (".", "..") for p in path.parts):
        raise ManifestError("invalid_manifest_path")
    if path.as_posix() != value or "\\" in value:
        raise ManifestError("invalid_manifest_path")
    return value


def _exact(value: Any, keys: set[str], reason: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ManifestError(reason)
    return value


def _digest(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ManifestError("invalid_manifest_digest")
    return value


def _regular_file(root: Path, relative: str) -> Path:
    candidate = root / _path(relative)
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ManifestError("symlink_forbidden")
    if not candidate.is_file() or not candidate.resolve().is_relative_to(root.resolve()):
        raise ManifestError("missing_or_escaping_file")
    return candidate


def transform(content: bytes, *, is_test: bool) -> tuple[bytes, list[str]]:
    """Only rebase module identities and the provider import; preserve algorithms."""
    text = content.decode("utf-8")
    applied: list[str] = []
    groups = "|".join(CORE_DIRECTORIES)
    replacements = [
        (
            "rebase_internal_from_imports",
            rf"(?m)^(\s*from )({groups})(?=[.\s])",
            r"\1echomind_core.upstream.\2",
        ),
        (
            "rebase_internal_module_imports",
            rf"(?m)^(\s*import )({groups})(?=\.)",
            r"\1echomind_core.upstream.\2",
        ),
        (
            "bind_anthropic_messages_to_qwen_adapter",
            r"(?m)^from anthropic import AsyncAnthropic$",
            "from echomind_core.provider import QwenMessagesClient as AsyncAnthropic",
        ),
    ]
    if is_test:
        replacements.extend([
            (
                "rebase_upstream_test_helper_imports",
                r"(?m)^(\s*from )(test_agent_orchestrator|test_knowledge_base)( import )",
                r"\1echomind_core.upstream.tests.\2\3",
            ),
            (
                "rebase_two_test_importlib_module_names",
                r'"(tooling\.knowledge_base|memory\.conversation_memory)"',
                r'"echomind_core.upstream.\1"',
            ),
        ])
    for identifier, pattern, replacement in replacements:
        text, count = re.subn(pattern, replacement, text)
        if count:
            applied.append(identifier)
    return text.encode("utf-8"), applied


def source_inventory(source_root: Path) -> list[str]:
    """The complete six core directories, original Skills and portable unit tests."""
    inventory: list[str] = []
    for directory in CORE_DIRECTORIES:
        parent = source_root / directory
        if parent.is_symlink() or not parent.is_dir():
            raise ManifestError("invalid_source_directory")
        inventory.extend(p.relative_to(source_root).as_posix() for p in parent.rglob("*.py"))
    for directory in ("skills", "tests"):
        if (source_root / directory).is_symlink() or not (source_root / directory).is_dir():
            raise ManifestError("invalid_source_directory")
    inventory.extend(p.relative_to(source_root).as_posix() for p in (source_root / "skills").rglob("*.md"))
    for path in (source_root / "tests").glob("*.py"):
        relative = path.relative_to(source_root).as_posix()
        if relative not in EXCLUDED_TESTS:
            inventory.append(relative)
    for relative in inventory:
        _regular_file(source_root, relative)
    return sorted(inventory)


def generated_namespaces(inventory: list[str]) -> list[str]:
    generated = {"__init__.py"}
    for relative in inventory:
        for parent in PurePosixPath(relative).parents:
            if parent.as_posix() != ".":
                generated.add((parent / "__init__.py").as_posix())
    return sorted(generated - set(inventory))


def build_manifest(source_root: Path) -> dict[str, Any]:
    """Pure in-memory manifest construction, also used for reproducible verification."""
    inventory = source_inventory(source_root)
    files: list[dict[str, Any]] = []
    for relative in inventory:
        original = _regular_file(source_root, relative).read_bytes()
        delivered, transformations = transform(original, is_test=relative.startswith("tests/")) if relative.endswith(".py") else (original, [])
        files.append({
            "path": relative,
            "source_path": relative,
            "source_sha256": sha256(original),
            "delivered_sha256": sha256(delivered),
            "transformations": transformations,
        })
    for relative in generated_namespaces(inventory):
        files.append({
            "path": relative,
            "source_path": None,
            "source_sha256": None,
            "delivered_sha256": sha256(b""),
            "transformations": [],
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "source_commit": SOURCE_COMMIT,
        "source_repository": "User-provided EchoMind reference; license/redistribution permission requires separate confirmation.",
        "scope": "Complete Python core in agents/core/tooling/memory/monitor/evaluation; original Skill Markdown and portable upstream unit tests. Not the original API/auth/environment/data/launchers.",
        "transform_allowlist": list(TRANSFORM_ALLOWLIST),
        "files": sorted(files, key=lambda item: item["path"]),
        "excluded_tests": [
            {"path": relative, "source_sha256": sha256(_regular_file(source_root, relative).read_bytes()), "reason": reason}
            for relative, reason in sorted(EXCLUDED_TESTS.items())
        ],
    }


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError("duplicate_manifest_key")
        result[key] = value
    return result


def verify(delivered_root: Path, manifest_path: Path, source_root: Path | None = None) -> dict[str, int | str]:
    if delivered_root.is_symlink() or manifest_path.is_symlink():
        raise ManifestError("symlink_forbidden")
    try:
        raw = manifest_path.read_bytes()
        if len(raw) > 256 * 1024:
            raise ManifestError("manifest_too_large")
        manifest = json.loads(raw, object_pairs_hook=_no_duplicate_keys)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError("manifest_unreadable") from exc
    _exact(manifest, {"schema_version", "source_commit", "source_repository", "scope", "transform_allowlist", "files", "excluded_tests"}, "invalid_manifest_fields")
    if manifest["schema_version"] != SCHEMA_VERSION or manifest["source_commit"] != SOURCE_COMMIT:
        raise ManifestError("unsupported_manifest_version")
    if manifest["transform_allowlist"] != list(TRANSFORM_ALLOWLIST):
        raise ManifestError("transformation_allowlist_changed")
    if not isinstance(manifest["source_repository"], str) or not isinstance(manifest["scope"], str) or not 1 <= len(manifest["source_repository"]) <= 512 or not 1 <= len(manifest["scope"]) <= 1024:
        raise ManifestError("invalid_manifest_metadata")
    if not isinstance(manifest["excluded_tests"], list) or len(manifest["excluded_tests"]) != len(EXCLUDED_TESTS):
        raise ManifestError("invalid_excluded_tests")
    excluded: set[str] = set()
    for entry in manifest["excluded_tests"]:
        _exact(entry, {"path", "source_sha256", "reason"}, "invalid_excluded_test_fields")
        relative = _path(entry["path"])
        if relative in excluded or relative not in EXCLUDED_TESTS or entry["reason"] != EXCLUDED_TESTS[relative]:
            raise ManifestError("invalid_excluded_tests")
        excluded.add(relative)
        _digest(entry["source_sha256"])
    if not isinstance(manifest["files"], list) or not 1 <= len(manifest["files"]) <= 100:
        raise ManifestError("invalid_manifest_files")
    declared: set[str] = set()
    source_paths: list[str] = []
    generated: list[str] = []
    imported = 0
    for item in manifest["files"]:
        _exact(item, {"path", "source_path", "source_sha256", "delivered_sha256", "transformations"}, "invalid_file_fields")
        relative = _path(item["path"])
        if relative in declared:
            raise ManifestError("duplicate_manifest_path")
        declared.add(relative)
        transformations = item["transformations"]
        if not isinstance(transformations, list) or any(not isinstance(value, str) for value in transformations) or len(transformations) != len(set(transformations)) or any(value not in TRANSFORM_ALLOWLIST for value in transformations):
            raise ManifestError("invalid_file_transformations")
        if sha256(_regular_file(delivered_root, relative).read_bytes()) != _digest(item["delivered_sha256"]):
            raise ManifestError("delivered_hash_mismatch")
        if item["source_path"] is None:
            if not relative.endswith("__init__.py") or item["source_sha256"] is not None or transformations or item["delivered_sha256"] != sha256(b""):
                raise ManifestError("invalid_generated_namespace")
            generated.append(relative)
        else:
            if _path(item["source_path"]) != relative:
                raise ManifestError("source_path_mismatch")
            _digest(item["source_sha256"])
            pieces = PurePosixPath(relative).parts
            allowed = (
                pieces[0] in CORE_DIRECTORIES and relative.endswith(".py")
                or pieces[0] == "skills" and relative.endswith(".md")
                or pieces[0] == "tests" and len(pieces) == 2 and relative not in EXCLUDED_TESTS and (pieces[1].startswith("test_") and pieces[1].endswith(".py") or pieces[1] == "mcp_fixture_server.py")
            )
            if not allowed:
                raise ManifestError("source_outside_import_scope")
            source_paths.append(relative)
            imported += 1
    if sorted(generated) != generated_namespaces(source_paths):
        raise ManifestError("generated_namespace_inventory_mismatch")
    actual: set[str] = set()
    for item in delivered_root.rglob("*"):
        if item.is_symlink():
            raise ManifestError("symlink_forbidden")
        if item.is_file():
            actual.add(item.relative_to(delivered_root).as_posix())
        elif not item.is_dir():
            raise ManifestError("special_file_forbidden")
    if actual != declared:
        raise ManifestError("delivered_inventory_mismatch")
    if source_root is not None:
        if source_root.is_symlink() or not source_root.is_dir():
            raise ManifestError("invalid_source_directory")
        if manifest != build_manifest(source_root):
            raise ManifestError("source_reconstruction_mismatch")
    return {"schema_version": SCHEMA_VERSION, "imported_files": imported, "generated_namespaces": len(declared) - imported, "source_reconstruction": "verified" if source_root is not None else "reference_absent_delivered_hashes_verified"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Optional original EchoMind directory; automatically checked when present.")
    arguments = parser.parse_args()
    backend = Path(__file__).resolve().parents[1]
    source = arguments.source or backend.parent / "EchoMind"
    checked_source = source if arguments.source is not None or source.exists() else None
    try:
        result = verify(backend / "echomind_core" / "upstream", backend / "echomind_core" / "SOURCE.json", checked_source)
    except ManifestError as exc:
        parser.exit(1, f"EchoMind provenance verification failed: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

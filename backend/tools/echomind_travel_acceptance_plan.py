"""OFFLINE ONLY: validate/freeze a zero-authority travel acceptance plan.

No sender, credentials, clients, service startup or quota ledger is present.
Business-text projection is not a provider payload: actual workspace snapshots
and model-dependent native feedback must be bound in a later authorized runner.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

from tools.echomind_travel_evaluation import Authorization, EvaluationError, canonical, strict_json

ROOT = Path(__file__).resolve().parents[2]
VERSION = "echomind-travel-acceptance-plan-v1"
PROTOCOL_VERSION = "echomind-travel-offline-freeze-v1"
DEFAULT_MANIFEST = ROOT / "backend/evaluations/echomind-travel-acceptance-plan-v1.json"
PRIOR_MANIFEST = "backend/evaluations/echomind-travel-v1.json"
MAX_MANIFEST = 128 * 1024
MAX_FILE = 8 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
MAX_PROTOCOL = 2 * 1024 * 1024
SOURCE_TREES = {
    **{f"backend/{name}": {".py", ".json", ".md"} for name in (
        "api", "agents", "core", "echomind_core", "memory", "monitor", "evaluation", "tooling", "skills", "tools")},
    "backend/knowledge": {".json", ".md"},
    "frontend/src": {".ts", ".tsx"}, "shared": {".json"},
}
FIXED_SOURCES = (
    "frontend/package.json", "frontend/package-lock.json", "frontend/next.config.ts",
    "frontend/tsconfig.json", "frontend/eslint.config.mjs", "frontend/postcss.config.mjs",
    "frontend/vitest.config.mts", "backend/pyproject.toml", "backend/uv.lock", "backend/main.py",
)
ALLOWED_TOOLS = frozenset({"inspect_trip", "travel_clarify", "update_trip_requirements",
                           "travel_schedule", "travel_budget", "travel_adjustment"})
Text = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=2000)]
Identifier = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Hash = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-f0-9]{64}$")]
Commit = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-f0-9]{40}$")]
ToolName = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Runtime(Strict):
    model: Literal["qwen3.7-flash-2026-07-15"]
    mode: Literal["direct_tools"]
    max_tokens: Literal[900]
    temperature: Literal[0.1]
    stream: Literal[False]
    enable_thinking: Literal[False]
    parallel_tool_calls: Literal[False]
    task_model_limit: Literal[3]
    task_tool_limit: Literal[4]
    task_seconds: Literal[45]
    active_product_cutover: Literal[False]

    @model_validator(mode="before")
    @classmethod
    def scalar_types(cls, value: Any) -> Any:
        if type(value) is dict:
            for key in ("max_tokens", "task_model_limit", "task_tool_limit", "task_seconds"):
                if type(value.get(key)) is not int:
                    raise ValueError("integer_required")
            for key in ("stream", "enable_thinking", "parallel_tool_calls", "active_product_cutover"):
                if type(value.get(key)) is not bool:
                    raise ValueError("boolean_required")
            if type(value.get("temperature")) is not float:
                raise ValueError("temperature_type")
        return value


class PriorManifest(Strict):
    path: Literal["backend/evaluations/echomind-travel-v1.json"]
    sha256: Hash


class Fixture(Strict):
    id: Identifier
    specification: list[Text] = Field(min_length=1, max_length=20)


class Operation(Strict):
    id: Identifier
    group: Literal["clarification", "requirements", "day", "budget", "preview"]
    branch: Literal["native_candidate", "local_confirmation"]
    fixture_id: Identifier
    depends_on: list[Identifier] = Field(max_length=12)
    text: Text
    expected: list[Text] = Field(min_length=1, max_length=12)
    blocking: list[Text] = Field(min_length=1, max_length=12)
    allowed_tools: list[ToolName] = Field(max_length=16)
    expected_action: ToolName | None

    @model_validator(mode="after")
    def branch_contract(self) -> "Operation":
        if len(set(self.allowed_tools)) != len(self.allowed_tools) or len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("duplicate_operation_value")
        if bool(self.allowed_tools) != (self.branch == "native_candidate"):
            raise ValueError("branch_tools")
        if not set(self.allowed_tools) <= ALLOWED_TOOLS:
            raise ValueError("unknown_plan_tool")
        if self.branch == "local_confirmation" and self.expected_action is None:
            raise ValueError("local_action_required")
        return self


class Limits(Strict):
    planned_native_tasks: Literal[7]
    planned_local_submissions: Literal[7]
    recommended_model_send_cap: Literal[21]
    expected_model_sends: Literal[14]
    real_amap_calls: Literal[0]

    @model_validator(mode="before")
    @classmethod
    def scalar_types(cls, value: Any) -> Any:
        if type(value) is dict and any(type(value.get(key)) is not int for key in cls.model_fields):
            raise ValueError("integer_required")
        return value


class Manifest(Strict):
    schema_version: Literal["echomind-travel-acceptance-plan-v1"]
    baseline_commit: Commit
    preparation: Literal["offline_plan_only"]
    authorization: Authorization
    runtime: Runtime
    prior_manifest: PriorManifest
    fixtures: list[Fixture] = Field(min_length=1, max_length=4)
    operations: list[Operation] = Field(min_length=1, max_length=20)
    limits: Limits
    live_prerequisites: list[Text] = Field(min_length=1, max_length=15)

    @model_validator(mode="after")
    def matrix(self) -> "Manifest":
        fixtures = {row.id for row in self.fixtures}
        if len(fixtures) != len(self.fixtures):
            raise ValueError("duplicate_fixture")
        previous: set[str] = set()
        for row in self.operations:
            if row.id in previous or not set(row.depends_on) <= previous or row.fixture_id not in fixtures:
                raise ValueError("operation_identity_or_dependency")
            previous.add(row.id)
        native = sum(row.branch == "native_candidate" for row in self.operations)
        local = len(self.operations) - native
        if (native != self.limits.planned_native_tasks or local != self.limits.planned_local_submissions
                or self.limits.recommended_model_send_cap != native * self.runtime.task_model_limit
                or self.limits.expected_model_sends != native * 2
                or {row.group for row in self.operations} != {"clarification", "requirements", "day", "budget", "preview"}):
            raise ValueError("operation_matrix_or_cost")
        return self


class Protocol(Strict):
    schema_version: Literal["echomind-travel-offline-freeze-v1"]
    baseline_commit: Commit
    preparation: Literal["offline_plan_only"]
    authorization: Authorization
    manifest_path: Text
    manifest_sha256: Hash
    source_files: dict[str, Hash] = Field(min_length=1, max_length=10000)
    real_model_calls: Literal[0]
    real_amap_calls: Literal[0]
    live_ready: Literal[False]
    full_payload_frozen: Literal[False]

    @model_validator(mode="before")
    @classmethod
    def scalar_types(cls, value: Any) -> Any:
        if type(value) is dict:
            if any(type(value.get(key)) is not int for key in ("real_model_calls", "real_amap_calls")):
                raise ValueError("integer_required")
            if any(type(value.get(key)) is not bool for key in ("live_ready", "full_payload_frozen")):
                raise ValueError("boolean_required")
        return value


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _relative(value: str) -> str:
    if not isinstance(value, str):
        raise EvaluationError("unsafe_relative_path")
    path = Path(value)
    if (not value or not path.parts or path.is_absolute() or ".." in path.parts
            or "\\" in value or path.as_posix() != value or "." in path.parts):
        raise EvaluationError("unsafe_relative_path")
    return value


def _path(root: Path, relative: str) -> Path:
    value = root / _relative(relative)
    current = root
    if root.is_symlink() or not root.is_dir():
        raise EvaluationError("unsafe_source_root")
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise EvaluationError("source_symlink")
    return value


def _read(path: Path, limit: int) -> bytes:
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise EvaluationError("unsafe_or_oversized_file")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
                    or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino)):
                raise EvaluationError("unsafe_or_oversized_file")
            chunks = bytearray()
            while len(chunks) <= limit:
                part = os.read(descriptor, min(65536, limit + 1 - len(chunks)))
                if not part:
                    return bytes(chunks)
                chunks.extend(part)
            raise EvaluationError("oversized_file")
        finally:
            os.close(descriptor)
    except EvaluationError:
        raise
    except OSError:
        raise EvaluationError("file_unavailable") from None


def load(path: Path = DEFAULT_MANIFEST) -> Manifest:
    try:
        return Manifest.model_validate(strict_json(_read(path, MAX_MANIFEST)))
    except EvaluationError:
        raise
    except (ValueError, TypeError, ValidationError, RecursionError):
        raise EvaluationError("invalid_acceptance_plan") from None


def model_input(operation: Operation) -> dict[str, str]:
    """Only the user text; NOT an HTTP/provider payload or runtime snapshot."""
    if type(operation) is not Operation:
        raise EvaluationError("invalid_operation")
    return {"text": operation.text}


def summary(manifest: Manifest) -> dict[str, Any]:
    return {"schema_version": VERSION, "baseline_commit": manifest.baseline_commit,
            "preparation": "offline_plan_only", "real_model_calls": 0, "real_amap_calls": 0,
            "live_ready": False, "full_payload_frozen": False,
            "operation_count": len(manifest.operations), "limits": manifest.limits.model_dump(),
            "operations": [{"id": row.id, "branch": row.branch,
                            "business_text_sha256": digest(canonical(model_input(row))),
                            "expected_model_sends": 2 if row.branch == "native_candidate" else 0,
                            "hard_model_send_cap": 3 if row.branch == "native_candidate" else 0}
                           for row in manifest.operations]}


def source_hashes(root: Path = ROOT) -> dict[str, str]:
    """Bounded source closure; re-enumeration detects added/deleted source files."""
    selected = set(FIXED_SOURCES)
    for relative, extensions in SOURCE_TREES.items():
        folder = _path(root, relative)
        if not folder.is_dir():
            raise EvaluationError("source_tree_missing")
        def unavailable(_: OSError) -> None:
            raise EvaluationError("source_tree_unavailable")
        for directory, subdirs, files in os.walk(folder, followlinks=False, onerror=unavailable):
            subdirs[:] = sorted(item for item in subdirs if item != "__pycache__")
            for item in (*subdirs, *files):
                path = Path(directory) / item
                if path.is_symlink():
                    raise EvaluationError("source_symlink")
                if item in files and path.suffix in extensions:
                    selected.add(path.relative_to(root).as_posix())
    if len(selected) > 10000:
        raise EvaluationError("source_file_count_exceeded")
    result, size = {}, 0
    for relative in sorted(selected):
        raw = _read(_path(root, relative), MAX_FILE)
        size += len(raw)
        if size > MAX_TOTAL:
            raise EvaluationError("source_closure_too_large")
        result[relative] = digest(raw)
    return result


def verify(manifest_path: Path = DEFAULT_MANIFEST, *, root: Path = ROOT,
           protocol_path: Path | None = None) -> dict[str, Any]:
    try:
        relative = manifest_path.absolute().relative_to(root.absolute()).as_posix()
    except ValueError:
        raise EvaluationError("manifest_outside_repository") from None
    raw = _read(_path(root, relative), MAX_MANIFEST)
    manifest = load(_path(root, relative))
    if digest(_read(_path(root, PRIOR_MANIFEST), MAX_MANIFEST)) != manifest.prior_manifest.sha256:
        raise EvaluationError("prior_manifest_changed")
    sources = source_hashes(root)
    result = {**summary(manifest), "manifest_sha256": digest(raw), "source_file_count": len(sources)}
    if protocol_path is not None:
        try:
            frozen = Protocol.model_validate(strict_json(_read(protocol_path, MAX_PROTOCOL)))
        except EvaluationError:
            raise
        except (ValueError, TypeError, ValidationError, RecursionError):
            raise EvaluationError("invalid_offline_protocol") from None
        for path in frozen.source_files:
            _relative(path)
        if (frozen.baseline_commit != manifest.baseline_commit or frozen.manifest_path != relative
                or frozen.manifest_sha256 != digest(raw) or frozen.source_files != sources):
            raise EvaluationError("frozen_manifest_or_source_changed")
        result["protocol_sha256"] = digest(_read(protocol_path, MAX_PROTOCOL))
        result["protocol_verified"] = True
    return result


def freeze(output: Path, manifest_path: Path = DEFAULT_MANIFEST, *, root: Path = ROOT) -> dict[str, Any]:
    if not output.is_absolute() or ".." in output.parts or any(path.is_symlink() for path in (output, *output.parents)):
        raise EvaluationError("absolute_safe_output_required")
    result = verify(manifest_path, root=root)
    manifest = load(manifest_path)
    relative = manifest_path.absolute().relative_to(root.absolute()).as_posix()
    value = Protocol(schema_version=PROTOCOL_VERSION, baseline_commit=manifest.baseline_commit,
                     preparation="offline_plan_only", authorization=manifest.authorization,
                     manifest_path=relative, manifest_sha256=result["manifest_sha256"],
                     source_files=source_hashes(root), real_model_calls=0, real_amap_calls=0,
                     live_ready=False, full_payload_frozen=False)
    raw = canonical(value.model_dump(mode="json")) + b"\n"
    if len(raw) > MAX_PROTOCOL:
        raise EvaluationError("protocol_too_large")
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        raise EvaluationError("protocol_already_exists") from None
    except OSError:
        raise EvaluationError("protocol_creation_failed") from None
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        directory = os.open(output.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        # Preserve partial evidence rather than deleting/reinitializing it.
        raise EvaluationError("protocol_write_failed_preserve_file") from None
    return {**result, "protocol_sha256": digest(raw), "protocol_verified": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify", "freeze"))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "freeze":
            if args.output is None or args.protocol is not None:
                raise EvaluationError("freeze_output_required")
            result = freeze(args.output, args.manifest)
        else:
            if args.output is not None:
                raise EvaluationError("verify_does_not_write")
            result = verify(args.manifest, protocol_path=args.protocol)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except EvaluationError as error:
        parser.exit(2, f"Offline preparation stopped: {error}. No request was sent.\n")


if __name__ == "__main__":
    main()

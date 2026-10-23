"""Controlled-only preparation continuation; no sender, credentials or live grant.

The old plan/source freeze is immutable. This tool permits exactly four new
acceptance-only modules, while every old source, prompt, rule and plan must
remain unchanged. Dynamic live histories/payloads are deliberately not forged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

from tools.echomind_travel_acceptance_plan import (
    ROOT, Protocol, EvaluationError, _path, _read, _relative, canonical, load,
    source_hashes, strict_json,
)

VERSION = "echomind-travel-controlled-preparation-v1"
PARENT_SHA256 = "2ddbf306bc31e59d598dd5161603ad2ddecdcacf18dbf26047462fd753ac352e"
MODULES = tuple(f"backend/tools/echomind_travel_trial_{name}.py" for name in (
    "ledger", "transport", "capture", "prepare"))
TESTS = tuple(f"backend/tests/test_echomind_travel_trial_{name}.py" for name in (
    "ledger", "transport", "capture", "prepare"))
MAX_RECEIPT = 128 * 1024
MAX_EXECUTION = 2 * 1024 * 1024


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _document(path: Path, limit: int) -> dict[str, Any]:
    try:
        value = strict_json(_read(path, limit))
        if type(value) is not dict:
            raise ValueError()
        return value
    except EvaluationError:
        raise
    except (ValueError, TypeError, RecursionError):
        raise EvaluationError("invalid_preparation_document") from None


def transition(parent_path: Path, *, root: Path = ROOT,
               expected_parent: str = PARENT_SHA256) -> dict[str, Any]:
    """Only additive acceptance tools; never accept an old-business hash drift."""
    raw = _read(parent_path, MAX_EXECUTION)
    if _hash(raw) != expected_parent:
        raise EvaluationError("parent_protocol_changed")
    try:
        parent = Protocol.model_validate(strict_json(raw))
    except (ValueError, TypeError, RecursionError):
        raise EvaluationError("invalid_parent_protocol") from None
    _relative(parent.manifest_path)
    plan_path = _path(root, parent.manifest_path)
    if _hash(_read(plan_path, MAX_RECEIPT)) != parent.manifest_sha256:
        raise EvaluationError("frozen_plan_changed")
    plan = load(plan_path)
    if (plan.baseline_commit != parent.baseline_commit
            or _hash(_read(_path(root, plan.prior_manifest.path), MAX_RECEIPT)) != plan.prior_manifest.sha256):
        raise EvaluationError("original_manifest_changed")
    current = source_hashes(root)
    for path, digest in parent.source_files.items():
        _relative(path)
        if current.get(path) != digest:
            raise EvaluationError("frozen_business_source_changed")
    additions = set(current) - set(parent.source_files)
    if additions != set(MODULES):
        raise EvaluationError("unexpected_acceptance_source_set")
    tests = {path: _hash(_read(_path(root, path), 1024 * 1024)) for path in TESTS}
    # This one non-secret upstream knob is read, never overwritten or echoed.
    # The original per-Agent wait_for is earlier than the provider's 20s cap.
    try:
        timeout = float(os.environ.get("ECHOMIND_LLM_TIMEOUT_S", "15"))
        if not math.isfinite(timeout) or timeout != 15:
            raise ValueError()
    except (ValueError, TypeError):
        raise EvaluationError("nondefault_agent_timeout_not_frozen") from None
    return {"schema_version": VERSION, "mode": "controlled_only",
            "parent_protocol_sha256": _hash(raw), "plan_manifest_sha256": parent.manifest_sha256,
            "unchanged_parent_source_count": len(parent.source_files),
            "source_files": current, "test_files": tests,
            "added_source_files": {path: current[path] for path in MODULES},
            "effective_timeouts": {"agent_call_seconds": 15, "provider_operation_seconds": 20,
                                   "task_seconds": 45},
            "real_authorized_calls": 0, "real_model_calls": 0, "real_amap_calls": 0,
            "full_live_payload_frozen": False, "live_ready": False}


def _capture(path: Path) -> str:
    value = _document(path, MAX_RECEIPT)
    required = {"schema_version": "echomind-travel-initial-capture-v1", "mode": "controlled_capture",
                "operation_id": "m01", "real_model_calls": 0, "real_amap_calls": 0,
                "upstream_sends": 0, "captured_request_count": 1, "full_live_payload_frozen": False}
    for key, wanted in required.items():
        if type(value.get(key)) is not type(wanted) or value[key] != wanted:
            raise EvaluationError("not_a_controlled_initial_capture")
    digest = value.get("payload_sha256")
    if type(digest) is not str or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise EvaluationError("capture_payload_hash_missing")
    return _hash(_read(path, MAX_RECEIPT))


def _protected(path: Path, *, evidence_root: Path) -> str:
    """Read-only link old ledgers. Never open, initialize, or amend a live gate."""
    before = _document(path, MAX_RECEIPT)
    ledgers = before.get("old_ledgers")
    if type(ledgers) is not dict or not 1 <= len(ledgers) <= 64:
        raise EvaluationError("historical_ledger_references_required")
    for name, digest in ledgers.items():
        _relative(name)
        if type(digest) is not str or _hash(_read(_path(evidence_root, name), 8 * 1024 * 1024)) != digest:
            raise EvaluationError("historical_ledger_changed")
    return _hash(canonical(ledgers))


def _exclusive(output: Path, value: dict[str, Any]) -> None:
    if (not output.is_absolute() or ".." in output.parts
            or any(path.is_symlink() for path in (output, *output.parents))):
        raise EvaluationError("absolute_private_output_required")
    raw = canonical(value) + b"\n"
    if len(raw) > MAX_EXECUTION:
        raise EvaluationError("preparation_too_large")
    try:
        descriptor = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        raise EvaluationError("preparation_already_exists") from None
    except OSError:
        raise EvaluationError("preparation_creation_failed") from None
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        descriptor = os.open(output.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        raise EvaluationError("preparation_write_failed_preserve_file") from None


def prepare(parent_path: Path, output: Path, capture_path: Path, protected_path: Path,
            *, root: Path = ROOT, expected_parent: str = PARENT_SHA256,
            evidence_root: Path | None = None) -> dict[str, Any]:
    evidence_root = evidence_root or root.parent / "trippilot-evidence"
    value = transition(parent_path, root=root, expected_parent=expected_parent)
    value["controlled_capture_sha256"] = _capture(capture_path)
    value["prior_ledgers_sha256"] = _protected(protected_path, evidence_root=evidence_root)
    _exclusive(output, value)
    return verify_preparation(parent_path, output, capture_path, protected_path, root=root,
                              expected_parent=expected_parent, evidence_root=evidence_root)


def verify_preparation(parent_path: Path, output: Path, capture_path: Path, protected_path: Path,
                       *, root: Path = ROOT, expected_parent: str = PARENT_SHA256,
                       evidence_root: Path | None = None) -> dict[str, Any]:
    evidence_root = evidence_root or root.parent / "trippilot-evidence"
    expected = transition(parent_path, root=root, expected_parent=expected_parent)
    expected["controlled_capture_sha256"] = _capture(capture_path)
    expected["prior_ledgers_sha256"] = _protected(protected_path, evidence_root=evidence_root)
    actual = _document(output, MAX_EXECUTION)
    if canonical(actual) != canonical(expected):
        raise EvaluationError("preparation_changed")
    return {"schema_version": VERSION, "mode": "controlled_only", "verified": True,
            "execution_sha256": _hash(_read(output, MAX_EXECUTION)),
            "parent_protocol_sha256": expected_parent,
            "unchanged_parent_source_count": expected["unchanged_parent_source_count"],
            "added_source_files": list(MODULES), "real_authorized_calls": 0,
            "real_model_calls": 0, "real_amap_calls": 0,
            "full_live_payload_frozen": False, "live_ready": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("transition", "prepare", "verify"))
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--capture", type=Path)
    parser.add_argument("--protected", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "transition":
            if any((args.output, args.capture, args.protected)):
                raise EvaluationError("transition_is_read_only")
            full = transition(args.parent)
            result = {k: v for k, v in full.items() if k not in {"source_files", "test_files"}}
        else:
            if not all((args.output, args.capture, args.protected)):
                raise EvaluationError("preparation_arguments_required")
            function = prepare if args.command == "prepare" else verify_preparation
            result = function(args.parent, args.output, args.capture, args.protected)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except EvaluationError as error:
        parser.exit(2, f"Controlled preparation stopped: {error}. No real request was sent.\n")


if __name__ == "__main__":
    main()

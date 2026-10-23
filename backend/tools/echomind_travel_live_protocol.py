"""Additive live-trial freeze: no sender, environment discovery or quota reset.

The user's new bounded grant is recorded separately from immutable zero-call
preparation.  This module can freeze/verify files, never send or open a ledger.
Dynamic real history/feedback payloads still require per-send admission.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Any
from urllib.parse import urlsplit

from tools.echomind_travel_acceptance_plan import (
    ROOT, EvaluationError, _path, _read, _relative, canonical, source_hashes, strict_json,
)

VERSION = "echomind-travel-live-protocol-v1"
PARENT_PROTOCOL_SHA256 = "2ddbf306bc31e59d598dd5161603ad2ddecdcacf18dbf26047462fd753ac352e"
PARENT_EXECUTION_SHA256 = "9d2448b181eefa4e18e3af0e41d73f5de10e949f969166e562de76a724a73404"
PLAN_SHA256 = "b0a7e4e0e8d73fa1725be599583c143ab1cd5b982b18db3d747a433a545c662c"
PARENT_EXECUTION = ROOT.parent / "trippilot-evidence/echomind-travel-readiness-20261023.gdDE12/execution-v2.json"
EVIDENCE_ROOT = ROOT.parent / "trippilot-evidence"
PLAN = "backend/evaluations/echomind-travel-acceptance-plan-v1.json"
BASE_SOURCE_COUNT = 489
OLD_LEDGER_COUNT = 16
MODULES = tuple(f"backend/tools/echomind_travel_live_{name}.py" for name in ("protocol", "transport", "runner"))
TESTS = tuple(f"backend/tests/test_echomind_travel_live_{name}.py" for name in ("protocol", "transport", "runner"))
MODEL = "qwen3.7-flash-2026-07-15"
HASH = re.compile(r"[a-f0-9]{64}\Z")
KEY = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
MAX_DOCUMENT = 2 * 1024 * 1024
AUTHORIZATION = {
    "user_statement": "可以，那就开始做真实验收吧",
    "scope": "previously_discussed_seven_native_submissions_maximum_21_physical_sends",
    "max_model_sends": 21, "real_amap_calls": 0, "automatic_retry": False,
    "historical_allowances_reused": False,
}
PARAMETERS = {"model": MODEL, "max_tokens": 900, "temperature": .1, "stream": False,
              "enable_thinking": False, "parallel_tool_calls": False, "tool_choice": "auto",
              "agent_call_seconds": 15, "provider_operation_seconds": 20, "task_seconds": 45}


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _document(path: Path) -> dict[str, Any]:
    try:
        value = strict_json(_read(path, MAX_DOCUMENT))
        if type(value) is not dict:
            raise ValueError()
        return value
    except (ValueError, TypeError, RecursionError):
        raise EvaluationError("live_invalid_document") from None


def trusted_settings(endpoint: str) -> dict[str, Any]:
    """Hash only the canonical endpoint already selected by backend policy.

    This independent narrow check does not discover credentials or expand the
    backend domain policy. Public Beijing and official Beijing workspaces are
    allowed; no arbitrary URL is recorded or accepted.
    """
    try:
        parsed = urlsplit(endpoint)
        host = parsed.hostname or ""
        allowed = host == "dashscope.aliyuncs.com" or re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.cn-beijing\.maas\.aliyuncs\.com", host)
        if (type(endpoint) is not str or not allowed or parsed.scheme != "https"
                or parsed.port is not None or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment
                or endpoint != f"https://{host}/compatible-mode/v1/chat/completions"):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise EvaluationError("live_endpoint_not_official_beijing") from None
    return PARAMETERS | {"endpoint_sha256": digest(endpoint.encode())}


def _settings(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(PARAMETERS) | {"endpoint_sha256"}:
        raise EvaluationError("live_settings_not_frozen")
    for key, expected in PARAMETERS.items():
        if type(value[key]) is not type(expected) or value[key] != expected:
            raise EvaluationError("live_settings_not_frozen")
    if type(value["endpoint_sha256"]) is not str or HASH.fullmatch(value["endpoint_sha256"]) is None:
        raise EvaluationError("live_settings_not_frozen")
    return dict(value)


def _bindings(value: Any) -> dict[str, str]:
    if type(value) is not dict or not 1 <= len(value) <= 32:
        raise EvaluationError("live_hashed_fixture_binding_required")
    if any(type(key) is not str or KEY.fullmatch(key) is None
           or type(item) is not str or HASH.fullmatch(item) is None for key, item in value.items()):
        raise EvaluationError("live_hashed_fixture_binding_required")
    return dict(value)


def ledger_binding(frozen_protocol: dict[str, Any], protocol_sha256: str) -> dict[str, Any]:
    """Project an already verified protocol into the journal's label contract.

    Historical relative paths remain intact in the immutable protocol. Only
    journal labels are encoded, deterministically and without truncating their
    *values*. Path-derived labels distinguish equal-content historical files.
    This function neither verifies current sources nor opens/creates a ledger;
    callers must still run ``verify`` before every dispatch.
    """
    try:
        if (type(frozen_protocol) is not dict or type(protocol_sha256) is not str
                or HASH.fullmatch(protocol_sha256) is None
                or digest(canonical(frozen_protocol) + b"\n") != protocol_sha256
                or frozen_protocol.get("schema_version") != VERSION
                or frozen_protocol.get("parent_protocol_sha256") != PARENT_PROTOCOL_SHA256
                or frozen_protocol.get("parent_execution_sha256") != PARENT_EXECUTION_SHA256
                or canonical(frozen_protocol.get("authorization")) != canonical(AUTHORIZATION)):
            raise ValueError()
        prior = frozen_protocol.get("prior_ledger_hashes")
        if type(prior) is not dict or len(prior) != OLD_LEDGER_COUNT:
            raise ValueError()
        labels: dict[str, str] = {}
        for path, value in sorted(prior.items()):
            _relative(path)
            if type(value) is not str or HASH.fullmatch(value) is None:
                raise ValueError()
            label = "ledger_" + digest(path.encode("utf-8"))[:40]
            if label in labels:
                raise ValueError()
            labels[label] = value
        return {"grant_sha256": digest(canonical(frozen_protocol["authorization"])),
                "plan_protocol_sha256": PARENT_PROTOCOL_SHA256,
                "execution_sha256": protocol_sha256, "prior_ledger_hashes": labels}
    except (ValueError, TypeError, AttributeError, RecursionError, UnicodeError):
        raise EvaluationError("live_invalid_ledger_binding_projection") from None


def _commit() -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                            text=True, check=False, timeout=5)
    value = result.stdout.strip()
    if result.returncode or re.fullmatch(r"[a-f0-9]{40}", value) is None:
        raise EvaluationError("live_repository_commit_unavailable")
    return value


def _state(*, settings_summary: dict[str, Any], fixture_binding: dict[str, str],
           protected_path: Path, fixture_summary: Any = None,
           parent_execution: Path = PARENT_EXECUTION,
           authorization: dict[str, Any] = AUTHORIZATION) -> dict[str, Any]:
    if canonical(authorization) != canonical(AUTHORIZATION):
        raise EvaluationError("live_new_authorization_scope_required")
    raw = _read(parent_execution, MAX_DOCUMENT)
    if digest(raw) != PARENT_EXECUTION_SHA256:
        raise EvaluationError("live_parent_execution_changed")
    parent = _document(parent_execution)
    if (parent.get("parent_protocol_sha256") != PARENT_PROTOCOL_SHA256
            or parent.get("plan_manifest_sha256") != PLAN_SHA256
            or parent.get("mode") != "controlled_only"
            or parent.get("real_authorized_calls") != 0 or parent.get("real_model_calls") != 0
            or parent.get("real_amap_calls") != 0 or parent.get("live_ready") is not False
            or parent.get("full_live_payload_frozen") is not False):
        raise EvaluationError("live_parent_execution_not_zero_call")
    if digest(_read(_path(ROOT, PLAN), 128 * 1024)) != PLAN_SHA256:
        raise EvaluationError("live_frozen_plan_changed")
    old = parent.get("source_files")
    if type(old) is not dict or len(old) != BASE_SOURCE_COUNT:
        raise EvaluationError("live_parent_source_set_invalid")
    current = source_hashes(ROOT)
    if any(current.get(path) != value for path, value in old.items()):
        raise EvaluationError("live_frozen_source_changed")
    if set(current) - set(old) != set(MODULES):
        raise EvaluationError("live_unexpected_source_additions")
    old_tests = parent.get("test_files")
    if type(old_tests) is not dict or len(old_tests) != 4:
        raise EvaluationError("live_parent_test_set_invalid")
    tests = {path: digest(_read(_path(ROOT, path), 1024 * 1024)) for path in (*old_tests, *TESTS)}
    if any(tests[path] != value for path, value in old_tests.items()):
        raise EvaluationError("live_frozen_test_changed")
    protected = _document(protected_path)
    ledgers = protected.get("old_ledgers")
    if type(ledgers) is not dict or len(ledgers) != OLD_LEDGER_COUNT:
        raise EvaluationError("live_historical_ledgers_required")
    if digest(canonical(ledgers)) != parent.get("prior_ledgers_sha256"):
        raise EvaluationError("live_historical_ledger_set_changed")
    for name, value in ledgers.items():
        if (type(value) is not str or HASH.fullmatch(value) is None
                or digest(_read(_path(EVIDENCE_ROOT, name), 8 * 1024 * 1024)) != value):
            raise EvaluationError("live_historical_ledger_changed")
    try:
        summary = canonical(fixture_summary)
        if len(summary) > 48_000:
            raise ValueError()
    except (TypeError, ValueError, RecursionError):
        raise EvaluationError("live_fixture_summary_invalid") from None
    return {"schema_version": VERSION, "scope": "real_qwen_synthetic_business_first_chain_check",
        "repository_commit_at_freeze": _commit(), "parent_protocol_sha256": PARENT_PROTOCOL_SHA256,
        "parent_execution_sha256": PARENT_EXECUTION_SHA256, "plan_manifest_sha256": PLAN_SHA256,
        "authorization": dict(AUTHORIZATION), "settings_summary": _settings(settings_summary),
        "fixture_binding": _bindings(fixture_binding), "fixture_summary_sha256": digest(summary),
        "operations": [f"m{number:02}" for number in range(1, 8)], "per_operation_limit": 3,
        "local_operations": [f"l{number:02}" for number in range(1, 8)],
        "operation_dependencies": "unchanged_frozen_plan_dependencies",
        "source_files": current, "test_files": tests, "prior_ledger_hashes": ledgers,
        "real_model_calls_at_freeze": 0, "real_amap_calls": 0,
        "dynamic_payloads": "freeze_actual_hashes_and_bindings_before_each_send",
        "product_cutover": False, "semantic_quality_passed": False}


def _private_output(output: Path, *, existing: bool) -> None:
    if (not output.is_absolute() or ".." in output.parts
            or any(path.is_symlink() for path in (output, *output.parents))
            or output.resolve().is_relative_to(ROOT)):
        raise EvaluationError("live_private_external_output_required")
    try:
        info = output.parent.stat()
    except OSError:
        raise EvaluationError("live_private_external_output_required") from None
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise EvaluationError("live_private_external_output_required")
    if existing:
        try:
            info = output.lstat()
        except OSError:
            raise EvaluationError("live_private_external_output_required") from None
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise EvaluationError("live_private_external_output_required")


def _exclusive(output: Path, state: dict[str, Any]) -> None:
    _private_output(output, existing=False)
    raw = canonical(state) + b"\n"
    if len(raw) > MAX_DOCUMENT:
        raise EvaluationError("live_protocol_too_large")
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except OSError:
        raise EvaluationError("live_protocol_must_be_new") from None
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        parent = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    except OSError:
        raise EvaluationError("live_protocol_storage_failed_preserve_file") from None


def freeze(output: Path, **options: Any) -> dict[str, Any]:
    """Create once, without enabling a sender or initializing any ledger."""
    _exclusive(output, _state(**options))
    return verify(output, **options)


def verify(output: Path, **options: Any) -> dict[str, Any]:
    _private_output(output, existing=True)
    expected = _state(**options)
    actual = _document(output)
    if canonical(actual) != canonical(expected):
        raise EvaluationError("live_protocol_or_configuration_changed")
    return {"schema_version": VERSION, "verified": True,
            "protocol_sha256": digest(_read(output, MAX_DOCUMENT)),
            "parent_execution_sha256": PARENT_EXECUTION_SHA256,
            "source_count": len(expected["source_files"]), "max_model_sends": 21,
            "real_model_calls": 0, "real_amap_calls": 0, "semantic_quality_passed": False}

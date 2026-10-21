"""Opt-in Echo migration live gate; never imported by production startup.

The user-authorized batch is shared by API/Chrome and all model rounds. This
gate wraps (rather than replaces) the production durable-metered HTTP client.
No CLI, automatic initialization, model call, retry or credentials are included.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
from typing import Any, Iterator
import zlib

import httpx

from core.llm_utils import RuntimeFailure, parse_native_response, strict_json, validate_arguments
from core.usage_runtime import MeteredAsyncClient
from tooling.providers.qwen import MODEL

ROOT = Path(__file__).resolve().parents[2]
AUTHORIZATION = "echo-migration-live-20261022-8"
CLAIM = ROOT.parent / "trippilot-evidence/echomind-migration/.live-20261022-8.authorization.json"
PARAMETERS = {"model": MODEL, "stream": False, "enable_thinking": False, "max_tokens": 2048,
              "parallel_tool_calls": False, "tool_choice": "auto"}
MODEL_LIMIT = AMAP_LIMIT = 8
CONTINUATION_SCOPES = frozenset({"api02", "chrome01", "chrome02", "business_poi", "business_walking"})
CONTINUATION_FILES = frozenset({"backend/core/llm_utils.py", "backend/tools/echo_acceptance_runtime.py",
                                "backend/tests/test_echo_runtime.py", "backend/tests/test_echo_acceptance_runtime.py"})
_SCOPE: ContextVar[tuple[str, str] | None] = ContextVar("echo_live_acceptance_scope", default=None)


class AcceptanceStopped(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read(path: Path, limit: int = 4 * 1024 * 1024) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise AcceptanceStopped("unsafe_or_oversized_file")
        result = bytearray()
        while chunk := os.read(descriptor, min(65536, limit + 1 - len(result))):
            result.extend(chunk)
            if len(result) > limit:
                raise AcceptanceStopped("oversized_file")
        return bytes(result)
    finally:
        os.close(descriptor)


def _new(path: Path, raw: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        # Partial evidence is retained; it must never be deleted to recover budget.
        raise


def payload_descriptors(payload: dict) -> dict[str, str]:
    messages = payload.get("messages")
    if not isinstance(messages, list) or len(messages) < 2:
        raise AcceptanceStopped("model_payload_invalid")
    return {"system_sha256": digest(canonical(messages[0])),
            "tools_sha256": digest(canonical(payload.get("tools", []))),
            "payload_sha256": digest(canonical(payload))}


def _protocol(value: dict) -> dict:
    required = {"schema_version", "authorization_id", "source_commit", "source_files", "old_ledgers", "parameters", "scopes"}
    if (not isinstance(value, dict) or not required <= set(value)
            or value["schema_version"] != "echo-live-acceptance-v1" or value["authorization_id"] != AUTHORIZATION
            or value["parameters"] != PARAMETERS or re.fullmatch(r"[a-f0-9]{40}", value["source_commit"]) is None
            or not isinstance(value["scopes"], list) or len(value["scopes"]) > 8
            or not value["source_files"] or len(value["old_ledgers"]) < 16):
        raise AcceptanceStopped("protocol_invalid")
    scopes = value["scopes"]
    ids = [item.get("id") for item in scopes]
    if len(ids) != len(set(ids)):
        raise AcceptanceStopped("duplicate_scope")
    for item in scopes:
        if (not isinstance(item.get("id"), str) or re.fullmatch(r"[a-z][a-z0-9_]{1,39}", item["id"]) is None
                or not isinstance(item.get("route"), str) or not item["route"].startswith("/")
                or type(item.get("max_model_calls")) is not int or not 0 <= item["max_model_calls"] <= 3
                or type(item.get("max_amap_calls")) is not int or not 0 <= item["max_amap_calls"] <= 4
                or not isinstance(item.get("amap_paths"), list)
                or set(item["amap_paths"]) - {"/v5/place/text", "/v3/direction/walking"}):
            raise AcceptanceStopped("scope_invalid")
        if item["max_model_calls"]:
            if not isinstance(item.get("text"), str) or not 1 <= len(item["text"]) <= 2000:
                raise AcceptanceStopped("scope_question_invalid")
            for key in ("system_sha256", "tools_sha256"):
                if not isinstance(item.get(key), str) or re.fullmatch(r"[a-f0-9]{64}", item[key]) is None:
                    raise AcceptanceStopped("scope_payload_freeze_missing")
    if sorted(item["max_model_calls"] for item in scopes if item["max_model_calls"]) != [1, 1, 3, 3]:
        raise AcceptanceStopped("model_plan_must_be_3_1_3_1")
    if sum(item["max_amap_calls"] for item in scopes) > AMAP_LIMIT:
        raise AcceptanceStopped("amap_plan_over_budget")
    return value


def _check_hashes(protocol: dict, repository_root: Path) -> None:
    for relative, expected in protocol["source_files"].items():
        path = repository_root / relative
        if Path(relative).is_absolute() or ".." in Path(relative).parts or digest(_read(path, 8 * 1024 * 1024)) != expected:
            raise AcceptanceStopped("frozen_source_changed")
    for group in ("old_ledgers", "frozen_artifacts"):
        for absolute, expected in protocol.get(group, {}).items():
            path = Path(absolute)
            if not path.is_absolute() or digest(_read(path, 16 * 1024 * 1024)) != expected:
                raise AcceptanceStopped("prior_evidence_or_artifact_changed")


def freeze(directory: Path, protocol: dict, *, repository_root: Path = ROOT, authorization_path: Path = CLAIM) -> None:
    """Explicit, exclusive one-time preparation; never refresh mismatched hashes."""
    protocol = _protocol(protocol)
    _check_hashes(protocol, repository_root)
    directory, authorization_path = directory.resolve(), authorization_path.resolve()
    if directory.exists() or authorization_path.exists():
        raise AcceptanceStopped("batch_or_authorization_already_exists")
    if not authorization_path.parent.is_dir():
        raise AcceptanceStopped("authorization_parent_missing")
    raw = canonical(protocol)
    _new(authorization_path, canonical({"authorization_id": AUTHORIZATION, "directory": str(directory), "protocol_sha256": digest(raw)}))
    directory.mkdir(mode=0o700)
    _new(directory / "protocol.json", raw)
    _new(directory / "gate.lock", b"")
    (directory / "events").mkdir(mode=0o700)
    _new(directory / "calls.jsonl", canonical({"event": "initialized", "protocol_sha256": digest(raw)}) + b"\n")


def _journal(directory: Path, protocol_hash: str) -> list[dict]:
    raw = _read(directory / "calls.jsonl")
    if not raw.endswith(b"\n"):
        raise AcceptanceStopped("journal_incomplete")
    rows = [strict_json(line) for line in raw.splitlines()]
    if not rows or rows[0] != {"event": "initialized", "protocol_sha256": protocol_hash}:
        raise AcceptanceStopped("journal_identity")
    for index, row in enumerate(rows[1:], 1):
        if row.get("ordinal") != index or row.get("previous_sha256") != digest(canonical(rows[index - 1])):
            raise AcceptanceStopped("journal_chain")
        if _read(directory / "events" / f"{index:04}.json") != canonical(row):
            raise AcceptanceStopped("journal_mirror_changed")
    if len(list((directory / "events").iterdir())) != len(rows) - 1:
        raise AcceptanceStopped("journal_prefix_truncated_or_uncertain")
    return rows


def _continuation_manifest(directory: Path, original: bytes, manifest: dict, root: Path, *, exact_prefix: bool = False) -> dict:
    expected_keys = {"schema_version", "authorization_id", "original_protocol_sha256", "ledger_prefix_sha256",
                     "ledger_prefix_bytes", "original_pause_sha256", "source_commit", "source_files",
                     "allowed_changed_files", "allowed_scopes", "new_artifacts", "change_description"}
    if (not isinstance(manifest, dict) or set(manifest) != expected_keys
            or manifest["schema_version"] != "echo-live-continuation-v1" or manifest["authorization_id"] != AUTHORIZATION
            or manifest["original_protocol_sha256"] != digest(original)
            or re.fullmatch(r"[a-f0-9]{40}", manifest["source_commit"]) is None
            or not isinstance(manifest["change_description"], str) or not 10 <= len(manifest["change_description"]) <= 2000
            or type(manifest["ledger_prefix_bytes"]) is not int or not 0 < manifest["ledger_prefix_bytes"] <= 4 * 1024 * 1024
            or not isinstance(manifest["new_artifacts"], dict) or not manifest["new_artifacts"]
            or set(manifest["allowed_scopes"]) != CONTINUATION_SCOPES
            or len(manifest["allowed_scopes"]) != len(CONTINUATION_SCOPES)):
        raise AcceptanceStopped("continuation_manifest_invalid")
    protocol = _protocol(strict_json(original))
    if set(manifest["source_files"]) != set(protocol["source_files"]):
        raise AcceptanceStopped("continuation_source_set_changed")
    changed = {path for path in protocol["source_files"] if manifest["source_files"][path] != protocol["source_files"][path]}
    if (set(manifest["allowed_changed_files"]) != changed or not changed <= CONTINUATION_FILES
            or len(manifest["allowed_changed_files"]) != len(changed)
            or not {"backend/core/llm_utils.py", "backend/tools/echo_acceptance_runtime.py"} <= changed
            or set(protocol_scope["id"] for protocol_scope in protocol["scopes"]) != CONTINUATION_SCOPES | {"api01"}):
        raise AcceptanceStopped("continuation_change_not_allowlisted")
    pause = _read(directory / "paused.json", 4096)
    if (digest(pause) != manifest["original_pause_sha256"] or strict_json(pause) != {
            "code": "native_protocol_compatibility_review", "protocol_sha256": digest(original)}):
        raise AcceptanceStopped("continuation_original_pause_changed")
    journal = _read(directory / "calls.jsonl")
    size = manifest["ledger_prefix_bytes"]
    prefix = journal[:size]
    if (len(prefix) != size or not prefix.endswith(b"\n") or digest(prefix) != manifest["ledger_prefix_sha256"]
            or exact_prefix and len(journal) != size):
        raise AcceptanceStopped("continuation_prefix_changed")
    rows = _journal(directory, digest(original))
    prefix_rows = [strict_json(line) for line in prefix.splitlines()]
    attempts = [row for row in prefix_rows if row["event"] == "attempt"]
    if (len(attempts) != 1 or attempts[0]["scope"] != "api01" or attempts[0]["kind"] != "model"
            or not any(row["event"] == "scope_closed" and row["scope"] == "api01" for row in prefix_rows)
            or not any(row["event"] == "result" and row["attempt"] == attempts[0]["ordinal"] and row["status"] == 200
                       and row["error"] is None for row in prefix_rows)):
        raise AcceptanceStopped("continuation_prior_attempt_not_expected")
    manifest_hash = digest(canonical(manifest))
    if any(row.get("continuation_sha256") != manifest_hash for row in rows[len(prefix_rows):]):
        raise AcceptanceStopped("continuation_new_row_identity_changed")
    effective = {**protocol, "source_files": manifest["source_files"],
                 "frozen_artifacts": {**protocol.get("frozen_artifacts", {}), **manifest["new_artifacts"]}}
    if set(protocol.get("frozen_artifacts", {})) & set(manifest["new_artifacts"]):
        raise AcceptanceStopped("continuation_cannot_replace_old_artifact")
    _check_hashes(effective, root)
    return manifest


def create_continuation(directory: Path, manifest: dict, *, repository_root: Path = ROOT,
                        authorization_path: Path = CLAIM) -> Path:
    """Exclusive one-time repaired continuation, preserving the original 8 cap.

    Only the known index/diagnostic repair may differ. The first operation stays
    burned; its unused model rounds cannot be assigned to remaining scenarios.
    """
    directory = directory.resolve()
    original = _read(directory / "protocol.json")
    claim = {"authorization_id": AUTHORIZATION, "directory": str(directory), "protocol_sha256": digest(original)}
    if strict_json(_read(authorization_path)) != claim:
        raise AcceptanceStopped("authorization_identity_changed")
    _continuation_manifest(directory, original, manifest, repository_root, exact_prefix=True)
    path = directory / "continuation-v1.json"
    _new(path, canonical(manifest))
    return path


@dataclass
class _ScopeState:
    operation_id: str
    last_payload: dict | None = None
    last_response: dict | None = None


class AcceptanceGate:
    def __init__(self, directory: Path, *, repository_root: Path = ROOT, authorization_path: Path = CLAIM,
                 continuation: Path | None = None):
        self.directory, self.root, self.authorization_path = directory.resolve(), repository_root.resolve(), authorization_path.resolve()
        self.protocol_raw = _read(self.directory / "protocol.json")
        self.protocol = _protocol(strict_json(self.protocol_raw))
        self.protocol_hash = digest(self.protocol_raw)
        self.continuation_path: Path | None = None
        self.continuation: dict | None = None
        self.continuation_hash: str | None = None
        if continuation is not None:
            if continuation.resolve() != self.directory / "continuation-v1.json":
                raise AcceptanceStopped("continuation_path_invalid")
            self.continuation_path = continuation.resolve()
            raw = _read(self.continuation_path)
            self.continuation = _continuation_manifest(self.directory, self.protocol_raw, strict_json(raw), self.root)
            if canonical(self.continuation) != raw:
                raise AcceptanceStopped("continuation_not_canonical")
            self.continuation_hash = digest(raw)
        self.scopes = {item["id"]: item for item in self.protocol["scopes"]}
        self.active: dict[str, _ScopeState] = {}
        self.inflight: set[int] = set()
        self.verify()

    @contextmanager
    def _lock(self) -> Iterator[None]:
        descriptor = os.open(self.directory / "gate.lock", os.O_RDWR | os.O_NOFOLLOW)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise AcceptanceStopped("unsafe_lock")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _pause(self, code: str) -> None:
        try:
            path = "continuation-paused.json" if self.continuation is not None else "paused.json"
            _new(self.directory / path, canonical({"code": code, "protocol_sha256": self.protocol_hash,
                **({"continuation_sha256": self.continuation_hash} if self.continuation is not None else {})}))
        except FileExistsError:
            pass

    def _rows(self) -> list[dict]:
        return _journal(self.directory, self.protocol_hash)

    def _append(self, rows: list[dict], row: dict) -> None:
        row = {**row, "ordinal": len(rows), "previous_sha256": digest(canonical(rows[-1]))}
        if self.continuation is not None:
            row["continuation_sha256"] = self.continuation_hash
        # Exclusive immutable mirrors make a valid-looking ledger prefix
        # insufficient after truncation or a crash between prewrite and append.
        _new(self.directory / "events" / f"{len(rows):04}.json", canonical(row))
        descriptor = os.open(self.directory / "calls.jsonl", os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "ab") as output:
            output.write(canonical(row) + b"\n")
            output.flush()
            os.fsync(output.fileno())

    def _verify(self) -> tuple[list[dict], dict]:
        pause_path = "continuation-paused.json" if self.continuation is not None else "paused.json"
        if (self.directory / pause_path).exists():
            raise AcceptanceStopped("acceptance_paused")
        if _read(self.directory / "protocol.json") != self.protocol_raw:
            raise AcceptanceStopped("protocol_changed")
        expected = {"authorization_id": AUTHORIZATION, "directory": str(self.directory), "protocol_sha256": self.protocol_hash}
        if strict_json(_read(self.authorization_path)) != expected:
            raise AcceptanceStopped("authorization_identity_changed")
        if self.continuation is None:
            _check_hashes(self.protocol, self.root)
        else:
            raw = _read(self.continuation_path)
            if digest(raw) != self.continuation_hash:
                raise AcceptanceStopped("continuation_manifest_changed")
            _continuation_manifest(self.directory, self.protocol_raw, strict_json(raw), self.root)
        rows = self._rows()
        attempts = [row for row in rows if row["event"] == "attempt"]
        finished = {row["attempt"] for row in rows if row["event"] == "result"}
        bound = {row["scope"]: row["operation_id"] for row in rows if row["event"] == "bound"}
        closed = {row["scope"] for row in rows if row["event"] == "scope_closed"}
        pending = [row for row in attempts if row["ordinal"] not in finished and row["ordinal"] not in self.inflight]
        abandoned = [identifier for identifier in bound if identifier not in closed and identifier not in self.active]
        if pending or abandoned:
            raise AcceptanceStopped("uncertain_attempt_or_scope")
        used = {kind: sum(row["kind"] == kind for row in attempts) for kind in ("model", "amap")}
        if used["model"] > MODEL_LIMIT or used["amap"] > AMAP_LIMIT:
            raise AcceptanceStopped("counter_over_limit")
        return rows, {"model_calls": used["model"], "amap_physical_calls": used["amap"],
                      "remaining_model_calls": MODEL_LIMIT - used["model"], "remaining_amap_calls": AMAP_LIMIT - used["amap"],
                      "closed_scopes": sorted(closed), "paused": False}

    def verify(self) -> dict:
        with self._lock():
            try:
                return self._verify()[1]
            except (OSError, ValueError, TypeError, KeyError, AcceptanceStopped) as error:
                self._pause(str(error) if isinstance(error, AcceptanceStopped) else "integrity_unavailable")
                raise AcceptanceStopped("acceptance_verification_failed") from None

    @contextmanager
    def scope(self, identifier: str, operation_id: str, *, route: str | None = None, text: str | None = None) -> Iterator[None]:
        plan = self.scopes.get(identifier)
        if (plan is None or not isinstance(operation_id, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", operation_id) is None
                or route is not None and route != plan["route"] or text is not None and text != plan.get("text")):
            self._pause("unplanned_scope")
            raise AcceptanceStopped("unplanned_scope")
        if self.continuation is not None and identifier not in self.continuation["allowed_scopes"]:
            self._pause("continuation_scope_forbidden")
            raise AcceptanceStopped("continuation_scope_forbidden")
        with self._lock():
            rows, _ = self._verify()
            if any(row.get("scope") == identifier and row["event"] in {"bound", "scope_closed"} for row in rows):
                self._pause("scope_already_used")
                raise AcceptanceStopped("scope_already_used")
            self.active[identifier] = _ScopeState(operation_id)
            self._append(rows, {"event": "bound", "scope": identifier, "operation_id": operation_id})
        token = _SCOPE.set((identifier, operation_id))
        failed = False
        try:
            yield
        except BaseException:
            failed = True
            self._pause("scope_execution_exception")
            raise
        finally:
            _SCOPE.reset(token)
            with self._lock():
                rows = self._rows()
                self._append(rows, {"event": "scope_closed", "scope": identifier, "operation_id": operation_id,
                                    "state": "execution_exception" if failed else "completed"})
            self.active.pop(identifier, None)

    @contextmanager
    def request_scope(self, route: str, operation_id: str, text: str) -> Iterator[None]:
        matches = [item["id"] for item in self.scopes.values() if item["route"] == route and item.get("text") == text]
        if len(matches) != 1:
            self._pause("ambiguous_request_scope")
            raise AcceptanceStopped("ambiguous_request_scope")
        with self.scope(matches[0], operation_id, route=route, text=text):
            yield

    def reserve(self, request: httpx.Request) -> tuple[int, str, dict | None]:
        context = _SCOPE.get()
        if context is None or context[0] not in self.active or self.active[context[0]].operation_id != context[1]:
            self._pause("missing_scope")
            raise AcceptanceStopped("missing_scope")
        identifier, operation = context
        plan, state = self.scopes[identifier], self.active[identifier]
        kind, payload = "", None
        url = request.url
        if url.scheme != "https" or url.port not in {None, 443} or url.username or url.password or url.fragment:
            self._pause("external_url_invalid")
            raise AcceptanceStopped("external_url_invalid")
        if url.host == "restapi.amap.com" and request.method == "GET" and url.path in plan["amap_paths"]:
            parameters = {key: value for key, value in url.params.multi_items() if key != "key"}
            if len(parameters) != len([key for key, _ in url.params.multi_items() if key != "key"]):
                self._pause("duplicate_amap_parameter")
                raise AcceptanceStopped("duplicate_amap_parameter")
            allowlist = plan.get("amap_query_allowlist", [])
            if allowlist and not any(item["path"] == url.path and all(parameters.get(key) == value for key, value in item["params"].items()) for item in allowlist):
                self._pause("amap_query_unplanned")
                raise AcceptanceStopped("amap_query_unplanned")
            kind = "amap"
            fingerprint = digest(canonical({"method": request.method, "path": url.path, "parameters": parameters}))
        elif (request.method == "POST" and url.path == "/compatible-mode/v1/chat/completions" and not url.query
              and (url.host == "dashscope.aliyuncs.com" or re.fullmatch(
                  r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.cn-beijing\.maas\.aliyuncs\.com", url.host))):
            payload = strict_json(request.content)
            if not isinstance(payload, dict) or {key: payload.get(key) for key in PARAMETERS} != PARAMETERS or set(payload) != set(PARAMETERS) | {"messages", "tools"}:
                self._pause("model_parameters_changed")
                raise AcceptanceStopped("model_parameters_changed")
            descriptors = payload_descriptors(payload)
            if any(descriptors[key] != plan[key] for key in ("system_sha256", "tools_sha256")):
                self._pause("prompt_or_tools_changed")
                raise AcceptanceStopped("prompt_or_tools_changed")
            messages = payload["messages"]
            if state.last_payload is None:
                if len(messages) != 2 or strict_json(messages[1]["content"]).get("message") != plan["text"]:
                    self._pause("model_input_unplanned")
                    raise AcceptanceStopped("model_input_unplanned")
            else:
                previous = state.last_payload["messages"]
                selected = state.last_response.get("tool_call") if state.last_response else None
                if (selected is None or len(messages) != len(previous) + 2 or messages[:-2] != previous
                        or messages[-1].get("role") != "tool" or messages[-1].get("tool_call_id") != selected["id"]):
                    self._pause("tool_feedback_chain_changed")
                    raise AcceptanceStopped("tool_feedback_chain_changed")
                assistant = messages[-2]
                calls = assistant.get("tool_calls")
                if (set(assistant) != {"role", "content", "tool_calls"} or assistant["role"] != "assistant"
                        or assistant["content"] is not None or not isinstance(calls, list) or len(calls) != 1
                        or set(calls[0]) != {"id", "type", "function"} or calls[0]["id"] != selected["id"]
                        or calls[0]["type"] != selected["type"] or set(calls[0]["function"]) != {"name", "arguments"}
                        or calls[0]["function"]["name"] != selected["function"]["name"]
                        or canonical(strict_json(calls[0]["function"]["arguments"])) != canonical(state.last_response["validated_arguments"])):
                    self._pause("tool_feedback_arguments_changed")
                    raise AcceptanceStopped("tool_feedback_arguments_changed")
                strict_json(messages[-1]["content"])
            kind, fingerprint = "model", digest(request.content)
        else:
            self._pause("unplanned_external_service")
            raise AcceptanceStopped("unplanned_external_service")
        with self._lock():
            rows, totals = self._verify()
            prior = [row for row in rows if row["event"] == "attempt" and row["scope"] == identifier and row["kind"] == kind]
            limit = plan["max_model_calls"] if kind == "model" else plan["max_amap_calls"]
            total = totals["model_calls"] if kind == "model" else totals["amap_physical_calls"]
            if len(prior) >= limit or total >= 8 or kind == "model" and any(row["payload_sha256"] == fingerprint for row in prior):
                self._pause("attempt_limit_or_repeated_payload")
                raise AcceptanceStopped("attempt_limit_or_repeated_payload")
            ordinal = len(rows)
            self._append(rows, {"event": "attempt", "scope": identifier, "operation_id": operation,
                                "kind": kind, "round": len(prior) + 1, "payload_sha256": fingerprint})
            self.inflight.add(ordinal)
            if payload is not None:
                state.last_payload = payload
            return ordinal, kind, payload

    def complete(self, attempt: int, kind: str, payload: dict | None, *, status: int | None,
                 raw: bytes | None, elapsed_ms: int, error: str | None = None,
                 content_encoding: str | None = None) -> None:
        projection = safe_response(raw, payload, content_encoding=content_encoding) if kind == "model" and raw is not None else None
        with self._lock():
            rows = self._rows()
            if any(row["event"] == "result" and row["attempt"] == attempt for row in rows):
                return
            self._append(rows, {"event": "result", "attempt": attempt, "kind": kind, "status": status,
                                "elapsed_ms": elapsed_ms, "error": error, "projection": projection})
            self.inflight.discard(attempt)
            if error is not None or status is not None and status != 200:
                self._pause("transport_execution_failure")
        context = _SCOPE.get()
        if context and context[0] in self.active and projection is not None:
            self.active[context[0]].last_response = projection


def _diagnostic_decode(raw: bytes, encoding: str) -> bytes:
    """Bounded diagnostic-only decompression; never change the business stream."""
    if len(raw) > 32768:
        raise ValueError("compressed_body_limit")
    bits = (16 + zlib.MAX_WBITS,) if encoding == "gzip" else (zlib.MAX_WBITS, -zlib.MAX_WBITS)
    for window in bits:
        try:
            decoder = zlib.decompressobj(window)
            decoded = decoder.decompress(raw, 32769)
            if len(decoded) > 32768 or decoder.unconsumed_tail or not decoder.eof or decoder.unused_data:
                raise ValueError("decoded_body_limit_or_incomplete")
            return decoded
        except zlib.error:
            continue
    raise ValueError("compressed_body_invalid")


def safe_response(raw: bytes, payload: dict | None, *, content_encoding: str | None = None) -> dict:
    result: dict[str, Any] = {"response_sha256": digest(raw), "body_bytes": len(raw)}
    try:
        if content_encoding and content_encoding.strip().lower() != "identity":
            encoding = content_encoding.strip().lower()
            if encoding not in {"gzip", "deflate"}:
                raise ValueError("unsupported_diagnostic_encoding")
            raw = _diagnostic_decode(raw, encoding)
            result.update(content_encoding=encoding, decoded_body_bytes=len(raw))
        data = strict_json(raw)
        if isinstance(data, dict):
            for key in ("id", "model"):
                value = data.get(key)
                if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value):
                    result["request_id" if key == "id" else "model"] = value
            if isinstance(data.get("usage"), dict):
                for field, key in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens")):
                    value = data["usage"].get(field)
                    result[key] = value if type(value) is int and 0 <= value <= 1_000_000 else None
            choices = data.get("choices")
            if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict):
                reason = choices[0].get("finish_reason")
                if reason in {"stop", "tool_calls", "length", "content_filter"}:
                    result["finish_reason"] = reason
        parsed = parse_native_response(data)
        result.update({"request_id": parsed.request_id, "model": parsed.model,
                       "input_tokens": parsed.input_tokens, "output_tokens": parsed.output_tokens})
        if parsed.tool_call is not None:
            schemas = {tool["function"]["name"]: tool["function"]["parameters"] for tool in (payload or {}).get("tools", [])}
            call = parsed.tool_call
            if call.name not in schemas:
                raise RuntimeFailure("unknown_tool")
            validate_arguments(call.arguments, schemas[call.name])
            result["tool_call"] = {"id": call.id, "type": "function", "function": {"name": call.name,
                "arguments": data["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]}}
            result["validated_arguments"] = call.arguments
            actual_call = data["choices"][0]["message"]["tool_calls"][0]
            if "index" in actual_call:
                result["native_index"] = actual_call["index"]
            result["finish_reason"] = "tool_calls"
        else:
            result.update(content=parsed.content, finish_reason="stop")
    except (ValueError, TypeError, KeyError, UnicodeError, RuntimeFailure, RecursionError):
        result["validation_failure"] = "invalid_output_not_retained"
        if content_encoding and content_encoding.strip().lower() != "identity" and "decoded_body_bytes" not in result:
            result["diagnostic_error"] = "compression_invalid_unsupported_or_over_limit"
    return result


class _Capture(httpx.AsyncByteStream):
    def __init__(self, inner: httpx.AsyncByteStream, finish: Any, limit: int):
        self.inner, self.finish = inner, finish
        self.limit = limit
        self.body = bytearray()
        self.overflow, self.done = False, False

    def _complete(self, error: str | None = None) -> None:
        if not self.done:
            self.done = True
            self.finish(None if self.overflow or self.limit == 0 else bytes(self.body), error)

    async def __aiter__(self):
        try:
            async for chunk in self.inner:
                if self.limit == 0:
                    pass  # AMap bodies are neither retained nor additionally bounded here.
                elif len(self.body) + len(chunk) <= self.limit:
                    self.body.extend(chunk)
                else:
                    self.overflow = True
                yield chunk
        except BaseException:
            self._complete("stream_interrupted")
            raise
        else:
            self._complete("response_limit" if self.overflow else None)

    async def aclose(self):
        try:
            await self.inner.aclose()
        finally:
            self._complete("stream_closed_before_complete")


class GatedMeteredAsyncClient(MeteredAsyncClient):
    def __init__(self, *args: Any, gate: AcceptanceGate, **kwargs: Any):
        self.acceptance_gate = gate
        super().__init__(*args, **kwargs)

    async def send(self, request: httpx.Request, *, stream: bool = False, **kwargs: Any) -> httpx.Response:
        try:
            attempt, kind, payload = self.acceptance_gate.reserve(request)
        except AcceptanceStopped:
            raise
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            self.acceptance_gate._pause("gate_preflight_invalid_or_unavailable")
            raise AcceptanceStopped("gate_preflight_invalid_or_unavailable") from None
        started = time.monotonic()
        def finish(raw: bytes | None, error: str | None = None, status: int | None = None,
                   content_encoding: str | None = None):
            self.acceptance_gate.complete(attempt, kind, payload, status=status, raw=raw,
                                          elapsed_ms=round((time.monotonic() - started) * 1000), error=error,
                                          content_encoding=content_encoding)
        try:
            response = await super().send(request, stream=True, **kwargs)
        except BaseException:
            finish(None, "dispatch_or_transport_exception")
            raise
        if response.is_closed:
            # httpx caches already-decoded .content on a read/closed response.
            finish(response.content, status=response.status_code)
        else:
            response.stream = _Capture(response.stream, lambda raw, error: finish(raw, error, response.status_code,
                                       response.headers.get("content-encoding")),
                                       32768 if kind == "model" else 0)
        if not stream:
            try:
                await response.aread()
            except BaseException:
                await response.aclose()
                raise
        return response

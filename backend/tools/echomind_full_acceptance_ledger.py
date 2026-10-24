"""Finite full-candidate acceptance accounting; no sender or credential access.

This is a NEW grant's append-only journal, not a continuation or reset of the
old paused travel trial. Only the private-file/lock/canonical/hash primitives
are reused. Multiple sibling claims share one operation; reopening a journal
cannot reacquire that operation's in-process writer capability.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
from types import MappingProxyType
from typing import Any, Iterator
from uuid import uuid4

from tools.echomind_travel_trial_ledger import (
    Ledger as PrivateLedger, LedgerError, ZERO, _canonical, _exact, _hash,
    _integer, _lock, _pairs,
)

VERSION = "echomind-full-acceptance-journal-v1"
FILENAME = "echomind-full-acceptance.jsonl"
MAX_BYTES, MAX_LINE, MAX_RECORDS = 1024 * 1024, 16 * 1024, 512
PLAN = (("prep01", 1), ("prep02", 1), ("prep03", 1), ("index01", 1),
        ("f01", 6), ("f02", 6), ("f03", 6), ("f04", 6), ("f05", 6),
        ("f06", 10), ("f07", 16), ("f08", 6))
LIMITS = MappingProxyType(dict(PLAN))
LIMIT = sum(LIMITS.values())
_LABEL = re.compile(r"[a-z][a-z0-9_.:-]{0,63}\Z")
_SAFE_TEXT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")
_WRITER = re.compile(r"[a-f0-9]{32}\Z")
STATES = frozenset({"received", "failed", "cancelled", "unknown"})
PAUSE_REASONS = frozenset({"execution_error", "storage_failure", "version_changed",
    "authentication", "quota", "rate_limit", "timeout", "cancelled", "unknown"})
ERRORS = PAUSE_REASONS | frozenset({"connection_error", "upstream", "upstream_error",
    "configuration", "model_unavailable", "structure", "model_structure", "references", "semantic",
    "validation_error", "response_limit", "protocol", "counting", "network",
    "transport", "provider_failure", "task_budget", "task_deadline", "task_stopped",
    "unexpected"})


def _binding(value: Any) -> dict[str, Any]:
    _exact(value, {"grant_sha256", "protocol_sha256", "code_freeze_sha256", "prior_ledger_hashes"})
    for key in ("grant_sha256", "protocol_sha256", "code_freeze_sha256"):
        _hash(value[key])
    previous = value["prior_ledger_hashes"]
    if type(previous) is not dict or not 1 <= len(previous) <= 64:
        raise LedgerError("full_ledger_prior_bindings_required")
    for key, digest in previous.items():
        if type(key) is not str or _LABEL.fullmatch(key) is None:
            raise LedgerError("full_ledger_prior_label_invalid")
        _hash(digest)
    return json.loads(_canonical(value))


def _operation(value: Any) -> str:
    if type(value) is not str or value not in LIMITS:
        raise LedgerError("full_ledger_unknown_operation")
    return value


def _stage(value: Any) -> str:
    if type(value) is not str or _LABEL.fullmatch(value) is None:
        raise LedgerError("full_ledger_invalid_stage")
    return value


def _metadata(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    integers = {"status_code": (100, 599), "duration_ms": (0, 180_000),
        "input_tokens": (0, 10_000_000), "output_tokens": (0, 10_000_000),
        "total_tokens": (0, 20_000_000), "native_tool_calls": (0, 4)}
    allowed = set(integers) | {"response_sha256", "request_model", "upstream_model",
                                "request_id", "error_code"}
    if type(value) is not dict or not set(value) <= allowed:
        raise LedgerError("full_ledger_unsafe_metadata")
    for key, item in value.items():
        if key in integers:
            if item is not None:
                _integer(item, *integers[key])
        elif key == "response_sha256":
            _hash(item)
        elif key == "request_model":
            if type(item) is not str or item not in {"qwen3.7-flash-2026-07-15", "text-embedding-v4"}:
                raise LedgerError("full_ledger_unsafe_metadata")
        elif key in {"upstream_model", "request_id"}:
            if item is not None and (type(item) is not str or _SAFE_TEXT.fullmatch(item) is None):
                raise LedgerError("full_ledger_unsafe_metadata")
        elif type(item) is not str or item not in ERRORS:
            raise LedgerError("full_ledger_unsafe_metadata")
    return json.loads(_canonical(value))


class FullAcceptanceLedger(PrivateLedger):
    """One fixed 66-attempt grant; reservations never imply billable success.

    ``received`` means the bounded response-processing phase ended, not valid
    structure/business success. A response-limit error can lack the complete
    body/hash; its error metadata must not be described as complete receipt.
    Ordinary model failures can seal a failed operation and
    continue the fixed plan. Non-received outcomes and explicit execution stops
    latch pause. No recovery/refund/reopen interface exists here.
    """
    def __init__(self, path: str | Path):
        if Path(path).name != FILENAME:
            raise LedgerError("full_ledger_canonical_filename_required")
        self._writer_id, self._process_id = uuid4().hex, os.getpid()
        super().__init__(path)

    @classmethod
    def create(cls, path: str | Path, *, binding: dict[str, Any]) -> FullAcceptanceLedger:
        ledger = cls(path)
        header = {"schema_version": VERSION, "limit": LIMIT, "mode": "live",
            "real_authorized_calls": LIMIT, "binding": _binding(binding),
            "plan": [{"operation_id": op, "limit": limit} for op, limit in PLAN]}
        descriptor = None
        try:
            descriptor = os.open(ledger.path, os.O_RDWR | os.O_CREAT | os.O_EXCL
                                 | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            _lock(descriptor)
            ledger._append(descriptor, [], "header", header)
            parent = os.open(ledger.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        except FileExistsError:
            raise LedgerError("full_ledger_already_exists") from None
        except OSError:
            ledger._poisoned = True
            raise LedgerError("full_ledger_storage_failure") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
        return ledger

    @contextmanager
    def _locked(self) -> Iterator[tuple[int, list[dict[str, Any]], dict[str, Any]]]:
        if self._poisoned:
            raise LedgerError("full_ledger_storage_failure")
        self._check_parent()
        descriptor = None
        try:
            descriptor = os.open(self.path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
            _lock(descriptor)
            info, named = os.fstat(descriptor), self.path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)):
                raise LedgerError("full_ledger_unsafe_file")
            if not 1 <= info.st_size <= MAX_BYTES:
                raise LedgerError("full_ledger_invalid_size")
            raw = os.read(descriptor, MAX_BYTES + 1)
            records, state = self._replay(raw)
            yield descriptor, records, state
        except OSError:
            self._poisoned = True
            raise LedgerError("full_ledger_storage_failure") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _append(self, descriptor: int, records: list[dict[str, Any]], kind: str,
                body: dict[str, Any]) -> None:
        record = {"seq": len(records), "previous": records[-1]["sha256"] if records else ZERO,
                  "timestamp_ns": time.time_ns(), "kind": kind, "body": body}
        record["sha256"] = hashlib.sha256(_canonical(record)).hexdigest()
        line = _canonical(record) + b"\n"
        if (len(line) > MAX_LINE or len(records) >= MAX_RECORDS
                or os.fstat(descriptor).st_size + len(line) > MAX_BYTES):
            raise LedgerError("full_ledger_capacity_exceeded")
        os.lseek(descriptor, 0, os.SEEK_END)
        remaining = memoryview(line)
        try:
            while remaining:
                count = os.write(descriptor, remaining)
                if count <= 0:
                    raise OSError("write_failed")
                remaining = remaining[count:]
            os.fsync(descriptor)
        except OSError:
            # A partial or uncertain complete append is never truncated. Both
            # this instance and any new reader must stop/inspect that prefix.
            self._poisoned = True
            raise LedgerError("full_ledger_storage_failure") from None

    @staticmethod
    def _replay(raw: bytes) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if not raw.endswith(b"\n") or len(raw) > MAX_BYTES:
            raise LedgerError("full_ledger_truncated_or_oversized")
        lines = raw.splitlines(keepends=True)
        if not 1 <= len(lines) <= MAX_RECORDS or any(len(line) > MAX_LINE for line in lines):
            raise LedgerError("full_ledger_invalid_record_limit")
        records: list[dict[str, Any]] = []
        state: dict[str, Any] = {"operations": {}, "attempts": {}, "active": None, "paused": False}
        for index, line in enumerate(lines):
            try:
                row = json.loads(line, object_pairs_hook=_pairs,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            except (ValueError, UnicodeError, RecursionError):
                raise LedgerError("full_ledger_invalid_json") from None
            _exact(row, {"seq", "previous", "timestamp_ns", "kind", "body", "sha256"})
            _integer(row["seq"], index, index)
            _integer(row["timestamp_ns"], 1, 2**63 - 1)
            unsigned = {key: value for key, value in row.items() if key != "sha256"}
            if (row["previous"] != (records[-1]["sha256"] if records else ZERO)
                    or row["sha256"] != hashlib.sha256(_canonical(unsigned)).hexdigest()
                    or line != _canonical(row) + b"\n"):
                raise LedgerError("full_ledger_hashchain_mismatch")
            if index == 0:
                expected = {"schema_version": VERSION, "limit": LIMIT, "mode": "live",
                    "real_authorized_calls": LIMIT,
                    "plan": [{"operation_id": op, "limit": limit} for op, limit in PLAN]}
                _exact(row["body"], set(expected) | {"binding"})
                if row["kind"] != "header" or any(_canonical(row["body"][key]) != _canonical(value)
                                                    for key, value in expected.items()):
                    raise LedgerError("full_ledger_invalid_header")
                _binding(row["body"]["binding"])
                state["header"] = row["body"]
            else:
                FullAcceptanceLedger._transition(state, row["kind"], row["body"])
            records.append(row)
        return records, state

    @staticmethod
    def _transition(state: dict[str, Any], kind: Any, body: Any) -> None:
        operations, attempts = state["operations"], state["attempts"]
        if kind == "pause":
            _exact(body, {"reason"})
            if type(body["reason"]) is not str or body["reason"] not in PAUSE_REASONS:
                raise LedgerError("full_ledger_invalid_pause")
            state["paused"] = True
            return
        if state["paused"] and kind != "finish":
            raise LedgerError("full_ledger_paused")
        if kind == "start":
            _exact(body, {"op_id", "binding_sha256", "writer_id"})
            op = _operation(body["op_id"])
            _hash(body["binding_sha256"])
            if type(body["writer_id"]) is not str or _WRITER.fullmatch(body["writer_id"]) is None:
                raise LedgerError("full_ledger_writer_invalid")
            if (state["active"] is not None or len(operations) >= len(PLAN)
                    or op != PLAN[len(operations)][0] or op in operations):
                raise LedgerError("full_ledger_operation_not_startable")
            operations[op] = {"binding_sha256": body["binding_sha256"], "writer_id": body["writer_id"],
                               "attempts": 0, "sealed": False, "business_outcome": None}
            state["active"] = op
        elif kind == "claim":
            _exact(body, {"op_id", "binding_sha256", "writer_id", "stage", "payload_sha256", "attempt_id", "send_index"})
            op, stage = _operation(body["op_id"]), _stage(body["stage"])
            _hash(body["binding_sha256"])
            _hash(body["payload_sha256"])
            operation = operations.get(op)
            if (state["active"] != op or operation is None or operation["sealed"]
                    or body["binding_sha256"] != operation["binding_sha256"]
                    or body["writer_id"] != operation["writer_id"]):
                raise LedgerError("full_ledger_binding_or_writer_changed")
            number = _integer(body["send_index"], 1, LIMITS[op])
            if number != operation["attempts"] + 1 or len(attempts) >= LIMIT:
                raise LedgerError("full_ledger_budget_exhausted")
            identifier = f"{op}-a{number:02}"
            if body["attempt_id"] != identifier or identifier in attempts:
                raise LedgerError("full_ledger_duplicate_attempt")
            if any(item["op_id"] == op and item["stage"] == stage
                   and item["payload_sha256"] == body["payload_sha256"] for item in attempts.values()):
                raise LedgerError("full_ledger_payload_already_attempted")
            operation["attempts"] = number
            attempts[identifier] = {"op_id": op, "stage": stage, "state": "pending",
                "payload_sha256": body["payload_sha256"], "metadata": {}}
        elif kind == "finish":
            _exact(body, {"attempt_id", "state", "metadata"})
            identifier, outcome = body["attempt_id"], body["state"]
            if type(identifier) is not str or type(outcome) is not str or outcome not in STATES:
                raise LedgerError("full_ledger_invalid_outcome")
            _metadata(body["metadata"])
            attempted = attempts.get(identifier)
            if attempted is None or attempted["state"] != "pending":
                raise LedgerError("full_ledger_outcome_not_pending")
            attempted["state"], attempted["metadata"] = outcome, body["metadata"]
            if outcome != "received":
                state["paused"] = True
        elif kind == "seal":
            _exact(body, {"op_id", "business_outcome", "writer_id"})
            op = _operation(body["op_id"])
            operation = operations.get(op)
            if (state["active"] != op or operation is None or operation["sealed"]
                    or body["writer_id"] != operation["writer_id"]
                    or type(body["business_outcome"]) is not str
                    or body["business_outcome"] not in {"completed", "failed"}
                    or any(item["op_id"] == op and item["state"] == "pending" for item in attempts.values())):
                raise LedgerError("full_ledger_operation_not_sealable")
            operation["sealed"], operation["business_outcome"] = True, body["business_outcome"]
            state["active"] = None
        else:
            raise LedgerError("full_ledger_unknown_event")

    def _owned(self, state: dict[str, Any], op: str) -> None:
        operation = state["operations"].get(op)
        if (self._process_id != os.getpid() or operation is None
                or operation["writer_id"] != self._writer_id):
            raise LedgerError("full_ledger_recovery_audit_required")

    def start_operation(self, op_id: str, binding_sha256: str) -> None:
        if self._process_id != os.getpid():
            raise LedgerError("full_ledger_recovery_audit_required")
        self._event("start", {"op_id": _operation(op_id), "binding_sha256": _hash(binding_sha256),
                              "writer_id": self._writer_id})

    def claim(self, op_id: str, stage: str, payload_sha256: str, *, binding_sha256: str) -> str:
        op_id, stage = _operation(op_id), _stage(stage)
        with self._locked() as (descriptor, records, state):
            self._owned(state, op_id)
            number = state["operations"][op_id]["attempts"] + 1
            identifier = f"{op_id}-a{number:02}"
            body = {"op_id": op_id, "stage": stage, "payload_sha256": _hash(payload_sha256),
                "binding_sha256": _hash(binding_sha256), "writer_id": self._writer_id,
                "attempt_id": identifier, "send_index": number}
            self._transition(state, "claim", body)
            self._append(descriptor, records, "claim", body)
            return identifier

    def finish(self, attempt_id: str, state: str, metadata: dict[str, Any] | None = None) -> None:
        with self._locked() as (descriptor, records, current):
            attempted = current["attempts"].get(attempt_id) if type(attempt_id) is str else None
            if attempted is None:
                raise LedgerError("full_ledger_outcome_not_pending")
            self._owned(current, attempted["op_id"])
            body = {"attempt_id": attempt_id, "state": state, "metadata": _metadata(metadata)}
            self._transition(current, "finish", body)
            self._append(descriptor, records, "finish", body)

    def seal_operation(self, op_id: str, *, business_outcome: str = "completed") -> None:
        op_id = _operation(op_id)
        with self._locked() as (descriptor, records, state):
            self._owned(state, op_id)
            body = {"op_id": op_id, "business_outcome": business_outcome, "writer_id": self._writer_id}
            self._transition(state, "seal", body)
            self._append(descriptor, records, "seal", body)

    def pause(self, reason: str = "execution_error") -> None:
        with self._locked() as (descriptor, records, state):
            body = {"reason": reason}
            self._transition(state, "pause", body)
            # Repeated cancellation/pause is idempotent and cannot reopen work.
            if not any(row["kind"] == "pause" and row["body"] == body for row in records):
                self._append(descriptor, records, "pause", body)

    def verify(self, *, binding: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._locked() as (_, records, state):
            if binding is not None and _binding(binding) != state["header"]["binding"]:
                raise LedgerError("full_ledger_execution_binding_changed")
            counts = {name: sum(item["state"] == name for item in state["attempts"].values())
                      for name in ("pending", "received", "failed", "cancelled", "unknown")}
            active = state["active"]
            owned = (active is None or self._process_id == os.getpid()
                     and state["operations"][active]["writer_id"] == self._writer_id)
            return {"schema_version": VERSION, "mode": "live", "limit": LIMIT,
                "real_authorized_calls": LIMIT, "reserved_attempts": len(state["attempts"]),
                "remaining_attempts": LIMIT - len(state["attempts"]), "counts": counts,
                "pending": bool(counts["pending"]), "paused": state["paused"],
                "recovery_required": active is not None and not owned,
                "active_operation": active, "operations": state["operations"],
                "attempts": state["attempts"], "binding": state["header"]["binding"],
                "records": len(records), "ledger_sha256": hashlib.sha256(
                    b"".join(_canonical(row) + b"\n" for row in records)).hexdigest()}

    summary = verify

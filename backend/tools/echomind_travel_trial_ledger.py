"""Private, append-only admission journal for OFFLINE travel-trial preparation.

This is not product quota storage or live-call authorization.  Creation only
admits ``controlled`` mode; a future live runner needs separately reviewed
authorization/admission.  No environment, provider or business store is read.
An unresolved claim survives restart and blocks *all* subsequent claims.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
from typing import Any, Iterator

VERSION = "echomind-travel-trial-journal-v1"
MAX_BYTES = 256 * 1024
MAX_LINE = 16 * 1024
MAX_RECORDS = 128
LOCK_WAIT_SECONDS = 0.5
OPERATIONS = frozenset(f"m{number:02}" for number in range(1, 8))
HASH = re.compile(r"[a-f0-9]{64}\Z")
LABEL = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
ZERO = "0" * 64
STATES = frozenset({"received", "failed", "cancelled", "unknown"})
MODEL = "qwen3.7-flash-2026-07-15"
ERRORS = frozenset({"controlled_failure", "connection_error", "timeout", "cancelled",
                    "upstream_error", "validation_error", "unknown"})


class LedgerError(RuntimeError):
    """Fixed safe code only: never include caller data or OS exception text."""


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("ascii")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise LedgerError("ledger_invalid_value") from None


def _hash(value: Any) -> str:
    if type(value) is not str or HASH.fullmatch(value) is None:
        raise LedgerError("ledger_invalid_hash")
    return value


def _exact(value: Any, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise LedgerError("ledger_invalid_structure")
    return value


def _integer(value: Any, lower: int, upper: int) -> int:
    if type(value) is not int or not lower <= value <= upper:
        raise LedgerError("ledger_invalid_integer")
    return value


def _op(value: Any) -> str:
    if type(value) is not str or value not in OPERATIONS:
        raise LedgerError("ledger_unknown_operation")
    return value


def _binding(value: Any) -> dict[str, Any]:
    _exact(value, {"plan_protocol_sha256", "execution_sha256", "prior_ledger_hashes"})
    _hash(value["plan_protocol_sha256"])
    _hash(value["execution_sha256"])
    previous = value["prior_ledger_hashes"]
    if type(previous) is not dict or not 1 <= len(previous) <= 64:
        raise LedgerError("ledger_invalid_prior_bindings")
    for key, digest in previous.items():
        if type(key) is not str or LABEL.fullmatch(key) is None:
            raise LedgerError("ledger_invalid_prior_label")
        _hash(digest)
    return json.loads(_canonical(value))


def _metadata(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    integer_limits = {"status_code": (100, 599), "duration_ms": (0, 120_000),
                      "input_tokens": (0, 10_000_000), "output_tokens": (0, 10_000_000),
                      "native_tool_calls": (0, 4)}
    allowed = set(integer_limits) | {"response_sha256", "request_model", "upstream_model", "error_code"}
    if type(value) is not dict or not set(value) <= allowed:
        raise LedgerError("ledger_unsafe_metadata")
    for key, item in value.items():
        if key in integer_limits:
            if item is not None:
                _integer(item, *integer_limits[key])
        elif key == "response_sha256":
            _hash(item)
        elif key in {"request_model", "upstream_model"}:
            if type(item) is not str or item not in {MODEL, "unknown"}:
                raise LedgerError("ledger_unsafe_metadata")
        elif type(item) is not str or item not in ERRORS:
            raise LedgerError("ledger_unsafe_metadata")
    return dict(value)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LedgerError("ledger_duplicate_key")
        result[key] = value
    return result


def _lock(descriptor: int) -> None:
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise LedgerError("ledger_lock_busy") from None
            time.sleep(0.01)


class Ledger:
    """A fixed seven-operation controlled trial; never refunds or reopens ops.

    ``received`` only means a complete HTTP response, not a passing answer.
    Structural/semantic grades belong to the separate evaluation report.
    Execution failure or unknown dispatch pauses the entire immutable journal.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._poisoned = False
        self._check_parent()

    def _check_parent(self) -> None:
        if not self.path.is_absolute() or self.path.name in {"", ".", ".."} or ".." in self.path.parts:
            raise LedgerError("ledger_absolute_private_path_required")
        try:
            for parent in self.path.parents:
                if stat.S_ISLNK(parent.lstat().st_mode):
                    raise LedgerError("ledger_symlink_rejected")
            info = self.path.parent.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) != 0o700):
                raise LedgerError("ledger_private_parent_required")
        except OSError:
            raise LedgerError("ledger_path_unavailable") from None

    @classmethod
    def create(cls, path: str | Path, *, binding: dict[str, Any],
               mode: str = "controlled", limit: int = 21) -> "Ledger":
        if mode != "controlled":
            raise LedgerError("ledger_live_admission_not_implemented")
        header = {"schema_version": VERSION, "mode": mode, "limit": _integer(limit, 1, 21),
                  "binding": _binding(binding), "real_authorized_calls": 0, "real_calls": 0}
        ledger = cls(path)
        descriptor: int | None = None
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
            raise LedgerError("ledger_already_exists") from None
        except OSError:
            ledger._poisoned = True
            raise LedgerError("ledger_storage_failure") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
        return ledger

    @contextmanager
    def _locked(self) -> Iterator[tuple[int, list[dict[str, Any]], dict[str, Any]]]:
        if self._poisoned:
            raise LedgerError("ledger_storage_failure")
        self._check_parent()
        descriptor: int | None = None
        try:
            descriptor = os.open(self.path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
            _lock(descriptor)
            info = os.fstat(descriptor)
            named = self.path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)):
                raise LedgerError("ledger_unsafe_file")
            if not 1 <= info.st_size <= MAX_BYTES:
                raise LedgerError("ledger_invalid_size")
            raw = os.read(descriptor, MAX_BYTES + 1)
            records, state = self._replay(raw)
            yield descriptor, records, state
        except OSError:
            self._poisoned = True
            raise LedgerError("ledger_storage_failure") from None
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
            raise LedgerError("ledger_capacity_exceeded")
        os.lseek(descriptor, 0, os.SEEK_END)
        offset = 0
        try:
            while offset < len(line):
                written = os.write(descriptor, line[offset:])
                if written <= 0:
                    raise OSError("write_failed")
                offset += written
            os.fsync(descriptor)
        except OSError:
            self._poisoned = True
            # No truncation/rollback: a persisted claim remains occupied.  A
            # complete-but-unsynced finish is followed by a best-effort sticky
            # stop.  The caller must stop; this never grants permission to retry.
            if kind != "pause" and offset in {0, len(line)}:
                try:
                    prefix = records + [record] if offset else records
                    self._append(descriptor, prefix, "pause", {"reason": "storage_failure"})
                except (OSError, LedgerError):
                    pass
            raise LedgerError("ledger_storage_failure") from None

    @staticmethod
    def _replay(raw: bytes) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if not raw.endswith(b"\n") or len(raw) > MAX_BYTES:
            raise LedgerError("ledger_truncated_or_oversized")
        lines = raw.splitlines(keepends=True)
        if not 1 <= len(lines) <= MAX_RECORDS or any(len(line) > MAX_LINE for line in lines):
            raise LedgerError("ledger_invalid_record_limit")
        records: list[dict[str, Any]] = []
        state: dict[str, Any] = {"operations": {}, "attempts": {}, "active": None, "paused": False}
        for number, line in enumerate(lines):
            try:
                record = json.loads(line, object_pairs_hook=_pairs,
                                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            except (ValueError, UnicodeError, RecursionError):
                raise LedgerError("ledger_invalid_json") from None
            _exact(record, {"seq", "previous", "timestamp_ns", "kind", "body", "sha256"})
            _integer(record["seq"], number, number)
            _integer(record["timestamp_ns"], 1, 2**63 - 1)
            expected_previous = records[-1]["sha256"] if records else ZERO
            unsigned = {key: value for key, value in record.items() if key != "sha256"}
            if (record["previous"] != expected_previous or record["sha256"] != hashlib.sha256(_canonical(unsigned)).hexdigest()
                    or line != _canonical(record) + b"\n"):
                raise LedgerError("ledger_hashchain_mismatch")
            kind, body = record["kind"], record["body"]
            if number == 0:
                _exact(body, {"schema_version", "mode", "limit", "binding", "real_authorized_calls", "real_calls"})
                if (kind != "header" or body["schema_version"] != VERSION or body["mode"] != "controlled"
                        or type(body["real_authorized_calls"]) is not int or body["real_authorized_calls"] != 0
                        or type(body["real_calls"]) is not int or body["real_calls"] != 0):
                    raise LedgerError("ledger_invalid_header")
                _integer(body["limit"], 1, 21)
                _binding(body["binding"])
                state["header"] = body
            elif kind == "pause":
                _exact(body, {"reason"})
                if (type(body["reason"]) is not str
                        or body["reason"] not in {"storage_failure", "controlled_execution_error"}):
                    raise LedgerError("ledger_invalid_pause")
                state["paused"] = True
            else:
                Ledger._transition(state, kind, body)
            records.append(record)
        return records, state

    @staticmethod
    def _transition(state: dict[str, Any], kind: Any, body: Any) -> None:
        # A stop forbids new dispatches, not truthful completion of an already
        # occupied attempt (for example, one concurrent request caused pause).
        # No outcome below clears the pause latch or refunds the reservation.
        if state["paused"] and kind != "finish":
            raise LedgerError("ledger_paused")
        operations, attempts = state["operations"], state["attempts"]
        pending = any(item["state"] == "pending" for item in attempts.values())
        if kind == "start":
            _exact(body, {"op_id", "binding_sha256"})
            op_id = _op(body["op_id"])
            _hash(body["binding_sha256"])
            if (pending or state["active"] is not None or op_id in operations
                    or op_id != f"m{len(operations) + 1:02}"):
                raise LedgerError("ledger_operation_not_startable")
            operations[op_id] = {"binding_sha256": body["binding_sha256"], "attempts": 0, "sealed": False}
            state["active"] = op_id
        elif kind == "claim":
            _exact(body, {"op_id", "binding_sha256", "payload_sha256", "attempt_id", "send_index"})
            op_id = _op(body["op_id"])
            _hash(body["binding_sha256"])
            _hash(body["payload_sha256"])
            operation = operations.get(op_id)
            if (pending or state["active"] != op_id or operation is None or operation["sealed"]
                    or body["binding_sha256"] != operation["binding_sha256"]):
                raise LedgerError("ledger_pending_or_binding_changed")
            if any(row["op_id"] == op_id and row["payload_sha256"] == body["payload_sha256"]
                   for row in attempts.values()):
                raise LedgerError("ledger_payload_already_attempted")
            index = _integer(body["send_index"], 1, 3)
            if index != operation["attempts"] + 1 or len(attempts) >= state["header"]["limit"]:
                raise LedgerError("ledger_budget_exhausted")
            if body["attempt_id"] != f"{op_id}-a{index:02}" or body["attempt_id"] in attempts:
                raise LedgerError("ledger_duplicate_attempt")
            operation["attempts"] = index
            attempts[body["attempt_id"]] = {"op_id": op_id, "state": "pending",
                                           "payload_sha256": body["payload_sha256"]}
        elif kind == "finish":
            _exact(body, {"attempt_id", "state", "metadata"})
            attempt_id, result = body["attempt_id"], body["state"]
            if type(attempt_id) is not str or type(result) is not str or result not in STATES:
                raise LedgerError("ledger_invalid_outcome")
            _metadata(body["metadata"])
            attempt = attempts.get(attempt_id)
            if attempt is None or attempt["state"] != "pending":
                raise LedgerError("ledger_outcome_not_pending")
            attempt["state"] = result
            if result != "received":
                state["paused"] = True
        elif kind == "seal":
            _exact(body, {"op_id"})
            op_id = _op(body["op_id"])
            if pending or state["active"] != op_id or operations[op_id]["sealed"]:
                raise LedgerError("ledger_operation_not_sealable")
            operations[op_id]["sealed"] = True
            state["active"] = None
        else:
            raise LedgerError("ledger_unknown_event")

    def _event(self, kind: str, body: dict[str, Any]) -> None:
        with self._locked() as (descriptor, records, state):
            self._transition(state, kind, body)
            self._append(descriptor, records, kind, body)

    def start_operation(self, op_id: str, binding_sha256: str) -> None:
        self._event("start", {"op_id": _op(op_id), "binding_sha256": _hash(binding_sha256)})

    def claim(self, op_id: str, payload_sha256: str, *, binding_sha256: str) -> str:
        with self._locked() as (descriptor, records, state):
            operation = state["operations"].get(_op(op_id))
            if operation is None:
                raise LedgerError("ledger_operation_not_started")
            index = operation["attempts"] + 1
            attempt_id = f"{op_id}-a{index:02}"
            body = {"op_id": op_id, "binding_sha256": _hash(binding_sha256),
                    "payload_sha256": _hash(payload_sha256), "send_index": index, "attempt_id": attempt_id}
            self._transition(state, "claim", body)
            self._append(descriptor, records, "claim", body)
            return attempt_id

    def finish(self, attempt_id: str, state: str, metadata: dict[str, Any] | None = None) -> None:
        self._event("finish", {"attempt_id": attempt_id, "state": state, "metadata": _metadata(metadata)})

    def seal_operation(self, op_id: str) -> None:
        self._event("seal", {"op_id": _op(op_id)})

    def pause(self) -> None:
        """Persist an execution stop even when admission failed before claim.

        The reason is fixed, not caller text. Already-paused journals remain
        unchanged; no pending attempt is refunded, resolved or made retryable.
        """
        with self._locked() as (descriptor, records, state):
            if not state["paused"]:
                self._append(descriptor, records, "pause", {"reason": "controlled_execution_error"})

    def verify(self, *, binding: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._locked() as (_, records, state):
            header = state["header"]
            if binding is not None and _binding(binding) != header["binding"]:
                raise LedgerError("ledger_execution_binding_changed")
            counts = {name: sum(row["state"] == name for row in state["attempts"].values())
                      for name in ("pending", "received", "failed", "cancelled", "unknown")}
            return {"schema_version": VERSION, "mode": header["mode"], "limit": header["limit"],
                    "real_authorized_calls": 0, "real_calls": 0,
                    "controlled_attempts": len(state["attempts"]), "counts": counts,
                    "pending": bool(counts["pending"]), "paused": state["paused"],
                    "active_operation": state["active"], "operations": state["operations"],
                    "binding": header["binding"], "records": len(records),
                    "ledger_sha256": hashlib.sha256(b"".join(_canonical(row) + b"\n" for row in records)).hexdigest()}

    summary = verify

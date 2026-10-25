"""Fixed ServeFlow trial admission, not product quota or reusable paid grants.

Only standard-library journal primitives are reused. No provider, environment,
database or network is read. An attempt is durably occupied before dispatch.
The original trial journals and authorization are never opened or modified.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
from typing import Any

from tools.echomind_travel_trial_ledger import (
    Ledger as _Journal, LedgerError, MAX_BYTES, MAX_LINE, MAX_RECORDS, ZERO,
    _canonical, _exact, _hash, _integer, _lock, _pairs,
)

VERSION = "serveflow-live-trial-journal-v1"
MODEL = "qwen3.7-flash-2026-07-15"
CASES = ("greeting", "split_order", "refund", "policy")
LIMIT = 20
STATES = frozenset({"received", "failed", "cancelled", "unknown"})
ERRORS = frozenset({"network_error", "connection_error", "transport_error", "timeout", "cancelled", "unknown",
                    "authentication_error", "rate_limited", "upstream_error", "validation_error",
                    "storage_failure", "version_mismatch", "controlled_failure"})


def _binding(value: Any) -> dict[str, Any]:
    _exact(value, {"protocol_sha256", "source_hashes"})
    _hash(value["protocol_sha256"])
    sources = value["source_hashes"]
    if type(sources) is not dict or not 1 <= len(sources) <= 128:
        raise LedgerError("ledger_invalid_source_binding")
    for label, digest in sources.items():
        if (type(label) is not str or re.fullmatch(r"[A-Za-z0-9_./-]{1,240}", label) is None
                or label.startswith("/") or ".." in label.split("/")):
            raise LedgerError("ledger_invalid_source_label")
        _hash(digest)
    return json.loads(_canonical(value))


def _metadata(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    limits = {"status_code": (100, 599), "duration_ms": (0, 300_000),
              "input_tokens": (0, 10_000_000), "output_tokens": (0, 10_000_000)}
    if (type(value) is not dict or not set(value) <= set(limits)
            | {"response_sha256", "request_model", "upstream_model", "error_code"}):
        raise LedgerError("ledger_unsafe_metadata")
    for key, item in value.items():
        if key in limits:
            if item is not None:
                _integer(item, *limits[key])
        elif key == "response_sha256":
            _hash(item)
        elif key == "request_model":
            if item is not None and (type(item) is not str or item not in {MODEL, "unknown"}):
                raise LedgerError("ledger_unsafe_metadata")
        elif key == "upstream_model":
            if item is not None and (type(item) is not str
                    or re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", item) is None):
                raise LedgerError("ledger_unsafe_metadata")
        elif type(item) is not str or item not in ERRORS:
            raise LedgerError("ledger_unsafe_metadata")
    return dict(value)


class Ledger(_Journal):
    """One immutable four-case batch with at most twenty physical sends.

    A case's dispatch capability stays only in the creating instance. A restarted
    process can inspect an unfinished case but cannot reopen it or issue sends.
    Multiple threads may claim within that live case; file locking serializes
    all state changes, including across independent processes.
    """

    def __init__(self, path: str | Path):
        super().__init__(path)
        self._live_cases: dict[str, str] = {}
        self._verified_binding: dict[str, Any] | None = None

    @classmethod
    def create(cls, path: str | Path, *, binding: dict[str, Any]) -> "Ledger":
        header = {"schema_version": VERSION, "limit": LIMIT, "cases": list(CASES),
                  "binding": _binding(binding)}
        ledger = cls(path)
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
            raise LedgerError("ledger_already_exists") from None
        except OSError:
            ledger._poisoned = True
            raise LedgerError("ledger_storage_failure") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
        ledger._verified_binding = _binding(binding)
        return ledger

    @staticmethod
    def _replay(raw: bytes) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if not raw.endswith(b"\n") or len(raw) > MAX_BYTES:
            raise LedgerError("ledger_truncated_or_oversized")
        lines = raw.splitlines(keepends=True)
        if not 1 <= len(lines) <= MAX_RECORDS or any(len(line) > MAX_LINE for line in lines):
            raise LedgerError("ledger_invalid_record_limit")
        records = []
        state: dict[str, Any] = {"cases": {}, "active": None, "attempts": {}, "paused": False}
        for number, line in enumerate(lines):
            try:
                record = json.loads(line, object_pairs_hook=_pairs,
                                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            except (ValueError, UnicodeError, RecursionError):
                raise LedgerError("ledger_invalid_json") from None
            _exact(record, {"seq", "previous", "timestamp_ns", "kind", "body", "sha256"})
            _integer(record["seq"], number, number)
            _integer(record["timestamp_ns"], 1, 2**63 - 1)
            unsigned = {key: value for key, value in record.items() if key != "sha256"}
            if (record["previous"] != (records[-1]["sha256"] if records else ZERO)
                    or record["sha256"] != hashlib.sha256(_canonical(unsigned)).hexdigest()
                    or line != _canonical(record) + b"\n"):
                raise LedgerError("ledger_hashchain_mismatch")
            kind, body = record["kind"], record["body"]
            if number == 0:
                _exact(body, {"schema_version", "limit", "cases", "binding"})
                if (kind != "header" or body["schema_version"] != VERSION
                        or type(body["limit"]) is not int or body["limit"] != LIMIT
                        or body["cases"] != list(CASES)):
                    raise LedgerError("ledger_invalid_header")
                _binding(body["binding"])
                state["header"] = body
            else:
                Ledger._transition(state, kind, body)
            records.append(record)
        return records, state

    @staticmethod
    def _transition(state: dict[str, Any], kind: str, body: Any) -> None:
        if kind == "pause":
            _exact(body, {"reason"})
            if type(body["reason"]) is not str or body["reason"] not in ERRORS:
                raise LedgerError("ledger_invalid_pause")
            state["paused"] = True
            return
        if kind == "begin":
            _exact(body, {"case", "capability_sha256"})
            index = len(state["cases"])
            if (state["paused"] or state["active"] is not None or index >= len(CASES)
                    or body["case"] != CASES[index]):
                raise LedgerError("ledger_case_not_admissible")
            _hash(body["capability_sha256"])
            state["cases"][body["case"]] = {"status": "active", "capability_sha256": body["capability_sha256"]}
            state["active"] = body["case"]
        elif kind == "claim":
            _exact(body, {"attempt_id", "case", "payload_sha256", "model"})
            expected = f"{body['case']}:{len(state['attempts']) + 1:02}"
            if (state["paused"] or state["active"] != body["case"] or body["attempt_id"] != expected
                    or len(state["attempts"]) >= LIMIT or body["model"] != MODEL):
                raise LedgerError("ledger_attempt_not_admissible")
            _hash(body["payload_sha256"])
            state["attempts"][body["attempt_id"]] = {**body, "state": "pending"}
        elif kind == "settle":
            _exact(body, {"attempt_id", "state", "metadata"})
            if type(body["attempt_id"]) is not str or type(body["state"]) is not str:
                raise LedgerError("ledger_invalid_settlement")
            attempt = state["attempts"].get(body["attempt_id"])
            if attempt is None or attempt["state"] != "pending" or body["state"] not in STATES:
                raise LedgerError("ledger_attempt_already_settled_or_missing")
            _metadata(body["metadata"])
            attempt.update(body)
            if body["state"] != "received" or body["metadata"].get("status_code") in {401, 403, 429}:
                state["paused"] = True
        elif kind == "end":
            _exact(body, {"case"})
            if state["active"] != body["case"] or any(
                    attempt["case"] == body["case"] and attempt["state"] == "pending"
                    for attempt in state["attempts"].values()):
                raise LedgerError("ledger_unsettled_case")
            state["cases"][body["case"]]["status"] = "finished"
            state["active"] = None
        else:
            raise LedgerError("ledger_unknown_record")

    def _bound(self, state: dict[str, Any]) -> None:
        if self._verified_binding is None or state["header"]["binding"] != self._verified_binding:
            raise LedgerError("ledger_binding_not_verified")

    def _live(self, state: dict[str, Any], name: str) -> None:
        self._bound(state)
        capability = self._live_cases.get(name)
        if (capability is None or state["active"] != name
                or hashlib.sha256(capability.encode("ascii")).hexdigest()
                != state["cases"][name]["capability_sha256"]):
            raise LedgerError("ledger_case_not_owned_by_live_runner")

    @staticmethod
    def _summary(records: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, Any]:
        return {"version": VERSION, "limit": LIMIT, "attempts": len(state["attempts"]),
                "remaining": LIMIT - len(state["attempts"]), "paused": state["paused"],
                "active": state["active"], "cases": {key: value["status"] for key, value in state["cases"].items()},
                "pending": [key for key, value in state["attempts"].items() if value["state"] == "pending"],
                "journal_sha256": records[-1]["sha256"]}

    def verify(self, *, binding: dict[str, Any]) -> dict[str, Any]:
        current = _binding(binding)
        with self._locked() as (descriptor, records, state):
            if current != state["header"]["binding"]:
                self._append(descriptor, records, "pause", {"reason": "version_mismatch"})
                raise LedgerError("ledger_binding_mismatch")
            self._verified_binding = current
            if state["active"] is not None:
                self._live(state, state["active"])
            return self._summary(records, state)

    def summary(self) -> dict[str, Any]:
        with self._locked() as (_, records, state):
            return self._summary(records, state)

    def begin_case(self, name: str) -> None:
        capability = secrets.token_hex(32)
        body = {"case": name, "capability_sha256": hashlib.sha256(capability.encode("ascii")).hexdigest()}
        with self._locked() as (descriptor, records, state):
            self._bound(state)
            self._transition(state, "begin", body)
            self._append(descriptor, records, "begin", body)
            self._live_cases[name] = capability

    def claim(self, *, payload_sha256: str, model: str) -> str:
        _hash(payload_sha256)
        with self._locked() as (descriptor, records, state):
            name = state["active"]
            self._live(state, name)
            body = {"attempt_id": f"{name}:{len(state['attempts']) + 1:02}", "case": name,
                    "payload_sha256": payload_sha256, "model": model}
            self._transition(state, "claim", body)
            self._append(descriptor, records, "claim", body)
            return body["attempt_id"]

    def settle(self, attempt_id: str, *, state: str, metadata: dict[str, Any] | None = None) -> None:
        try:
            safe_metadata = _metadata(metadata)
        except LedgerError:
            # A real send may already have happened. Never permit a fallback
            # send merely because completion metadata could not be recorded.
            self.pause("storage_failure")
            raise
        body = {"attempt_id": attempt_id, "state": state, "metadata": safe_metadata}
        with self._locked() as (descriptor, records, current):
            self._bound(current)
            self._transition(current, "settle", body)
            self._append(descriptor, records, "settle", body)

    def finish_case(self, name: str) -> None:
        with self._locked() as (descriptor, records, state):
            self._live(state, name)
            body = {"case": name}
            self._transition(state, "end", body)
            self._append(descriptor, records, "end", body)
            del self._live_cases[name]

    def pause(self, code: str) -> None:
        with self._locked() as (descriptor, records, state):
            self._bound(state)
            body = {"reason": code}
            self._transition(state, "pause", body)
            self._append(descriptor, records, "pause", body)

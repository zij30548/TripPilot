"""Explicit, fixed seven-operation live admission; never an application client.

The old controlled journal remains controlled. This separate format reuses only
its reviewed durable file/lock/state primitives. A caller must verify the frozen
execution before creation and before every dispatch. It cannot opt into a mock
peer or another endpoint/model through this API. Unit tests replace the httpx
transport constructor outside this module; this module never sends on import.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import time
from typing import Any

import httpx

from core.config import Settings
from core.llm_utils import RuntimeFailure, strict_json
from core.usage_runtime import MeteredAsyncClient
from tooling.providers.qwen import MODEL, beijing_endpoint
from tools.echomind_travel_trial_ledger import (
    Ledger, LedgerError, MAX_BYTES, MAX_LINE, MAX_RECORDS, ZERO,
    _binding, _canonical, _exact, _hash, _integer, _lock, _pairs,
)
from tools.echomind_travel_trial_transport import (
    MAX_INPUT, TransportStopped, _Lease, _decode, _payload, _persist, digest,
)

VERSION = "echomind-travel-live-journal-v1"
FILENAME = "echomind-travel-live.jsonl"


def _live_binding(value: Any) -> dict[str, Any]:
    _exact(value, {"grant_sha256", "plan_protocol_sha256", "execution_sha256", "prior_ledger_hashes"})
    _hash(value["grant_sha256"])
    _binding({key: value[key] for key in ("plan_protocol_sha256", "execution_sha256", "prior_ledger_hashes")})
    return json.loads(_canonical(value))


class LiveLedger(Ledger):
    """Private append-only physical-send reservation, never refunds/reopens.

    The one canonical filename prevents tools selecting a fresh filename in the
    active batch. The runner additionally pins its stage directory in its grant;
    moving/deleting a journal or changing batches is not authorized recovery.
    """

    def __init__(self, path: str | Path):
        if Path(path).name != FILENAME:
            raise LedgerError("live_canonical_ledger_and_limit_required")
        super().__init__(path)

    @classmethod
    def create(cls, path: str | Path, *, binding: dict[str, Any],
               authorized_limit: int = 21) -> "LiveLedger":
        if Path(path).name != FILENAME or type(authorized_limit) is not int or authorized_limit != 21:
            raise LedgerError("live_canonical_ledger_and_limit_required")
        ledger = cls(path)
        header = {"schema_version": VERSION, "mode": "live", "limit": 21,
                  "binding": _live_binding(binding), "real_authorized_calls": 21}
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
                row = json.loads(line, object_pairs_hook=_pairs,
                                 parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            except (ValueError, UnicodeError, RecursionError):
                raise LedgerError("ledger_invalid_json") from None
            _exact(row, {"seq", "previous", "timestamp_ns", "kind", "body", "sha256"})
            _integer(row["seq"], number, number)
            _integer(row["timestamp_ns"], 1, 2**63 - 1)
            previous = records[-1]["sha256"] if records else ZERO
            unsigned = {key: value for key, value in row.items() if key != "sha256"}
            if (row["previous"] != previous or row["sha256"] != digest(_canonical(unsigned))
                    or line != _canonical(row) + b"\n"):
                raise LedgerError("ledger_hashchain_mismatch")
            kind, body = row["kind"], row["body"]
            if number == 0:
                _exact(body, {"schema_version", "mode", "limit", "binding", "real_authorized_calls"})
                if (kind != "header" or body["schema_version"] != VERSION or body["mode"] != "live"
                        or type(body["limit"]) is not int or body["limit"] != 21
                        or type(body["real_authorized_calls"]) is not int or body["real_authorized_calls"] != 21):
                    raise LedgerError("live_invalid_header")
                _live_binding(body["binding"])
                state["header"] = body
            elif kind == "pause":
                _exact(body, {"reason"})
                if body["reason"] not in {"storage_failure", "live_execution_error"}:
                    raise LedgerError("ledger_invalid_pause")
                state["paused"] = True
            else:
                LiveLedger._transition(state, kind, body)
            records.append(row)
        return records, state

    @staticmethod
    def _transition(state: dict[str, Any], kind: Any, body: Any) -> None:
        if kind != "skip":
            Ledger._transition(state, kind, body)
            return
        _exact(body, {"op_id", "reason"})
        op_id = body["op_id"]
        if (state["paused"] or state["active"] is not None
                or any(row["state"] == "pending" for row in state["attempts"].values())
                or type(op_id) is not str or op_id != f"m{len(state['operations']) + 1:02}"
                or op_id not in {f"m{number:02}" for number in range(1, 8)}
                or body["reason"] != "dependency_failed"):
            raise LedgerError("live_skip_not_permitted")
        state["operations"][op_id] = {"binding_sha256": None, "attempts": 0,
                                      "sealed": True, "skipped": "dependency_failed"}

    def skip_operation(self, op_id: str, *, reason: str = "dependency_failed") -> None:
        self._event("skip", {"op_id": op_id, "reason": reason})

    def pause(self) -> None:
        with self._locked() as (descriptor, records, state):
            if not state["paused"]:
                self._append(descriptor, records, "pause", {"reason": "live_execution_error"})

    def verify(self, *, binding: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._locked() as (_, records, state):
            header = state["header"]
            if binding is not None and _live_binding(binding) != header["binding"]:
                raise LedgerError("ledger_execution_binding_changed")
            counts = {name: sum(item["state"] == name for item in state["attempts"].values())
                      for name in ("pending", "received", "failed", "cancelled", "unknown")}
            return {"schema_version": VERSION, "mode": "live", "limit": 21,
                    "real_authorized_calls": 21, "reserved_attempts": len(state["attempts"]),
                    "counts": counts, "pending": bool(counts["pending"]), "paused": state["paused"],
                    "active_operation": state["active"], "operations": state["operations"],
                    "binding": header["binding"], "records": len(records),
                    "ledger_sha256": digest(b"".join(_canonical(row) + b"\n" for row in records))}

    summary = verify


class _LiveLease(_Lease):
    def __init__(self, ledger: LiveLedger, operation_id: str, binding_sha256: str, question: str,
                 initial_payload: dict[str, Any] | None, settings: Settings,
                 current_binding: Callable[[], str], verify_execution: Callable[[], None],
                 persist_initial: Callable[[bytes, str], None],
                 observe_response: Callable[[str, dict[str, Any]], None]):
        if type(ledger) is not LiveLedger or type(settings) is not Settings:
            raise TransportStopped("live_binding_required")
        _hash(binding_sha256)
        if not all(callable(item) for item in (current_binding, verify_execution, persist_initial, observe_response)):
            raise TransportStopped("live_verification_required")
        if settings.qwen_model != MODEL:
            raise TransportStopped("live_fixed_model_required")
        self.endpoint = beijing_endpoint(settings)
        self.authorization = "Bearer " + settings.qwen_api_key.get_secret_value()
        self.initial = None if initial_payload is None else deepcopy(_payload(initial_payload))
        if (type(question) is not str or not 1 <= len(question) <= 2000 or self.initial is not None
                and self.initial["messages"][-1] != {"role": "user", "content": question}):
            raise TransportStopped("live_question_binding")
        self.question, self.persist_initial = question, persist_initial
        self.observe_response = observe_response
        self.ledger, self.operation_id, self.binding = ledger, operation_id, binding_sha256
        self.current_binding, self.verify_execution = current_binding, verify_execution
        self.previous: dict[str, Any] | None = None
        self.calls: list[dict[str, Any]] | None = None
        self.pending: str | None = None
        self.last_status: int | None = None
        self.started, self.lock, self.closed = 0.0, asyncio.Lock(), False
        self.observation_lock = asyncio.Lock()

    async def finish(self, state: str, raw: bytes | None = None, encoding: str = "") -> None:
        # Receipt persistence is part of the execution boundary, not grading.
        # Failure leaves the reservation occupied and durably pauses admission.
        async with self.observation_lock:
            if self.pending is not None and raw is not None:
                decoded = _decode(raw, encoding)
                evidence: dict[str, Any] = {"schema_version": "echomind-native-response-observation-v1",
                    "http_status": self.last_status, "response_sha256": digest(raw),
                    "response_id": "unknown", "upstream_model": "unknown", "finish_reason": "unknown",
                    "native_tool_calls": self._selected(decoded) or [], "content": None,
                    "usage": {"input_tokens": None, "output_tokens": None}, "diagnostic": "invalid_json"}
                try:
                    parsed = strict_json(decoded)
                    if type(parsed) is dict:
                        for source, target in (("id", "response_id"), ("model", "upstream_model")):
                            value = parsed.get(source)
                            if (type(value) is str and 1 <= len(value) <= 128
                                    and all(character.isascii() and (character.isalnum() or character in "_.-")
                                            for character in value)):
                                evidence[target] = value
                        usage = parsed.get("usage")
                        if type(usage) is dict:
                            for source, target in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens")):
                                value = usage.get(source)
                                evidence["usage"][target] = value if type(value) is int and 0 <= value <= 10_000_000 else None
                        choices = parsed.get("choices")
                        if type(choices) is list and len(choices) == 1 and type(choices[0]) is dict:
                            choice = choices[0]
                            reason = choice.get("finish_reason")
                            if reason in {"stop", "tool_calls", "length", "content_filter", "function_call"}:
                                evidence["finish_reason"] = reason
                            message = choice.get("message")
                            if type(message) is dict:
                                content = message.get("content")
                                if type(content) is str and len(content.encode()) <= 8192:
                                    evidence["content"] = content
                        evidence["diagnostic"] = "native_tools" if evidence["native_tool_calls"] else "invalid_native_or_final"
                except (ValueError, KeyError, TypeError, RecursionError):
                    pass
                _, interrupted = await _persist(lambda: self.observe_response(self.pending, evidence))
                if interrupted:
                    raise asyncio.CancelledError
            await super().finish(state, raw, encoding)

    async def _request(self, request: httpx.Request) -> None:
        async with self.lock:
            self.verify_execution()
            self.ledger.verify()
            if self.closed or self.pending is not None or self.current_binding() != self.binding:
                raise TransportStopped("live_context_changed_or_pending")
            if (str(request.url) != self.endpoint or request.method != "POST"
                    or request.headers.get("authorization") != self.authorization):
                raise TransportStopped("live_endpoint_or_credentials")
            try:
                raw = request.content
                if len(raw) > MAX_INPUT:
                    raise ValueError()
                payload = _payload(strict_json(raw))
            except (ValueError, TypeError, RecursionError, httpx.RequestNotRead):
                raise TransportStopped("live_payload_invalid") from None
            if self.initial is None:
                if payload["messages"][-1] != {"role": "user", "content": self.question}:
                    raise TransportStopped("live_question_binding")
                self.initial = deepcopy(payload)
            if self.previous is None:
                _, interrupted = await _persist(lambda: self.persist_initial(raw, self.binding))
                if interrupted:
                    raise asyncio.CancelledError
            if {key: value for key, value in payload.items() if key != "messages"} != {
                    key: value for key, value in self.initial.items() if key != "messages"}:
                raise TransportStopped("live_frozen_configuration_changed")
            if self.previous is None:
                if payload != self.initial:
                    raise TransportStopped("live_initial_payload_changed")
            else:
                self._feedback(payload)
            attempt, interrupted = await _persist(lambda: self.ledger.claim(
                self.operation_id, digest(raw), binding_sha256=self.binding))
            self.pending, self.previous, self.calls = attempt, deepcopy(payload), None
            self.started = time.monotonic()
            if interrupted:
                raise asyncio.CancelledError
            self.verify_execution()
            if self.current_binding() != self.binding:
                raise TransportStopped("live_context_changed_or_pending")


@asynccontextmanager
async def live_client(ledger: LiveLedger, operation_id: str, binding_sha256: str,
                      question: str, initial_payload: dict[str, Any] | None, settings: Settings, *,
                      current_binding: Callable[[], str], verify_execution: Callable[[], None],
                      persist_initial: Callable[[bytes, str], None],
                      observe_response: Callable[[str, dict[str, Any]], None]
                      ) -> AsyncIterator[MeteredAsyncClient]:
    """Exact product client, trusted settings only, retries=0; no live fallback.

    ``verify_execution`` must check the final immutable grant/source/input hashes
    and original journals. It is called before opening and on both sides of a
    durable attempt claim. Caller context binds normal product-store metering.
    HTTP receipt is not a semantic pass; its selected facts are never repaired.
    """
    lease: _LiveLease | None = None
    failed, caught_error = False, False
    try:
        verify_execution()
        lease = _LiveLease(ledger, operation_id, binding_sha256, question, initial_payload,
                           settings, current_binding, verify_execution, persist_initial, observe_response)
        _, interrupted = await _persist(lambda: ledger.start_operation(operation_id, binding_sha256))
        if interrupted:
            raise asyncio.CancelledError
        transport = httpx.AsyncHTTPTransport(retries=0, trust_env=False)
        async with MeteredAsyncClient(transport=transport, trust_env=False,
                follow_redirects=False, timeout=httpx.Timeout(20, connect=5),
                headers={"Authorization": lease.authorization},
                event_hooks={"request": [lease.request], "response": [lease.response]}) as client:
            yield client
    except BaseException as error:
        caught_error = True
        execution_error = (lease is None or lease.pending is not None or not isinstance(error, RuntimeFailure)
                           or isinstance(error, (TransportStopped, LedgerError, httpx.HTTPError, TimeoutError)))
        if execution_error:
            failed = True
            if lease is not None:
                try:
                    await lease.finish("cancelled" if isinstance(error, asyncio.CancelledError) else "unknown")
                except BaseException:
                    pass
            try:
                await _persist(ledger.pause)
            except BaseException:
                pass
        raise
    finally:
        if lease is not None:
            lease.closed = True
            if lease.pending is not None and not failed:
                failed = True
                try:
                    await lease.finish("unknown")
                except BaseException:
                    await lease.pause_preserving_error()
                    if not caught_error:
                        raise
            if not failed:
                try:
                    if not ledger.summary()["paused"]:
                        _, interrupted = await _persist(lambda: ledger.seal_operation(operation_id))
                        if interrupted:
                            raise asyncio.CancelledError
                except BaseException:
                    await lease.pause_preserving_error()
                    if not caught_error:
                        raise

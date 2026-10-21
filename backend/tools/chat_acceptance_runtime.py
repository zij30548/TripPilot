"""Opt-in live acceptance harness; never imported by production startup.

Uses the unchanged application routes, real authentication/MySQL and the existing
metered HTTP client. It only adds a stricter, immutable twelve-send test gate.
No response fixtures, model retry, model substitution or production migration.
"""
from __future__ import annotations

import argparse
import asyncio
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4

import httpx

from tools.chat_acceptance import (AcceptanceStopped, BATCH, ChatAcceptanceLedger, IDS, MODEL, ROOT,
                                   STAGE_ROOT, _new, _read, canonical, create_continuation, digest,
                                   freeze, safe_upstream_metadata)

MANIFEST = ROOT / "backend/evaluations/chat12-acceptance-v1.json"
ORIGIN = "http://chat-accept.localhost:3106"
API = "http://127.0.0.1:8803"
_CASE: ContextVar[str | None] = ContextVar("chat_acceptance_case", default=None)


def wire(payload: dict) -> bytes:
    return httpx.Request("POST", "https://example.invalid", json=payload).content


def projected_native(raw: object) -> dict:
    """Keep actual structured choice evidence, never headers or thinking text."""
    if not isinstance(raw, dict):
        return {"kind": "non_object", "sha256": digest(canonical(raw))}
    from tooling.providers.qwen import parse_content, strict_json
    from tooling.providers.qwen_chat_places import parse_tool_call
    from core.schemas.chat import Selection
    result = {"metadata": safe_upstream_metadata(raw), "response_sha256": digest(canonical(raw))}
    try:
        choices = raw.get("choices")
        if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict) and choices[0].get("finish_reason") == "tool_calls":
            call_id, selected, _ = parse_tool_call(raw)
            call = choices[0]["message"]["tool_calls"][0]
            # Preserve original legal arguments, never normalize or repair them.
            result["native_tool_call"] = {"id": call_id, "type": "function", "function": {
                "name": selected.name, "arguments": strict_json(call["function"]["arguments"])}}
            result["finish_reason"] = "tool_calls"
        else:
            content, _ = parse_content(raw)
            Selection.model_validate(content)
            result["content"] = content
            result["finish_reason"] = "stop"
    except Exception:
        # Invalid/unknown fields are not safe arbitrary output to retain.
        result["validation_failure"] = "structure_not_retained"
    return result


def _read_json(path: Path) -> dict:
    from tooling.providers.qwen import strict_json
    try:
        value = strict_json(_read(path, 2 * 1024 * 1024))
    except (OSError, ValueError, TypeError, RecursionError):
        raise AcceptanceStopped("private_evidence_unavailable") from None
    if not isinstance(value, dict):
        raise AcceptanceStopped("invalid_evidence_json")
    return value


def frozen_payloads(bindings: dict, evaluation: dict, *, now: datetime) -> tuple[list[dict], dict]:
    """Derive requests from server-owned seed states, never evaluation labels."""
    from core.schemas.chat_places import ChatPlacesWorkspace
    from core.schemas.chat import Conversation
    from tooling.travel.chat import model_payload
    from tooling.providers.qwen_chat_places import tool_payload
    payloads, requests = [], {}
    for case in evaluation["cases"]:
        identifier, question = case["id"], case["user_text"]
        feature = "intent" if case["kind"] == "intent" else "places"
        binding = bindings["cases"][identifier if case["channel"] == "api" else "chrome"]
        if feature == "intent":
            # Match ChatStore's Pydantic field order, independent of the private
            # artifact's JSON sorting; embedded JSON strings affect wire hashes.
            conversation = Conversation.model_validate(binding["conversation"]).model_dump(mode="json")
            conversation["messages"].append({"role": "user", "text": question})
            payload = model_payload(conversation, question, model=MODEL, now=now)
            body = {"operation_id": str(uuid4()), "expected_revision": binding["conversation"]["revision"], "text": question}
            suffix = "/turns"
        else:
            workspace = deepcopy(binding["places"])
            if case["channel"] == "chrome":
                # Only these counters/versions enter the existing model context;
                # no future or invented POI snapshot is ever created or sent.
                workspace.update(city_id="320500", intent_revision=3,
                                 revision=case["expected_workspace_revision"])
            # begin_operation reserves one revision before the model context.
            workspace["revision"] += 1
            payload = tool_payload(ChatPlacesWorkspace.model_validate(workspace), question)
            if case["channel"] == "chrome":
                context = json.loads(payload["messages"][1]["content"])
                context.update(required_count=0 if identifier == "chrome02" else 1,
                               accommodation_confirmed=identifier == "chrome04")
                payload["messages"][1]["content"] = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
            body = {"action": "ask", "operation_id": str(uuid4()), "text": question,
                    "expected_intent_revision": workspace["intent_revision"],
                    "expected_revision": workspace["revision"] - 1}
            suffix = "/places"
        if len(wire(payload)) > 48_000:
            raise AcceptanceStopped("payload_mismatch")
        payloads.append({"id": identifier, "channel": case["channel"], "feature": feature,
                         "payload_hash_kind": "wire_bytes", "payload_sha256": digest(wire(payload)),
                         "canonical_payload_sha256": digest(canonical(payload))})
        requests[identifier] = {"path": "/chat/conversations/" + binding["conversation"]["id"] + suffix,
                                "body": body, "payload": payload}
    if tuple(p["id"] for p in payloads) != IDS or len({p["payload_sha256"] for p in payloads}) != 12:
        raise AcceptanceStopped("ambiguous_payload_plan")
    return payloads, requests


def frozen_files() -> dict:
    paths = subprocess.check_output(["rg", "--files", "backend/app", "frontend/src", "shared"], cwd=ROOT, text=True).splitlines()
    paths += ["backend/main.py", "backend/uv.lock", "frontend/package-lock.json", "frontend/next.config.ts",
              "backend/evaluations/chat12-acceptance-v1.json", "frontend/.next/routes-manifest.json"]
    paths += subprocess.check_output(["rg", "--files", "backend/tools", "backend/tests"], cwd=ROOT, text=True).splitlines()
    values = {p: digest((ROOT / p).read_bytes()) for p in sorted(set(paths))
            if (p.endswith((".py", ".ts", ".tsx", ".json", ".lock")) and "__pycache__" not in p)}
    values["frontend/.next/BUILD_ID"] = digest((ROOT / "frontend/.next/BUILD_ID").read_bytes())
    return values


def prepare_freeze(directory: Path, private_directory: Path) -> None:
    evaluation = json.loads(MANIFEST.read_bytes())
    bindings = _read_json(private_directory / "public-bindings.json")
    cases, requests = frozen_payloads(bindings, evaluation, now=datetime.now(timezone.utc))
    history = ROOT.parent / "trippilot-evidence"
    old = {str(p.resolve()): digest(p.read_bytes()) for p in sorted(history.rglob("*.jsonl")) if STAGE_ROOT not in p.parents}
    protocol = {"schema_version": "chat-acceptance-v1", "batch": BATCH, "limit": 12, "model": MODEL,
                "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "files": frozen_files(), "old_ledgers": old, "cases": cases}
    # Fully derive/validate before making the one-time authorization claim.
    freeze(directory, protocol)
    _new(directory / "requests.json", canonical(requests))
    _new(directory / "rubric.json", canonical(evaluation))
    _new(directory / "bindings.json", canonical(bindings))
    protected = ["backend/app/__pycache__/config.cpython-313.pyc",
                 "backend/app/integrations/__pycache__/amap.cpython-313.pyc",
                 "backend/app/__pycache__/logging_filters.cpython-313.pyc",
                 "backend/app/api/__pycache__/amap_proxy.cpython-313.pyc",
                 "backend/app/integrations/__pycache__/amap_proxy.cpython-313.pyc",
                 "backend/data/business-usage.sqlite3", "backend/data/saved-trips.sqlite3",
                 "backend/data/business-usage.sqlite3.identity"]
    _new(directory / "protected-before.json", canonical({p: digest((ROOT / p).read_bytes()) for p in protected}))
    gate = directory.parent / ".runtime-20261021-12"
    gate.mkdir(mode=0o700)
    identity = {"batch": BATCH, "protocol_sha256": digest((directory / "protocol.json").read_bytes()),
                "directory": str(directory.resolve()), "amap_logical_limit": 6, "amap_physical_limit": 12,
                "artifacts": {p: digest((directory / p).read_bytes()) for p in ("requests.json", "rubric.json", "bindings.json", "protected-before.json")}}
    _new(gate / "identity.json", canonical(identity))
    _new(gate / "gate.lock", b"")
    _new(gate / "amap.jsonl", canonical({"event": "initialized", "protocol_sha256": identity["protocol_sha256"]}) + b"\n")
    print(json.dumps({"frozen": True, "planned_model_operations": 12, "actual_calls": 0, "amap_logical_limit": 6, "amap_physical_limit": 12}))


def prepare_continuation(directory: Path) -> None:
    """Freeze only the eleven unsent operations; retain the original budget."""
    from tooling.travel.chat import PROVIDER_SCHEMA_VERSION
    if PROVIDER_SCHEMA_VERSION != "chat-intent-schema-v2-whole-text":
        raise AcceptanceStopped("historical_continuation_version_mismatch")
    protocol = _read_json(directory / "protocol.json")
    original = _read_json(directory / "requests.json")
    evaluation = _read_json(directory / "rubric.json")
    bindings = _read_json(directory / "bindings.json")
    stop_path = directory.parent / ".runtime-20261021-12" / "stop.json"
    if _read_json(stop_path).get("code") != "test_session_expired_or_changed":
        raise AcceptanceStopped("unsupported_prior_runtime_stop")
    today = json.loads(original["api02"]["payload"]["messages"][1]["content"])["today"]
    from zoneinfo import ZoneInfo
    if datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat() != today:
        raise AcceptanceStopped("frozen_calendar_changed")
    cases, requests = frozen_payloads(bindings, evaluation,
                                     now=datetime.fromisoformat(today).replace(tzinfo=timezone.utc))
    # Only the declared provider projection can differ. All original operation
    # bodies, questions, expectations, date contexts and native tools stay fixed.
    paths = (("accommodation_hint", "anyOf", 0), ("required_place_hints", "items"),
             ("preferences", "items"))
    for current, old_case in zip(cases, protocol["cases"], strict=True):
        identifier = current["id"]
        request = requests[identifier]
        restored = deepcopy(request["payload"])
        if current["feature"] == "intent":
            properties = restored["response_format"]["json_schema"]["schema"]["$defs"]["Intent"]["properties"]
            for path in paths:
                node = properties
                for key in path:
                    node = node[key]
                if node["pattern"] != r"^[\s\S]*\S[\s\S]*$":
                    raise AcceptanceStopped("unexpected_provider_projection")
                node["pattern"] = r"\S"
        if (digest(wire(restored)) != old_case["payload_sha256"]
                or canonical(restored) != canonical(original[identifier]["payload"])
                or request["path"] != original[identifier]["path"]):
            raise AcceptanceStopped("unexpected_continuation_payload_change")
        request["body"] = original[identifier]["body"]
    # Never make a candidate payload or new operation for the consumed api01.
    requests = {identifier: requests[identifier] for identifier in IDS[1:]}
    files = frozen_files()
    journal = _read(directory / "calls.jsonl", 256 * 1024)
    output = directory / "continuation-v1"
    output.mkdir(mode=0o700)
    _new(output / "requests.json", canonical(requests))
    artifacts = {str((directory / name).resolve()): digest(_read(directory / name, 2 * 1024 * 1024))
                 for name in ("requests.json", "rubric.json", "bindings.json", "protected-before.json")}
    artifacts[str((output / "requests.json").resolve())] = digest(_read(output / "requests.json", 2 * 1024 * 1024))
    # Byte snapshots, not just hashes, make this repaired execution reproducible.
    for relative, sha in files.items():
        target = output / "sources" / relative
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        raw = (ROOT / relative).read_bytes()
        if digest(raw) != sha:
            raise AcceptanceStopped("frozen_file_changed")
        _new(target, raw)
        artifacts[str(target.resolve())] = sha
    manifest = {
        "schema_version": "chat-acceptance-continuation-v1", "batch": BATCH, "limit": 12, "model": MODEL,
        "original_protocol_sha256": digest(_read(directory / "protocol.json", 512 * 1024)),
        "journal_prefix_sha256": digest(journal), "journal_prefix_bytes": len(journal), "consumed": 1,
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "files": files, "cases": cases[1:], "artifacts": artifacts,
        "prior_runtime_stop_sha256": digest(_read(stop_path, 4096)),
        "change_description": "Resume only api02-chrome04 under the original total 12. Offline repair: "
                              "actual authenticated session contract; " + PROVIDER_SCHEMA_VERSION +
                              "; exact continuation counting and byte snapshots. No prompt/model/parameter/label change.",
    }
    create_continuation(directory, manifest, continuation=directory / "continuation-v1.json")
    print(json.dumps({"continuation_frozen": True, "consumed": 1, "remaining": 11,
                      "total_limit": 12, "api01_repeated": False, "new_allowance": False}))


class LiveGate:
    continuation: Path | None = None

    def __init__(self, directory: Path, *, continuation: Path | None = None):
        self.directory = directory
        self.continuation = continuation
        self.ledger = ChatAcceptanceLedger(directory, continuation=continuation)
        self.runtime = directory.parent / ".runtime-20261021-12"
        self.identity = _read_json(self.runtime / "identity.json")
        self.requests = _read_json(directory / ("continuation-v1/requests.json" if continuation else "requests.json"))
        self.active: dict[str, dict] = {}
        self.verify()

    def verify(self) -> dict:
        status = self.ledger.verify()
        if self.identity != _read_json(self.runtime / "identity.json") or self.identity["protocol_sha256"] != self.ledger.protocol_hash:
            raise AcceptanceStopped("batch_identity_mismatch")
        for name, h in self.identity["artifacts"].items():
            if digest(_read(self.directory / name, 2 * 1024 * 1024)) != h:
                raise AcceptanceStopped("frozen_artifact_changed")
        if self.continuation:
            from zoneinfo import ZoneInfo
            frozen_day = json.loads(self.requests["api02"]["payload"]["messages"][1]["content"])["today"]
            if datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat() != frozen_day:
                self.stop("frozen_calendar_changed")
                raise AcceptanceStopped("frozen_calendar_changed")
        if self.stop_path.exists() or status["paused"]:
            raise AcceptanceStopped("acceptance_paused_or_exhausted")
        self.amap_rows()
        return status

    def amap_rows(self) -> list[dict]:
        raw = _read(self.runtime / "amap.jsonl", 256 * 1024)
        if not raw.endswith(b"\n"):
            raise AcceptanceStopped("incomplete_journal")
        rows = [json.loads(line) for line in raw.splitlines()]
        if rows[0] != {"event": "initialized", "protocol_sha256": self.ledger.protocol_hash}:
            raise AcceptanceStopped("amap_journal_mismatch")
        previous = rows[0]
        for ordinal, row in enumerate(rows[1:], 1):
            if (row.get("event") != "attempt" or row.get("ordinal") != ordinal or ordinal > 12
                    or row.get("previous_sha256") != digest(canonical(previous))
                    or _read_json(self.runtime / f"amap-{ordinal:02}.json") != row):
                raise AcceptanceStopped("amap_journal_mismatch")
            result = _read_json(self.directory / f"amap-{ordinal:02}-transport.json")
            if (result.get("operation") != row["operation"] or result.get("physical_ordinal") != ordinal
                    or result.get("error") not in (None, "connect_failure")
                    or result.get("error") is None and not isinstance(result.get("http_status"), int)):
                raise AcceptanceStopped("amap_unknown_or_execution_failure")
            previous = row
        markers = sorted(p.name for p in self.runtime.glob("amap-*.json"))
        if markers != [f"amap-{n:02}.json" for n in range(1, len(rows))]:
            raise AcceptanceStopped("amap_journal_mismatch")
        return rows

    def stop(self, code: str) -> None:
        if not self.stop_path.exists():
            _new(self.stop_path, canonical({"code": code, "at": datetime.now(timezone.utc).isoformat()}))

    @property
    def stop_path(self) -> Path:
        return self.runtime / ("continuation-stop-v1.json" if self.continuation else "stop.json")

    def model_case(self, request: httpx.Request) -> str:
        self.verify()
        h = digest(request.content)
        matched = [p for p in self.ledger.cases if p["payload_sha256"] == h]
        if len(matched) != 1:
            self.stop("payload_mismatch")
            raise AcceptanceStopped("payload_mismatch")
        return matched[0]["id"]

    def reserve_amap(self, operation: str, request: httpx.Request) -> int:
        self.verify()
        if (operation not in IDS[4:] or operation == "chrome01" or request.method != "GET"
                or request.url.scheme != "https" or request.url.host != "restapi.amap.com"
                or request.url.path != "/v5/place/text" or request.url.port not in (None, 443)):
            self.stop("unexpected_upstream")
            raise AcceptanceStopped("unexpected_upstream")
        from core.chat_cities import city_for
        params = dict(request.url.params)
        if (set(params) != {"key", "keywords", "region", "city_limit", "page_size", "page_num"}
                or len(list(request.url.params.multi_items())) != len(params)
                or params["city_limit"] != "true" or params["page_size"] != "20" or params["page_num"] != "1"
                or not 1 <= len(params["keywords"].strip()) <= 80):
            self.stop("unexpected_upstream")
            raise AcceptanceStopped("unexpected_upstream")
        city_for(params["region"])
        safe = {k: v for k, v in params.items() if k != "key"}
        lock = os.open(self.runtime / "gate.lock", os.O_RDWR | os.O_NOFOLLOW)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = self.runtime / "amap.jsonl"
            rows = self.amap_rows()
            attempts = [r for r in rows if r["event"] == "attempt"]
            same = [r for r in attempts if r["operation"] == operation]
            logical = {r["operation"] for r in attempts}
            if (len(attempts) >= 12 or operation not in logical and len(logical) >= 6 or len(same) >= 2
                    or same and same[0]["query"] != safe):
                self.stop("amap_budget_or_query_changed")
                raise AcceptanceStopped("amap_budget_or_query_changed")
            ordinal = len(attempts) + 1
            row = {"event": "attempt", "operation": operation, "ordinal": ordinal, "query": safe,
                   "previous_sha256": digest(canonical(rows[-1])), "at": datetime.now(timezone.utc).isoformat()}
            if self.continuation:
                row["continuation_sha256"] = self.ledger.continuation_hash
            _new(self.runtime / f"amap-{ordinal:02}.json", canonical(row))
            with path.open("ab") as stream:
                stream.write(canonical(row) + b"\n"); stream.flush(); os.fsync(stream.fileno())
            return ordinal
        finally:
            os.close(lock)


class RecordedPOIStream(httpx.AsyncByteStream):
    """Observe bounded streaming without buffering or changing body limits."""
    def __init__(self, inner: httpx.AsyncByteStream, complete):
        self.inner, self.complete, self.done = inner, complete, False

    def finish(self, error: str | None = None) -> None:
        if not self.done:
            self.done = True
            self.complete(error)

    async def __aiter__(self):
        try:
            async for chunk in self.inner:
                yield chunk
        except BaseException:
            self.finish("body_failed_or_cancelled")
            raise
        else:
            self.finish()

    async def aclose(self) -> None:
        try:
            await self.inner.aclose()
        finally:
            self.finish("closed_before_body_complete")


def install_live_gate(gate: LiveGate) -> None:
    """Test-process-only wrapper around unchanged metered production transport."""
    from tooling.providers.qwen import QwenClient, QwenError, strict_json
    from core.usage_runtime import MeteredAsyncClient
    original_send, original_request = MeteredAsyncClient.send, QwenClient._request

    async def send(self, request, **kwargs):
        if request.method == "POST" and request.url.path == "/compatible-mode/v1/chat/completions":
            operation = gate.model_case(request)
            payload = strict_json(request.content)
            gate.ledger.reserve(operation, request.content, model=payload.get("model"))
            _CASE.set(operation)
            gate.active[operation] = {"started": time.monotonic(), "http_status": None}
            try:
                response = await original_send(self, request, **kwargs)
                gate.active[operation]["http_status"] = response.status_code
                return response
            except BaseException:
                # The outer unchanged bounded model transport categorizes it.
                raise
        operation = _CASE.get()
        if operation is None:
            gate.stop("unexpected_upstream")
            raise AcceptanceStopped("unexpected_upstream")
        ordinal = gate.reserve_amap(operation, request)
        started = time.monotonic()
        result = {"operation": operation, "physical_ordinal": ordinal}
        def complete(error: str | None = None) -> None:
            if error:
                result["error"] = error
                if error != "connect_failure":
                    gate.stop("amap_transport_failed_or_cancelled")
            result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
            _new(gate.directory / f"amap-{ordinal:02}-transport.json", canonical(result))
        try:
            response = await original_send(self, request, **kwargs)
            result["http_status"] = response.status_code
            if response.is_closed:
                complete()
            else:
                response.stream = RecordedPOIStream(response.stream, complete)
            return response
        except BaseException:
            # The existing adapter permits one connect retry. Its next actual
            # attempt is separately reserved; no higher-level search retry.
            import sys
            error = sys.exception()
            retryable = isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout))
            complete("connect_failure" if retryable else "transport_failed_or_cancelled")
            raise

    async def model_request(self, payload):
        operation = None
        raw = None
        outcome = "execution"
        try:
            raw = await original_request(self, payload)
            operation = _CASE.get()
            if operation is not None:
                projected = projected_native(raw)
                _new(gate.directory / f"{operation}-native.json", canonical(projected))
                outcome = "structure" if projected.get("validation_failure") else "success"
            return raw
        except QwenError as error:
            operation = _CASE.get()
            outcome = error.code if error.code in {"structure", "references", "empty", "truncated", "refused", "configuration", "authentication", "quota", "model_unavailable", "rate_limit", "timeout"} else "network"
            raise
        except asyncio.CancelledError:
            operation, outcome = _CASE.get(), "cancelled"
            raise
        except BaseException:
            operation = _CASE.get()
            outcome = "execution"
            gate.stop("evidence_or_execution_failure")
            raise
        finally:
            if operation is not None and operation in gate.active:
                info = gate.active.pop(operation)
                metadata = safe_upstream_metadata(raw)
                metadata.update(elapsed_ms=round((time.monotonic() - info["started"]) * 1000), http_status=info["http_status"])
                if raw is not None:
                    metadata["response_sha256"] = digest(canonical(projected_native(raw)))
                gate.ledger.complete(operation, outcome=outcome, metadata=metadata)

    MeteredAsyncClient.send, QwenClient._request = send, model_request


def configure(private_directory: Path) -> None:
    data = _read_json(private_directory / "runtime-database.json")
    # Only the separately prepared private test environment supplies DB/auth.
    for key, value in data.items():
        if key not in {"TRIPPILOT_MYSQL_URL", "BUSINESS_USAGE_IDENTITY_PATH", "BUSINESS_DAILY_LIMITS",
                       "AUTH_COOKIE_SECURE", "AUTH_ALLOWED_ORIGINS", "BUSINESS_RATE_ACCOUNT_LIMIT", "BUSINESS_RATE_GLOBAL_LIMIT"}:
            raise AcceptanceStopped("unexpected_environment_key")
        os.environ[key] = value if isinstance(value, str) else json.dumps(value)
    os.environ["AUTH_COOKIE_SECURE"] = "false"
    os.environ["AUTH_ALLOWED_ORIGINS"] = json.dumps([ORIGIN])


def serve(directory: Path, private_directory: Path, *, continuation: Path | None = None) -> None:
    configure(private_directory)
    import uvicorn
    from main import app
    gate = LiveGate(directory, continuation=continuation) if continuation else LiveGate(directory)
    install_live_gate(gate)
    uvicorn.run(app, host="127.0.0.1", port=8803, access_log=False, log_level="warning")


async def api_run(directory: Path, private_directory: Path, case_id: str, *, continuation: Path | None = None) -> None:
    """One named HTTP operation per invocation; never loops over failures."""
    if case_id not in IDS[:8]:
        raise AcceptanceStopped("api_case_required")
    gate = LiveGate(directory, continuation=continuation) if continuation else LiveGate(directory)
    if gate.verify()["consumed"] != IDS.index(case_id):
        raise AcceptanceStopped("operation_not_next_or_already_used")
    account = _read_json(private_directory / "account-a.json")
    async with httpx.AsyncClient(base_url=API, follow_redirects=False, timeout=55) as client:
        session_path = private_directory / ("api-session-continuation-v1.json" if continuation else "api-session.json")
        if session_path.exists():
            session = _read_json(session_path)
            client.cookies.update(session["cookies"])
            csrf = session["csrf_token"]
            response = await client.get("/auth/session")
            view = response.json()
            if (response.status_code != 200 or view.get("authenticated") is not True
                    or (view.get("user") or {}).get("username") != account["username"]
                    or view.get("csrf_token") != csrf
                    or session.get("session_id") is not None and view.get("session_id") != session["session_id"]):
                gate.stop("test_session_expired_or_changed")
                raise AcceptanceStopped("test_session_expired_or_changed")
        else:
            pre = await client.get("/auth/session")
            csrf = pre.json()["csrf_token"]
            response = await client.post("/auth/login", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}, json=account)
            if response.status_code != 200:
                gate.stop("test_login_failed")
                raise AcceptanceStopped("test_login_failed")
            csrf = response.json()["csrf_token"]
            _new(session_path, canonical({"cookies": dict(client.cookies), "csrf_token": csrf,
                                          "session_id": response.json()["session_id"]}))
        command = gate.requests[case_id]
        try:
            response = await client.post(command["path"], headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}, json=command["body"])
        except httpx.HTTPError:
            gate.stop("application_transport_failure")
            raise AcceptanceStopped("application_transport_failure") from None
        data = response.json()
        _new(directory / f"{case_id}-application.json", canonical({"id": case_id, "http_status": response.status_code, "response": data}))
        # Preserve ordinary structure/reference failures and continue only by an
        # explicit next CLI invocation. Execution problems permanently pause.
        if response.status_code != 200 and data.get("detail", {}).get("code") not in {
                "chat_structure", "chat_references", "chat_empty", "chat_truncated", "chat_refused"}:
            gate.stop("application_execution_failure")
        error_code = (data.get("last_operation") or {}).get("error_code")
        if error_code in {"operation_timeout", "places_timeout", "places_upstream", "chat_place_upstream_error"}:
            gate.stop("place_execution_failure")
        print(json.dumps({"id": case_id, "http_status": response.status_code, "ledger": gate.ledger.verify()}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "continue-freeze", "verify", "serve", "api"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--private-directory", type=Path)
    parser.add_argument("--case", choices=IDS[:8])
    parser.add_argument("--continuation", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "continue-freeze":
            prepare_continuation(args.directory)
        elif args.command == "verify":
            print(json.dumps(LiveGate(args.directory, continuation=args.continuation).verify()))
        elif args.private_directory is None:
            raise AcceptanceStopped("private_environment_required")
        elif args.command == "freeze":
            prepare_freeze(args.directory, args.private_directory)
        elif args.command == "serve":
            serve(args.directory, args.private_directory, continuation=args.continuation)
        else:
            asyncio.run(api_run(args.directory, args.private_directory, args.case, continuation=args.continuation))
    except AcceptanceStopped as error:
        print(json.dumps({"stopped": error.code, "automatic_retry": False}))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()

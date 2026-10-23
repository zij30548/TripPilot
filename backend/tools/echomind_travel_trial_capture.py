"""Zero-send ASGI capture of the existing builder, not live admission.

Trusted callers supply a newly initialized, independent MySQL fixture.  The
ordinary workspace/CSRF/store/CAS path builds m01's first provider request, then
an exclusive MockTransport returns a controlled rejection.  No real transport,
credentials, provider response, scenario-answer table or reset is available.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import text
from sqlalchemy.engine import Engine

from core.llm_utils import strict_json
from core.schemas.dialogue import GROUPS
from tools.echomind_travel_acceptance_plan import load
from tools.echomind_travel_controlled import ROOT, ControlledError, canonical, controlled_app

VERSION = "echomind-travel-initial-capture-v1"
ORIGIN = "http://127.0.0.1:3000"
MODEL = "qwen3.7-flash-2026-07-15"
M01 = "给我安排一个轻松的旅行，具体去哪和哪天我还没决定。"
_TOOLS = frozenset(GROUPS) | {"inspect_trip", "read_preferences", "search_materials",
                            "travel_clarify", "travel_information", "update_trip_requirements"}
_FIELDS = {"model", "max_tokens", "temperature", "stream", "enable_thinking", "messages",
           "tools", "tool_choice", "parallel_tool_calls"}


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _write(descriptor: int, value: dict[str, Any]) -> None:
    raw = canonical(value) + b"\n"
    if len(raw) > 16_384:
        raise ControlledError("controlled_capture_receipt_too_large")
    remaining = memoryview(raw)
    while remaining:
        count = os.write(descriptor, remaining)
        if count <= 0:
            raise ControlledError("controlled_capture_receipt_unavailable")
        remaining = remaining[count:]
    os.fsync(descriptor)


def _output(path: Path) -> int:
    if (not path.is_absolute() or ".." in path.parts or path.is_symlink()
            or any(parent.is_symlink() for parent in path.parents)
            or path.resolve().is_relative_to(ROOT)):
        raise ControlledError("private_external_capture_path_required")
    info = path.parent.stat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise ControlledError("private_external_capture_path_required")
    try:
        return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except OSError:
        raise ControlledError("capture_output_must_be_new") from None


def _fixture(engine: Engine, anchor: Path) -> None:
    url = engine.url
    if (engine.dialect.name != "mysql" or url.drivername != "mysql+pymysql"
            or url.host != "127.0.0.1" or url.port != 13306
            or re.fullmatch(r"trippilot_test_[a-f0-9]{32}", url.database or "") is None):
        raise ControlledError("independent_empty_mysql_required")
    if (not anchor.is_absolute() or anchor.is_symlink()
            or any(parent.is_symlink() for parent in anchor.parents)
            or anchor.resolve().is_relative_to(ROOT)):
        raise ControlledError("independent_private_anchor_required")
    info = anchor.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or info.st_size != 33
            or re.fullmatch(rb"[a-f0-9]{32}\n", anchor.read_bytes()) is None):
        raise ControlledError("independent_private_anchor_required")
    # Initialization is an explicit caller operation, never runtime DDL.  An old
    # populated fixture cannot be silently reused as an empty conversation.
    with engine.connect() as db:
        if any(db.execute(text("SELECT COUNT(*) FROM " + table)).scalar_one() != 0
               for table in ("chat_conversations", "assistant_tasks", "usage_attempts")):
            raise ControlledError("independent_empty_mysql_required")


def _payload_summary(raw: bytes) -> dict[str, Any]:
    if not raw or len(raw) > 48_000:
        raise ControlledError("controlled_capture_payload_invalid")
    try:
        payload = strict_json(raw)
        if (type(payload) is not dict or set(payload) != _FIELDS or payload["model"] != MODEL
                or type(payload["max_tokens"]) is not int or payload["max_tokens"] != 900
                or type(payload["temperature"]) is not float or payload["temperature"] != 0.1
                or any(payload[key] is not False for key in ("stream", "enable_thinking", "parallel_tool_calls"))
                or payload["tool_choice"] != "auto"):
            raise ValueError
        messages, tools = payload["messages"], payload["tools"]
        if (type(messages) is not list or not 2 <= len(messages) <= 64
                or messages[-1] != {"role": "user", "content": M01}
                or messages[0].get("role") != "system" or type(messages[0].get("content")) is not str
                or type(tools) is not list or len(tools) != len(_TOOLS)):
            raise ValueError
        names = []
        for row in tools:
            if (type(row) is not dict or set(row) != {"type", "function"} or row["type"] != "function"
                    or type(row["function"]) is not dict
                    or set(row["function"]) != {"name", "description", "parameters"}):
                raise ValueError
            function = row["function"]
            if (function["name"] not in _TOOLS or type(function["description"]) is not str
                    or type(function["parameters"]) is not dict):
                raise ValueError
            names.append(function["name"])
        if len(set(names)) != len(names) or set(names) != _TOOLS:
            raise ValueError
        if any(type(row) is not dict or set(row) != {"role", "content"}
               or row["role"] not in {"system", "user", "assistant"}
               or type(row["content"]) is not str for row in messages):
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise ControlledError("controlled_capture_payload_invalid") from None
    return {"payload_sha256": hashlib.sha256(raw).hexdigest(), "request_bytes": len(raw),
            "system_sha256": _digest(messages[0]), "messages_sha256": _digest(messages),
            "tool_schema_sha256": _digest(tools), "tool_names": names, "tool_count": len(names),
            "message_count": len(messages), "last_user_sha256": _digest(M01),
            "parameters": {key: payload[key] for key in _FIELDS - {"messages", "tools"}}}


class _CaptureUpstream:
    """Only one valid builder request, deliberately rejected before any network."""
    def __init__(self) -> None:
        self.attempts = 0
        self.summary: dict[str, Any] | None = None

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.attempts += 1
        if (self.attempts != 1 or request.method != "POST"
                or str(request.url) != "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"):
            raise ControlledError("unplanned_controlled_capture_request")
        self.summary = _payload_summary(request.content)
        return httpx.Response(503, json={"error": {"code": "controlled_capture_rejected_no_real_send"}})


def capture_initial_request(output: Path, *, engine: Engine, anchor: Path) -> dict[str, Any]:
    """Capture m01 only; caller must provide an initialized empty fixture.

    This receipt is NOT an acceptance ledger, a usable live payload freeze, or
    authority to send.  The one product-metering row belongs only to this new
    synthetic fixture and is never copied/refunded into a real-call budget.
    """
    descriptor = _output(output)
    base = {"schema_version": VERSION, "mode": "controlled_capture", "operation_id": "m01", "fixture": "empty",
            "capture_kind": "controlled_production_builder_capture", "real_model_calls": 0,
            "real_amap_calls": 0, "real_send_adapter_connected": False,
            "full_live_payload_frozen": False, "deferred_dynamic_operations": ["m03", "m05", "m06", "m07"]}
    try:
        _fixture(engine, anchor)
        manifest = load()
        operation = next(row for row in manifest.operations if row.id == "m01")
        if operation.text != M01 or operation.fixture_id != "empty" or operation.branch != "native_candidate":
            raise ControlledError("frozen_m01_input_changed")
        from fastapi.testclient import TestClient
        from memory.assistant_store import AssistantStore
        from memory.chat_storage import ChatStore
        from memory.chat_places_storage import ChatPlacesStore

        upstream = _CaptureUpstream()
        with controlled_app(engine, anchor, origin=ORIGIN, upstream=upstream) as (app, _):
            with TestClient(app) as client:
                seed = client.get("/workspace/session")
                if seed.status_code != 200:
                    raise ControlledError("controlled_workspace_bootstrap_failed")
                identity = client.post("/workspace/session", json={}, headers={
                    "origin": ORIGIN, "x-csrf-token": seed.json()["csrf_token"]})
                if identity.status_code != 200:
                    raise ControlledError("controlled_workspace_bootstrap_failed")
                owner = identity.json()["user"]["id"]
                headers = {"origin": ORIGIN, "x-csrf-token": identity.json()["csrf_token"],
                           "x-trippilot-surface": "dialogue-v2"}
                created = client.post("/chat/conversations", headers=headers, json={"operation_id": str(uuid4())})
                if created.status_code != 200:
                    raise ControlledError("controlled_conversation_failed")
                before = created.json()
                places = ChatPlacesStore(engine).get(owner, before["id"])
                op, workspace = str(uuid4()), str(uuid4())
                selection = {"workspace_version": workspace, "display": "none", "items": []}
                command = {"operation_id": op, "expected_revision": before["revision"],
                           "text": M01, "dialogue_context": selection}
                response = client.post(f"/chat/conversations/{before['id']}/assistant-turns", headers=headers, json=command)
                after = ChatStore(engine).get(owner, before["id"])
                task = AssistantStore(engine).latest(owner, before["id"])
        if (upstream.attempts != 1 or upstream.summary is None or response.status_code != 502
                or after["last_turn"]["state"] != "failed" or task is None or task["state"] != "failed"
                or after["requirements"] != before["requirements"] or after["confirmed_revision"] is not None):
            raise ControlledError("controlled_capture_did_not_fail_closed")
        with engine.connect() as db:
            rows = db.execute(text("SELECT service,state FROM usage_attempts")).all()
        if rows != [("qwen_text", "received")]:
            raise ControlledError("controlled_capture_metering_mismatch")
        result = base | {"state": "captured_controlled_rejection", "api_status": response.status_code,
            "asgi_status": response.status_code, "program_status": "failed", "captured_request_count": 1,
            "upstream_sends": 0, "controlled_transport_attempts": 1, "product_mock_usage_attempts": 1,
            "payload_sha256": upstream.summary["payload_sha256"], "parameters": upstream.summary["parameters"],
            "binding": {"owner_sha256": _digest(owner), "conversation_id": before["id"],
                "operation_id": op, "initial_revision": before["revision"], "failed_revision": after["revision"],
                "place_revision": places["revision"], "workspace_version": workspace,
                "capture_view_sha256": _digest(before), "selection_sha256": _digest(selection),
                "submitted_command_sha256": _digest(command), "database_name_sha256": _digest(engine.url.database)},
            "request": upstream.summary}
        _write(descriptor, result)
        return result
    except BaseException:
        # Fixed diagnostic only: never exception text, headers, Cookie, input or
        # credentials.  Keep the exclusive file after an interrupted preparation.
        if os.fstat(descriptor).st_size == 0:
            _write(descriptor, base | {"state": "capture_failed_preserve_fixture"})
        raise
    finally:
        os.close(descriptor)

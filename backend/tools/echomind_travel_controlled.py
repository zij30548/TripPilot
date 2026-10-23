"""Opt-in LOCAL controlled HTTP/MySQL harness; never a production fallback.

Runs ordinary API handlers, anonymous-workspace cookies/CSRF, MySQL transactions
and durable quotas. Only actual outbound HTTP transports are controlled. It has
no real credentials, network fallback, model authorization or reset command.
Synthetic POIs/routes and scripted tool choices do NOT prove semantic quality.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager, ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import threading
from typing import Any, Iterator
from unittest.mock import patch
from uuid import uuid4

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlmodel import Session

from core.llm_utils import strict_json

ROOT = Path(__file__).resolve().parents[2]
VERSION = "echomind-travel-controlled-http-v1"
MAX_EVENTS = 2048
MAX_EVENT_LINE_BYTES = 1024
MAX_EVENT_FILE_BYTES = 2 * 1024 * 1024
ORIGIN = "http://127.0.0.1:3112"
REQUIREMENTS_TEXT = "去上海，2026-11-01到2026-11-02，两个人，总预算3000元，住宿参考受控住宿甲，必去受控景点甲，步行。先整理，不搜索。"
PLACE_ROWS = (
    {"id": "controlled-hotel", "name": "受控住宿甲", "address": "受控地址甲（合成测试）", "location": "121.480000,31.230000", "type": "住宿", "citycode": "021", "cityname": "上海市", "adcode": "310101"},
    {"id": "controlled-visit", "name": "受控景点甲", "address": "受控地址乙（合成测试）", "location": "121.482000,31.232000", "type": "景点", "citycode": "021", "cityname": "上海市", "adcode": "310101"},
    {"id": "controlled-park", "name": "受控公园甲", "address": "受控地址丙（合成测试）", "location": "121.483000,31.231000", "type": "公园", "citycode": "021", "cityname": "上海市", "adcode": "310101"},
)


class ControlledError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _new(path: Path, value: Any) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(canonical(value) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


def admin_url(raw: str):
    try:
        url = make_url(raw)
        if (url.drivername != "mysql+pymysql" or url.host != "127.0.0.1" or url.port != 13306
                or url.database != "trippilot_test_control" or url.query or not url.username):
            raise ValueError()
        return url
    except Exception:
        raise ControlledError("independent_test_mysql_required") from None


def _private_root(path: Path, *, new: bool = False) -> Path:
    if (not path.is_absolute() or ".." in path.parts or path.is_symlink()
            or any(parent.is_symlink() for parent in path.parents)
            or path.resolve().is_relative_to(ROOT)):
        raise ControlledError("private_external_directory_required")
    if new:
        path.mkdir(mode=0o700, exist_ok=False)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ControlledError("private_external_directory_required")
    return path


def prepare(path: Path, raw_admin: str) -> dict[str, Any]:
    """Exclusive new DB/identity only; retain partial resources, never reset old."""
    url = admin_url(raw_admin)
    _private_root(path, new=True)
    database, username = "trippilot_test_" + uuid4().hex, "tpec_" + secrets.token_hex(8)
    password, identity = secrets.token_urlsafe(32), uuid4().hex
    runtime = url.set(database=database, username=username, password=password).update_query_dict({"charset": "utf8mb4"})
    _new(path / "resources.json", {"schema_version": VERSION, "database": database, "state": "preparing",
                                  "source_database_read": False, "automatic_cleanup": False})
    _new(path / "runtime.json", {"schema_version": VERSION,
        "database_url": runtime.render_as_string(hide_password=False), "origin": ORIGIN})
    descriptor = os.open(path / "usage.identity", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(identity + "\n"); handle.flush(); os.fsync(handle.fileno())
    admin = create_engine(url, isolation_level="AUTOCOMMIT", hide_parameters=True)
    engine = None
    try:
        with admin.connect() as db:
            db.execute(text(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"))
            db.execute(text("CREATE USER :user@'127.0.0.1' IDENTIFIED BY :password"), {"user": username, "password": password})
            db.execute(text(f"GRANT SELECT,INSERT,UPDATE,DELETE ON `{database}`.* TO :user@'127.0.0.1'"), {"user": username})
        engine = create_engine(url.set(database=database).update_query_dict({"charset": "utf8mb4"}),
                               hide_parameters=True, isolation_level="READ COMMITTED")
        initialize(engine, path / "usage.identity")
        _new(path / "prepared.json", {"schema_version": VERSION, "state": "prepared",
            "database": database, "real_model_calls": 0, "real_amap_calls": 0,
            "synthetic_upstreams": True, "runtime_permissions": "SELECT,INSERT,UPDATE,DELETE"})
        return {"state": "prepared", "real_model_calls": 0, "real_amap_calls": 0}
    except Exception:
        _new(path / "failed.json", {"schema_version": VERSION, "state": "failed_preserve_resources"})
        raise ControlledError("preparation_failed_preserve_resources") from None
    finally:
        if engine is not None:
            engine.dispose()
        admin.dispose()


def initialize(engine: Engine, anchor: Path) -> None:
    """Explicit independent fixture provisioning, never application startup DDL."""
    from memory.mysql_storage import initialize_private_schema
    from memory.mysql_usage import initialize_usage_schema
    from tools.chat_storage import provision_chat_extension
    from tools.chat_places_storage import provision_chat_places_extension
    from tools.assistant_storage import provision_assistant_extension
    if (engine.dialect.name != "mysql" or re.fullmatch(r"trippilot_test_[a-f0-9]{32}", engine.url.database or "") is None):
        raise ControlledError("independent_test_mysql_required")
    identity = anchor.read_text(encoding="ascii")
    if re.fullmatch(r"[a-f0-9]{32}\n", identity) is None:
        raise ControlledError("independent_anchor_required")
    initialize_private_schema(engine)
    initialize_usage_schema(engine, identity.strip())
    provision_chat_extension(engine)
    provision_chat_places_extension(engine)
    provision_assistant_extension(engine)


def native_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"model": "qwen3.7-flash-2026-07-15", "id": "controlled_response", "choices": [{"index": 0,
        "finish_reason": "tool_calls", "message": {"role": "assistant", "content": None,
        "tool_calls": [{"id": "controlled_tool", "type": "function", "function": {
            "name": name, "arguments": canonical(arguments).decode()}}]}}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 10}}


def native_text() -> dict[str, Any]:
    return {"model": "qwen3.7-flash-2026-07-15", "id": "controlled_feedback", "choices": [{"index": 0,
        "finish_reason": "stop", "message": {"role": "assistant", "content": "CONTROLLED_FREE_PROSE_MUST_NOT_BE_SHOWN"}}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 10}}


class ControlledEventJournal:
    """Exclusive per-serve append receipt, fsynced before simulated responses.

    This is evidence, not a quota ledger or authority to send. There is no
    truncate/resume/reset API. Old incomplete files stay intact. A failed write
    latches this instance closed to further simulated upstream responses.
    """
    def __init__(self, directory: Path):
        _private_root(directory)
        self.path = directory / ("events-" + uuid4().hex + ".jsonl")
        self._failed = False
        self._count, self._bytes = 0, 0
        self._lock = threading.Lock()
        self._descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        header = {"schema_version": VERSION, "type": "controlled_evidence_session", "real_upstream_calls": 0}
        try:
            self._append_line(header)
            parent = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        except BaseException:
            os.close(self._descriptor)
            self._descriptor = -1
            raise
        self._previous = hashlib.sha256(canonical(header)).hexdigest()

    def _append_line(self, value: dict[str, Any]) -> None:
        raw = canonical(value) + b"\n"
        if len(raw) > MAX_EVENT_LINE_BYTES or self._bytes + len(raw) > MAX_EVENT_FILE_BYTES:
            raise ControlledError("controlled_evidence_unavailable")
        info = os.fstat(self._descriptor)
        named = self.path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_size != self._bytes
                or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)):
            raise ControlledError("controlled_evidence_unavailable")
        view = memoryview(raw)
        while view:
            written = os.write(self._descriptor, view)
            if written <= 0:
                raise ControlledError("controlled_evidence_unavailable")
            view = view[written:]
        os.fsync(self._descriptor)
        self._bytes += len(raw)

    def append(self, event: dict[str, Any]) -> None:
        with self._lock:
            try:
                if self._failed or self._descriptor < 0 or self._count >= MAX_EVENTS or type(event) is not dict:
                    raise ValueError()
                service = event.get("service")
                if service == "controlled_model":
                    valid = (set(event) == {"service", "payload_sha256", "native_tool_feedback"}
                        and type(event["payload_sha256"]) is str and re.fullmatch(r"[a-f0-9]{64}", event["payload_sha256"]) is not None
                        and type(event["native_tool_feedback"]) is bool)
                elif service == "controlled_poi":
                    valid = set(event) == {"service", "count"} and type(event["count"]) is int and 0 <= event["count"] <= 20
                else:
                    valid = service == "controlled_walking" and set(event) == {"service"}
                if not valid:
                    raise ValueError()
                row = {"ordinal": self._count + 1, "previous_sha256": self._previous, "event": dict(event)}
                self._append_line(row)
                self._previous = hashlib.sha256(canonical(row)).hexdigest()
                self._count += 1
            except Exception:
                self._failed = True
                raise ControlledError("controlled_evidence_unavailable") from None

    def close(self) -> None:
        with self._lock:
            if self._descriptor >= 0:
                os.close(self._descriptor)
                self._descriptor = -1


def scenario_tools() -> dict[str, tuple[str, dict[str, Any]]]:
    """Exact controlled inputs only. This is NOT runtime intent recognition."""
    from core.schemas.chat import Intent
    requirements = Intent(destination_id="shanghai", start_date="2026-11-01", end_date="2026-11-02", travelers=2,
        budget_cny="3000", accommodation_hint="受控住宿甲", required_place_hints=["受控景点甲"], transport_mode="walking").model_dump(mode="json")
    result = {REQUIREMENTS_TEXT: ("update_trip_requirements", {"requirements": requirements,
        "sources": [{"field": field, "quote": quote} for field, quote in (
            ("destination_id", "上海"), ("start_date", "2026-11-01"), ("end_date", "2026-11-02"),
            ("travelers", "两个人"), ("budget_cny", "3000元"), ("accommodation_hint", "受控住宿甲"),
            ("required_place_hints", "受控景点甲"), ("transport_mode", "步行"))], "issues": []}),
        "还没确定去哪和哪天，请先问我。": ("travel_clarify", {"reason": "destination_missing"}),
        "查看当前需求": ("inspect_trip", {}),
        "查询本次费用和未知项": ("travel_budget", {"action": "read_budget"}),
        "看看第二天安排": ("travel_schedule", {"action": "read_schedule", "date": None, "day": 2}),
    }
    for message, group, action, arguments in (
        ("确认需求", "travel_workspace", "confirm_requirements", {}),
        ("准备行程", "travel_workspace", "prepare_planning", {}),
        ("主动生成行程", "travel_schedule", "generate_schedule", {}),
        ("搜索受控住宿甲作为住宿", "travel_places", "search_places", {"keyword": "受控住宿甲", "role": "accommodation"}),
        ("搜索受控景点甲作为必去", "travel_places", "search_places", {"keyword": "受控景点甲", "role": "required"}),
        ("预览第1个地点", "travel_places", "preview_place", {"number": 1}),
        ("确认第1个地点", "travel_places", "confirm_place", {"number": 1}),
        ("准备可选候选", "travel_places", "prepare_candidates", {}),
        ("预览排除受控公园甲", "travel_adjustment", "preview_exclusion", {"selector": "受控公园甲"}),
        ("取消调整预览", "travel_adjustment", "cancel_preview", {}),
        ("采用调整预览", "travel_adjustment", "adopt_preview", {}),
        ("门票估算填0", "travel_budget", "set_cost", {"kind": "ticket", "selector": "受控景点甲", "value": "0"}),
        ("首日餐饮估算100", "travel_budget", "set_cost", {"kind": "meal", "selector": "2026-11-01", "value": "100"}),
        ("保存受控验收行程", "travel_archives", "save_trip", {"name": "Echo完整迁移受控验收"}),
        ("查看历史行程", "travel_archives", "list_saved", {"offset": 0}),
    ):
        result[message] = (group, {"action": action, **arguments})
    validate_scenarios(result)
    return result


def validate_scenarios(scenarios: dict[str, tuple[str, dict[str, Any]]]) -> None:
    """Fail fixed test material before sending, never relax production schemas."""
    from core.schemas.chat import Selection
    from core.schemas.dialogue import GROUPS, validate_action
    from echomind_core.travel_bridge import CLARIFICATIONS
    for statement, (name, arguments) in scenarios.items():
        if not isinstance(statement, str) or not statement or not isinstance(arguments, dict):
            raise ControlledError("controlled_plan_invalid")
        if name in GROUPS:
            if arguments.get("action") not in GROUPS[name][1]:
                raise ControlledError("controlled_plan_invalid")
            validate_action(arguments["action"], {key: value for key, value in arguments.items() if key != "action"})
        elif name == "update_trip_requirements":
            Selection.model_validate(arguments)
        elif name == "travel_clarify":
            if set(arguments) != {"reason"} or arguments["reason"] not in CLARIFICATIONS:
                raise ControlledError("controlled_plan_invalid")
        elif name != "inspect_trip" or arguments:
            raise ControlledError("controlled_plan_invalid")


class ControlledUpstream:
    def __init__(self, responses: list[Any] | None = None, *, journal: ControlledEventJournal | None = None):
        self.responses = responses
        self.journal = journal
        self.events: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.entered, self.release = threading.Event(), threading.Event()
        self.pause_next = False

    def _record(self, event: dict[str, Any]) -> None:
        with self.lock:
            if len(self.events) >= MAX_EVENTS:
                raise ControlledError("controlled_evidence_unavailable")
            if self.journal is not None:
                self.journal.append(event)
            self.events.append(dict(event))

    def summary(self) -> dict[str, Any]:
        with self.lock:
            return {"schema_version": VERSION, "real_model_calls": 0, "real_amap_calls": 0,
                    "events": list(self.events), "synthetic_upstreams": True}

    async def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "dashscope.aliyuncs.com" and request.url.path == "/compatible-mode/v1/chat/completions" and request.method == "POST":
            payload = strict_json(request.content)
            self._record({"service": "controlled_model", "payload_sha256": hashlib.sha256(request.content).hexdigest(),
                "native_tool_feedback": payload["messages"][-1]["role"] == "tool"})
            if self.pause_next:
                self.pause_next = False
                self.entered.set()
                while not self.release.is_set():
                    await asyncio.sleep(.005)
            if self.responses is not None:
                if not self.responses:
                    raise ControlledError("controlled_response_plan_exhausted")
                response = self.responses.pop(0)
                if isinstance(response, BaseException):
                    raise response
                return httpx.Response(200, json=response)
            if payload["messages"][-1]["role"] == "tool":
                return httpx.Response(200, json=native_text())
            user = payload["messages"][-1].get("content")
            selected = scenario_tools().get(user) if isinstance(user, str) else None
            if selected is None:
                raise ControlledError("controlled_question_not_in_plan")
            return httpx.Response(200, json=native_tool(*selected))
        if request.url.host == "restapi.amap.com" and request.method == "GET":
            if request.url.path == "/v5/place/text":
                keyword = request.url.params.get("keywords", "")
                rows = [row for row in PLACE_ROWS if row["name"] == keyword]
                if not rows and keyword in {"公园", "风景名胜", "上海公园", "公园广场", "旅游景点"}:
                    rows = [PLACE_ROWS[2]]
                self._record({"service": "controlled_poi", "count": len(rows)})
                return httpx.Response(200, json={"status": "1", "pois": rows})
            if request.url.path == "/v3/direction/walking":
                origin, destination = request.url.params.get("origin"), request.url.params.get("destination")
                allowed = {row["location"] for row in PLACE_ROWS}
                if origin not in allowed or destination not in allowed:
                    raise ControlledError("controlled_endpoints_not_in_plan")
                self._record({"service": "controlled_walking"})
                return httpx.Response(200, json={"status": "1", "route": {"paths": [{"distance": "300", "duration": "360", "steps": [{"polyline": origin + ";" + destination}]}]}})
        raise ControlledError("external_request_forbidden")


@contextmanager
def controlled_app(engine: Engine, anchor: Path, *, origin: str = ORIGIN,
                   upstream: ControlledUpstream | None = None) -> Iterator[tuple[Any, ControlledUpstream]]:
    """Use production handlers with true session/store/metering; transport only fake."""
    from main import app
    from core import auth
    from core.config import Settings, get_settings
    from core.business_usage import SERVICES, UsageSettings
    from core.usage_runtime import MeteredAsyncClient
    from memory.chat_storage import ChatStore
    from memory.chat_places_storage import ChatPlacesStore
    from memory.assistant_store import AssistantStore
    from memory.mysql_usage import MySQLUsageStore
    from memory.saved_trips import get_session
    from api.chat import get_chat_store
    from api.chat_places import get_chat_places_store
    from api.assistant import get_assistant_store
    from api.explanation import get_qwen_http_client
    from api.places import get_http_client
    from api.amap_proxy import get_proxy_http_client
    if engine.dialect.name != "mysql" or re.fullmatch(r"trippilot_test_[a-f0-9]{32}", engine.url.database or "") is None:
        raise ControlledError("independent_test_mysql_required")
    if re.fullmatch(r"http://127\.0\.0\.1:(?:3112|3000)", origin) is None:
        raise ControlledError("controlled_origin_required")
    auth_settings = auth.AuthSettings(_env_file=None, auth_cookie_secure=False, auth_allowed_origins=(origin,))
    settings = Settings(_env_file=None, qwen_api_key="controlled-not-real", amap_web_key="controlled-not-real",
        amap_js_key="", amap_js_security_code="",
        qwen_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", assistant_runtime="echomind_travel_v1")
    if getattr(settings, "assistant_runtime", None) != "echomind_travel_v1":
        raise ControlledError("candidate_switch_not_ready")
    usage_settings = UsageSettings(_env_file=None, business_usage_identity_path=str(anchor),
        business_daily_limits={service: {"account": 1000 if service in {"qwen_text", "amap_poi", "amap_route"} else 0,
                                        "global": 2000 if service in {"qwen_text", "amap_poi", "amap_route"} else 0} for service in SERVICES})
    usage = MySQLUsageStore(usage_settings, engine=engine)
    usage.verify()
    upstream = upstream or ControlledUpstream()

    async def http_dependency():
        async with MeteredAsyncClient(transport=httpx.MockTransport(upstream.handle), follow_redirects=False, trust_env=False) as client:
            yield client

    def session_dependency():
        with Session(engine) as session:
            yield session

    async def forbidden_default_transport(*_arguments: Any, **_keywords: Any):
        raise ControlledError("uncontrolled_network_forbidden")

    overrides = {get_settings: lambda: settings, get_chat_store: lambda: ChatStore(engine),
        get_chat_places_store: lambda: ChatPlacesStore(engine), get_assistant_store: lambda: AssistantStore(engine),
        get_session: session_dependency, get_qwen_http_client: http_dependency,
        get_http_client: http_dependency, get_proxy_http_client: http_dependency}
    with ExitStack() as stack:
        for module in ("core.auth", "core.workspace_identity", "api.workspace"):
            stack.enter_context(patch(module + ".get_auth_settings", return_value=auth_settings))
        stack.enter_context(patch("core.workspace_identity.mysql_configured", return_value=True))
        stack.enter_context(patch("core.workspace_identity.current_mysql_engine", return_value=engine))
        stack.enter_context(patch("memory.mysql_storage._configured_url", return_value=engine.url.render_as_string(hide_password=False)))
        for module in ("core.usage_runtime", "core.business_usage", "api.usage"):
            stack.enter_context(patch(module + ".get_usage_store", return_value=usage))
        stack.enter_context(patch("httpx.AsyncHTTPTransport.handle_async_request", forbidden_default_transport))
        stack.enter_context(patch.dict(app.dependency_overrides, overrides))
        yield app, upstream


def serve(path: Path, port: int) -> None:
    import uvicorn
    _private_root(path)
    config_file = path / "runtime.json"
    info = config_file.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or config_file.is_symlink()
            or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 8192):
        raise ControlledError("private_runtime_required")
    config = strict_json(config_file.read_bytes())
    if set(config) != {"schema_version", "database_url", "origin"} or config["schema_version"] != VERSION:
        raise ControlledError("controlled_runtime_invalid")
    url = make_url(config["database_url"])
    if (url.drivername != "mysql+pymysql" or url.host != "127.0.0.1" or url.port != 13306
            or re.fullmatch(r"trippilot_test_[a-f0-9]{32}", url.database or "") is None
            or re.fullmatch(r"tpec_[a-f0-9]{16}", url.username or "") is None or not url.password
            or dict(url.query) != {"charset": "utf8mb4"} or port != 8812):
        raise ControlledError("controlled_runtime_invalid")
    engine = create_engine(url, hide_parameters=True, isolation_level="READ COMMITTED", pool_pre_ping=True)
    journal = ControlledEventJournal(path)
    upstream = ControlledUpstream(journal=journal)
    try:
        with controlled_app(engine, path / "usage.identity", origin=config["origin"], upstream=upstream) as (app, _):
            uvicorn.run(app, host="127.0.0.1", port=port, access_log=False, log_level="warning")
    finally:
        try:
            _new(path / ("counts-" + uuid4().hex + ".json"), upstream.summary())
        finally:
            journal.close()
            engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--output", type=Path, required=True)
    run = sub.add_parser("serve")
    run.add_argument("--directory", type=Path, required=True)
    run.add_argument("--port", type=int, default=8812)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.output, os.environ.get("TRIPPILOT_TEST_MYSQL_ADMIN_URL", ""))
            print(json.dumps(result))
        else:
            serve(args.directory, args.port)
    except Exception:
        raise SystemExit("Controlled harness stopped safely; preserve resources. No real upstream fallback.") from None


if __name__ == "__main__":
    main()

"""Opt-in release-1B recorder: real HTTP, unchanged adapters, one submission/case.

Never imports or calls a model. Only the explicit `serve` command installs the
recorder on the normal app; production startup remains main:app.
"""
import argparse
from collections.abc import AsyncIterator
from contextvars import ContextVar
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import subprocess
import time

import httpx
import uvicorn

from api.places import get_http_client
from core.config import get_settings
from tooling.providers.amap import REQUEST_TIMEOUT
from core.schemas.destination import CityScheduleRequest

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT.parent / "trippilot-evidence/release-1b/batch-20261018"
LIMIT = 4 * 1024 * 1024  # Evidence limit only, never a product response limit.
PATHS = {"/v5/place/text", "/v3/direction/transit/integrated", "/v3/direction/walking"}
SOURCE_LAYOUT = "echo-v1"
FILES = ["backend/tooling/providers/amap.py", "backend/tooling/providers/amap_transit.py",
         "backend/tooling/providers/amap_walking.py", "backend/tooling/travel/schedule_routes.py",
         "backend/tooling/travel/schedule.py", "backend/api/schedule.py",
         "frontend/src/components/trip/schedule-preview.tsx", "frontend/src/lib/schedule-blocker.ts",
         "backend/tools/traffic_acceptance.py", "backend/tests/test_traffic_acceptance.py"]
CASES = [
    {"id": "c01", "city": "shanghai", "mode": "transit", "date": "2026-10-20", "end": "18:00",
     "searches": ["静安寺 地铁站", "豫园", "东方明珠广播电视塔"], "stays": [90, 90]},
    {"id": "c02", "city": "shanghai", "mode": "transit", "date": "2026-10-21", "end": "20:00",
     "searches": ["静安寺 地铁站", "上海迪士尼度假区"], "stays": [180]},
    {"id": "c03", "city": "beijing", "mode": "walking", "date": "2026-10-22", "end": "18:00",
     "searches": ["天坛东门 地铁站", "天坛公园"], "stays": [60]},
]
CONTEXT: ContextVar[str] = ContextVar("traffic_acceptance_case", default="search")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def write_new(path: Path, value: object) -> None:
    raw = canonical(value)
    if len(raw) > LIMIT:
        raise ValueError("Evidence too large; never silently truncate")
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def append(directory: Path, value: dict) -> None:
    with (directory / "attempts.jsonl").open("a", encoding="utf8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps({"time": datetime.now(timezone.utc).isoformat(), **value}, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def scrub(value: object, secrets: tuple[str, ...]) -> object:
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[redacted]")
        return value
    if isinstance(value, list):
        return [scrub(item, secrets) for item in value]
    if isinstance(value, dict):
        return {scrub(key, secrets): scrub(item, secrets) for key, item in value.items()
                if key.lower() not in {"key", "jscode", "authorization"}}
    return value


def freeze(directory: Path = DIRECTORY) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    write_new(directory / "protocol.json", {
        "stage": "release-1b", "source_layout": SOURCE_LAYOUT, "cases": CASES, "model_calls": 0,
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "files": {path: digest((ROOT / path).read_bytes()) for path in FILES},
        "build_id": (ROOT / "frontend/.next/BUILD_ID").read_text().strip(),
        "rules": {"daily_start": "09:00", "lunch": ["12:00", "13:00"], "optional": [],
                  "one_schedule_submission_per_case": True, "no_automatic_rerun": True,
                  "search_requests": 7, "logical_routes": 9, "bounded_http_attempts": 32,
                  "existing_retry_only": "connection failure once; observed separately"},
        "criteria": "Exact POIs confirmed from real search; complete snapshots before submit; outcomes retained, no sample substitution; all new cases, never original-case reproduction.",
    })


def verify(directory: Path) -> dict:
    protocol = json.loads((directory / "protocol.json").read_bytes())
    # Old receipts keep their own exact path/hash manifest. Never map them to
    # new source paths or refresh hashes to make a historical freeze pass.
    files = protocol.get("files")
    if (protocol.get("cases") != CASES or not isinstance(files, dict) or not files
            or protocol.get("source_layout") not in (None, SOURCE_LAYOUT)
            or protocol.get("source_layout") == SOURCE_LAYOUT and set(files) != set(FILES)):
        raise ValueError("Frozen business version changed")
    try:
        if any(not isinstance(p, str) or not isinstance(h, str) or digest((ROOT / p).read_bytes()) != h
               for p, h in files.items()):
            raise ValueError("Frozen business version changed")
    except OSError:
        raise ValueError("Frozen business version changed") from None
    if (ROOT / "frontend/.next/BUILD_ID").read_text().strip() != protocol["build_id"]:
        raise ValueError("Frozen production build changed")
    return protocol


def freeze_repair(directory: Path = DIRECTORY) -> None:
    """One announced c02 post-fix recheck, never rewrite the initial freeze."""
    original = json.loads((directory / "protocol.json").read_bytes())
    reviewed_changes = {"backend/tooling/providers/amap_transit.py",
                        "backend/tools/traffic_acceptance.py", "backend/tests/test_traffic_acceptance.py"}
    try:
        if any(digest((ROOT / p).read_bytes()) != h for p, h in original["files"].items() if p not in reviewed_changes):
            raise ValueError("Unreviewed frozen file changed")
    except OSError:
        raise ValueError("Unreviewed frozen file changed") from None
    if (ROOT / "frontend/.next/BUILD_ID").read_text().strip() != original["build_id"]:
        raise ValueError("Production build changed")
    for case in CASES:
        for suffix in ["input", "attempt", "response"]:
            if not (directory / f"{case['id']}-{suffix}.json").is_file():
                raise ValueError("Initial outcomes must be preserved before recheck")
    target = directory / "repair-c02"
    target.mkdir(exist_ok=False)
    write_new(target / "protocol.json", {
        **original,
        "source_layout": SOURCE_LAYOUT,
        "files": {p: digest((ROOT / p).read_bytes()) for p in FILES},
        "purpose": "One c02 unchanged-input post-fix UI recheck: two original directed transit edges; no other case rerun/search/model/DB.",
        "original_protocol_hash": digest((directory / "protocol.json").read_bytes()),
        "original_ledger_hash": digest((directory / "attempts.jsonl").read_bytes()),
        "original_records": {f"{c['id']}-{s}.json": digest((directory / f"{c['id']}-{s}.json").read_bytes())
                             for c in CASES for s in ["input", "attempt", "response"]},
        "reviewed_changes": sorted(reviewed_changes),
        "limits": {"schedule_submissions": 1, "logical_routes": 2, "bounded_http_attempts": 4},
    })
    write_new(target / "c02-input.json", json.loads((directory / "c02-input.json").read_bytes()))
    (target / "armed.txt").write_text("c02")


def verify_repair(directory: Path) -> dict:
    protocol = verify(directory)
    parent = directory.parent
    if (digest((parent / "protocol.json").read_bytes()) != protocol["original_protocol_hash"]
            or digest((parent / "attempts.jsonl").read_bytes()) != protocol["original_ledger_hash"]
            or any(digest((parent / p).read_bytes()) != h for p, h in protocol["original_records"].items())
            or (directory / "armed.txt").read_text() != "c02"
            or json.loads((directory / "c02-input.json").read_bytes()) != json.loads((parent / "c02-input.json").read_bytes())):
        raise ValueError("Original evidence or fixed repair input changed")
    return protocol


def replay_transit(directory: Path = DIRECTORY) -> None:
    """Offline replay only. Read saved upstream bodies; never create an HTTP client."""
    from tooling.providers.amap_transit import _convert_route
    from core.schemas.schedule import ScheduleEdge
    from tooling.travel.schedule import build_schedule
    rows = [json.loads(line) for line in (directory / "attempts.jsonl").read_text().splitlines()]
    for case_id in ["c01", "c02"]:
        original = json.loads((directory / f"{case_id}-response.json").read_bytes())["body"]["schedule"]
        request = CityScheduleRequest.model_validate(json.loads((directory / f"{case_id}-input.json").read_bytes()))
        edges, references = [], []
        for old in original["edges"]:
            key = lambda endpoint: f"{endpoint['longitude']:.6f},{endpoint['latitude']:.6f}"
            matches = [r for r in rows if r["event"] == "http_started" and r["case"] == case_id
                       and r["parameters"].get("origin") == key(old["origin"])
                       and r["parameters"].get("destination") == key(old["destination"])]
            if len(matches) != 1:
                raise ValueError("Replay requires exactly one observed attempt for each directed edge")
            completion = next(r for r in rows if r.get("operation") == matches[0]["operation"] and r["event"] == "http_completed")
            artifact = directory / completion["artifact"]
            status, route = _convert_route(json.loads(artifact.read_bytes()))
            if status != "ok" or route is None:
                raise ValueError("Replay did not produce a valid plan; retain original failure")
            edges.append(ScheduleEdge.model_validate({
                **old, "status": "ok", "message": None, "duration_seconds": route.duration_seconds,
                "duration_minutes": math.ceil(route.duration_seconds / 60), "transit_route": route,
                "selection_rule": "first_supported_complete",
            }))
            references.append({"edge": old["id"], "artifact": artifact.name, "hash": digest(artifact.read_bytes())})
        replayed = build_schedule(request, edges, datetime.fromisoformat(original["generated_at"]))
        write_new(directory / f"{case_id}-offline-replay.json", {
            "kind": "saved-real-upstream-offline-replay-not-new-live-verification",
            "replayed_at": datetime.now(timezone.utc).isoformat(),
            "adapter_hash": digest((ROOT / "backend/tooling/providers/amap_transit.py").read_bytes()),
            "original_response_hash": digest((directory / f"{case_id}-response.json").read_bytes()),
            "upstream_references": references, "schedule": replayed.model_dump(mode="json"),
        })


def prepare(case_id: str, payload: dict, directory: Path = DIRECTORY) -> None:
    verify(directory)
    case = next(c for c in CASES if c["id"] == case_id)
    request = CityScheduleRequest.model_validate(payload)
    if (request.destination_id != case["city"] or request.transport_mode != case["mode"]
            or str(request.start_date) != case["date"] or str(request.end_date) != case["date"]
            or request.daily_start_time != "09:00" or request.daily_end_time != case["end"]
            or request.optional_places or len(request.must_visit_places) != len(case["stays"])
            or [s.minutes for s in request.duration_settings] != case["stays"]
            or request.lunch.model_dump() != {"enabled": True, "start_time": "12:00", "end_time": "13:00"}):
        raise ValueError("Input differs from predefined case")
    write_new(directory / f"{case_id}-input.json", payload)
    # Arm changes are acceptance-only, never part of the user's planning state.
    (directory / "armed.txt").write_text(case_id)


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, directory: Path, secrets: tuple[str, ...], inner: httpx.AsyncBaseTransport | None = None):
        self.directory, self.secrets = directory, secrets
        self.inner = inner or httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != "restapi.amap.com" or request.url.path not in PATHS:
            raise ValueError("Unexpected upstream in acceptance run")
        operation = os.urandom(8).hex()
        params = {k: v for k, v in request.url.params.multi_items() if k in {
            "origin", "destination", "origin_id", "destination_id", "city", "cityd", "region", "keywords", "page_size", "page_num"}}
        append(self.directory, {"event": "http_started", "operation": operation, "case": CONTEXT.get(),
                                "path": request.url.path, "parameters": scrub(params, self.secrets)})
        started = time.monotonic()
        try:
            response = await self.inner.handle_async_request(request)
            await response.aread()  # Same body, cached by httpx; never replaced.
            artifact = None
            if "/direction/" in request.url.path:
                artifact = f"upstream-{operation}.json"
                if len(response.content) <= LIMIT:
                    try:
                        body = scrub(response.json(), self.secrets)
                    except ValueError:
                        body = {"capture": "invalid_json", "body_hash": digest(response.content)}
                else:
                    body = {"capture": "over_evidence_limit", "bytes": len(response.content), "body_hash": digest(response.content)}
                write_new(self.directory / artifact, body)
            append(self.directory, {"event": "http_completed", "operation": operation, "case": CONTEXT.get(),
                                    "status": response.status_code, "seconds": time.monotonic() - started, "artifact": artifact})
            return response
        except BaseException as error:
            append(self.directory, {"event": "http_failed", "operation": operation, "case": CONTEXT.get(),
                                    "category": "cancelled" if isinstance(error, BaseException) and type(error).__name__ == "CancelledError" else "transport_or_recording_error",
                                    "seconds": time.monotonic() - started})
            raise

    async def aclose(self) -> None:
        await self.inner.aclose()


class Gate:
    def __init__(self, application, directory: Path, *, repair: bool = False):
        self.app, self.directory, self.repair = application, directory, repair

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] == "OPTIONS":
            return await self.app(scope, receive, send)
        path = scope["path"]
        allowed = {"/health", "/trips/schedule-preview"} if self.repair else {"/health", "/places/search", "/trips/schedule-preview"}
        if path not in allowed:
            from starlette.responses import JSONResponse
            return await JSONResponse({"detail": "Acceptance scope excludes this operation"}, status_code=403)(scope, receive, send)
        if path != "/trips/schedule-preview":
            return await self.app(scope, receive, send)
        (verify_repair if self.repair else verify)(self.directory)
        raw = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            raw.extend(message.get("body", b""))
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError("Acceptance request limit")
            if not message.get("more_body", False):
                break
        case_id = (self.directory / "armed.txt").read_text().strip()
        expected = json.loads((self.directory / f"{case_id}-input.json").read_bytes())
        if json.loads(raw) != expected:
            raise ValueError("Submitted input differs from frozen exact POIs/settings")
        # Exclusive creation precedes route dispatch; duplicate or uncertain attempts cannot resend.
        write_new(self.directory / f"{case_id}-attempt.json", {"input_hash": digest(canonical(expected)), "started_at": datetime.now(timezone.utc).isoformat()})
        token = CONTEXT.set(case_id)
        served, output = False, bytearray()
        status = 0
        async def replay():
            nonlocal served
            if not served:
                served = True
                return {"type": "http.request", "body": bytes(raw), "more_body": False}
            return await receive()
        async def record(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            if message["type"] == "http.response.body":
                output.extend(message.get("body", b""))
                if len(output) > LIMIT:
                    raise ValueError("Acceptance response evidence limit")
                if not message.get("more_body", False):
                    write_new(self.directory / f"{case_id}-response.json", {"http_status": status, "body": json.loads(output)})
            await send(message)
        try:
            await self.app(scope, replay, record)
        finally:
            CONTEXT.reset(token)


def serve(directory: Path = DIRECTORY, *, repair: bool = False) -> None:
    (verify_repair if repair else verify)(directory)
    from main import app
    settings = get_settings()
    secrets = tuple(getattr(settings, name).get_secret_value() for name in ["amap_web_key", "amap_js_key", "amap_js_security_code", "qwen_api_key"])
    async def client() -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient(transport=RecordingTransport(directory, secrets), timeout=REQUEST_TIMEOUT) as value:
            yield value
    class DiagnosticHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            append(directory, {"event": "diagnostic", "case": CONTEXT.get(), "logger": record.name,
                               "message": str(scrub(record.getMessage(), secrets))[:1000]})
    for name in ["tooling.providers.amap_transit", "tooling.providers.amap_walking", "tooling.travel.schedule_routes"]:
        logging.getLogger(name).addHandler(DiagnosticHandler())
        logging.getLogger(name).setLevel(logging.INFO)
    app.dependency_overrides[get_http_client] = client
    uvicorn.run(Gate(app, directory, repair=repair), host="127.0.0.1", port=8000, access_log=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["freeze", "prepare", "verify", "serve", "freeze-repair", "serve-repair", "replay"])
    parser.add_argument("--case", choices=[c["id"] for c in CASES])
    parser.add_argument("--input", type=Path)
    args = parser.parse_args()
    if args.command == "freeze": freeze()
    elif args.command == "replay": replay_transit()
    elif args.command == "freeze-repair": freeze_repair()
    elif args.command == "serve-repair": serve(DIRECTORY / "repair-c02", repair=True)
    elif args.command == "verify": verify(DIRECTORY)
    elif args.command == "prepare": prepare(args.case, json.loads(args.input.read_bytes()))
    else: serve()

"""Private explicit full-runtime acceptance runner, never an application route.

Creates one new isolated fixture, freezes it, then waits for an exclusive local
start marker. All real sends pass the new journal and normal production quotas.
There is no retry, resume, reset, cloud switch, AMap sender or judging model.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
from typing import Any
from uuid import uuid4

import httpx
from fastapi import Request, HTTPException
from sqlalchemy import create_engine, text
import uvicorn

from core.config import Settings
from core.llm_utils import strict_json
from core.schemas.dialogue import SubmitDialogueTurn
from core.usage_runtime import bind_usage_context, reset_usage_context
from echomind_core.embedding import DIMENSIONS, EMBEDDING_MODEL
from echomind_core.execution import CandidateScope
from echomind_core.full_resources import FullResourceConfig, FullRuntimeServices
from echomind_core.integration_full import close_full_services
from echomind_core.runtime import CandidateResources
from echomind_core.storage import StorageConfig
from echomind_core.travel_capabilities import ReviewedKnowledgePort
from memory.assistant_store import AssistantStore
from memory.chat_places_storage import ChatPlacesStore
from memory.chat_storage import ChatStore
from tooling.providers.qwen import beijing_endpoint
from tooling.travel.knowledge_national import get_index
from tools.echomind_full_acceptance_fixture import full_fixture_app, seed, turn_body, write_new, digest
from tools.echomind_full_acceptance_ledger import FullAcceptanceLedger, FILENAME
from tools.echomind_full_acceptance_preparation import prepare_templates, prepare_index
from tools.echomind_full_acceptance_protocol import load_manifest, freeze, verify, ledger_binding, trusted_settings
from tools.echomind_full_acceptance_transport import full_acceptance_client
from tools.echomind_travel_controlled import prepare, ORIGIN

ROOT = Path(__file__).resolve().parents[2]
VERSION = "echomind-full-acceptance-runner-v1"


def read_private_document(path: Path, *, limit: int = 64 * 1024) -> dict:
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("private_evidence_path_required")
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > limit):
        raise ValueError("private_evidence_path_required")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError("evidence_changed")
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("evidence_limit")
    value = strict_json(raw)
    if not isinstance(value, dict):
        raise ValueError("private_document_required")
    return value


def checked_request(case: dict, fixture: dict, body: dict, conversation_id: str) -> dict:
    checked = SubmitDialogueTurn.model_validate(body).model_dump(mode="json")
    expected = fixture["conversation"]
    if (conversation_id != expected["id"] or checked["text"] != case["text"]
            or checked["expected_revision"] != expected["revision"]
            or checked["dialogue_context"] is None):
        raise ValueError("unplanned_full_acceptance_request")
    if case.get("channel", "api") == "api" and (checked["operation_id"] != fixture["operation_id"]
            or digest(checked["dialogue_context"]) != digest(fixture["selection_context"])):
        raise ValueError("unplanned_full_acceptance_request")
    # Browser operation/context UUIDs are captured immediately before first
    # sending; their exact bytes are never predicted or relabelled afterward.
    return checked


class Trial:
    def __init__(self, folder: Path, settings: Settings):
        self.folder, self.settings = folder, settings
        self.manifest = load_manifest()
        self.cases = self.manifest["operations"]
        self.position = 0
        self.ledger: FullAcceptanceLedger | None = None
        self.options: dict = {}
        self.fixtures: dict = {}
        self.identity: dict = {}
        self.observations: dict = {}
        self.request_bindings: dict = {}
        self.events = {case["id"]: asyncio.Event() for case in self.cases}
        self.bindings: dict = {}
        self.armed = False
        self.engine = None

    def case(self) -> dict:
        if self.position >= len(self.cases):
            raise ValueError("fixed_plan_finished")
        return self.cases[self.position]

    def tier(self) -> str:
        return self.case()["tier"] if self.position < len(self.cases) else "single"

    def verify(self) -> None:
        verify(self.folder / "protocol.json", **self.options)
        if self.ledger is not None:
            state = self.ledger.verify(binding=self.binding)
            if state.get("recovery_required"):
                raise ValueError("full_acceptance_uncertain_previous_writer")
            if state["paused"] or state["pending"]:
                # Existing sibling requests have their own pre-send check and
                # are permitted by this writer; new operations are not.
                if state["paused"]:
                    raise ValueError("full_acceptance_paused")

    def op_binding(self, op: str) -> str:
        if op not in self.bindings:
            self.bindings[op] = digest({"protocol_sha256": self.binding["protocol_sha256"],
                "owner_sha256": digest(self.identity["user"]["id"]), "operation": op,
                "fixture_sha256": self.options["fixture_binding"]["fixture"]})
        return self.bindings[op]

    @asynccontextmanager
    async def client_for(self, operation: str):
        if not self.armed or self.ledger is None:
            raise ValueError("explicit_start_required")
        observation = None
        failed = False
        try:
            async with full_acceptance_client(ledger=self.ledger, operation_id=operation,
                    binding_sha256=self.op_binding(operation), settings=self.settings, verify=self.verify) as value:
                observation = value[1]
                yield value
        except BaseException:
            failed = True
            raise
        finally:
            if observation is not None:
                self.observations[operation] = observation.records
                write_new(self.folder / f"{operation}-native.json", {"records": observation.records,
                    "body_exception": failed})

    async def qwen_dependency(self, raw: Request):
        """Only the next frozen assistant-turn is eligible for a model client."""
        if not self.armed or self.ledger is None:
            raise HTTPException(503, detail="full_acceptance_not_armed")
        case = self.case()
        fixture = self.fixtures[case["id"]]
        try:
            request = checked_request(case, fixture, await raw.json(), raw.path_params["conversation_id"])
            if raw.headers.get("X-TripPilot-Surface") != "dialogue-v3":
                raise ValueError()
        except Exception:
            self.ledger.pause(reason="execution_error")
            raise HTTPException(409, detail="full_acceptance_unplanned_request") from None
        operation = case["id"]
        captured = {"conversation_id": fixture["conversation"]["id"], "request": request,
            "frozen_fixture_sha256": self.options["fixture_binding"]["fixture"],
            "owner_sha256": digest(self.identity["user"]["id"])}
        self.bindings[operation] = digest(captured)
        self.request_bindings[operation] = captured
        write_new(self.folder / f"{operation}-request.json", captured)
        self.ledger.start_operation(operation, self.op_binding(operation))
        observation = None
        try:
            async with full_acceptance_client(ledger=self.ledger, operation_id=operation,
                    binding_sha256=self.op_binding(operation), settings=self.settings, verify=self.verify) as value:
                client, observation = value
                yield client
        finally:
            if observation is not None:
                self.observations[operation] = observation.records
                write_new(self.folder / f"{operation}-native.json", {"records": observation.records})
            state = self.ledger.summary()
            if not state["paused"] and not state["pending"]:
                task = await asyncio.to_thread(AssistantStore(self.engine).latest,
                    self.identity["user"]["id"], fixture["conversation"]["id"])
                outcome = "completed" if task and task["state"] in {"succeeded", "waiting"} else "failed"
                self.ledger.seal_operation(operation, business_outcome=outcome)
            self.events[operation].set()

    async def collect(self, api: httpx.AsyncClient, case: dict, response: httpx.Response | None = None) -> None:
        fixture = self.fixtures[case["id"]]
        cid = fixture["conversation"]["id"]
        current = await api.get(f"/chat/conversations/{cid}")
        receipt = await api.get(f"/chat/conversations/{cid}/assistant-state", headers={"X-TripPilot-Surface": "dialogue-v3"})
        actions = await api.get(f"/chat/conversations/{cid}/dialogue-action", headers={"X-TripPilot-Surface": "dialogue-v3"})
        places = await api.get(f"/chat/conversations/{cid}/places")
        record = {"schema_version": VERSION, "case_id": case["id"], "channel": case["channel"],
            "request_bound": self.request_bindings.get(case["id"]),
            "api_status": response.status_code if response is not None else None,
            "api_error": response.json() if response is not None and response.status_code != 200 else None,
            "conversation": current.json(), "task": receipt.json(), "actions": actions.json(),
            "places": places.json(), "real_amap_calls": 0, "synthetic_business": True,
            "semantic_review": "pending-human-readable-review", "ledger": self.ledger.summary()}
        write_new(self.folder / f"{case['id']}-result.json", record)
        if self.ledger.summary()["paused"]:
            raise ValueError("execution_pause")


async def wait_start(trial: Trial) -> None:
    ready = {"schema_version": VERSION, "state": "frozen_waiting_explicit_start",
        "protocol_sha256": trial.binding["protocol_sha256"], "grant_sha256": trial.binding["grant_sha256"],
        "max_physical_model_sends": 66, "actual_model_sends": 0, "real_amap_calls": 0,
        "fixture_conversations": {key: value["conversation"]["id"] for key, value in trial.fixtures.items()}}
    write_new(trial.folder / "ready.json", ready)
    print("FROZEN: zero sends; awaiting execute-once.json bound to protocol/grant", flush=True)
    deadline = asyncio.get_running_loop().time() + 1800
    while not (trial.folder / "execute-once.json").exists():
        if asyncio.get_running_loop().time() > deadline:
            raise ValueError("start_wait_expired_no_sends")
        await asyncio.sleep(.1)
    marker = read_private_document(trial.folder / "execute-once.json")
    if type(marker.get("execute")) is not bool or marker != {"execute": True, "protocol_sha256": ready["protocol_sha256"], "grant_sha256": ready["grant_sha256"]}:
        raise ValueError("start_marker_mismatch")
    trial.verify()
    write_new(trial.folder / "execution-started.json", {"protocol_sha256": ready["protocol_sha256"], "physical_limit": 66})
    trial.armed = True


async def run(folder: Path, prior_ledgers_path: Path) -> None:
    if folder.resolve().is_relative_to(ROOT) or not folder.is_absolute() or folder.exists():
        raise ValueError("exclusive_external_batch_directory_required")
    folder.mkdir(mode=0o700)
    secret_source = Settings()  # Never print/store full Settings or an environment file.
    if not secret_source.qwen_api_key.get_secret_value().strip():
        raise ValueError("qwen_key_missing")
    endpoint = beijing_endpoint(secret_source)
    prepare(folder / "resources", os.environ.get("TRIPPILOT_TEST_MYSQL_ADMIN_URL", ""))
    runtime = strict_json((folder / "resources/runtime.json").read_bytes())
    engine = create_engine(runtime["database_url"], hide_parameters=True, isolation_level="READ COMMITTED", pool_pre_ping=True)
    redis_folder = Path(tempfile.mkdtemp(prefix="tp-full-live-", dir="/private/tmp" if Path("/private/tmp").is_dir() else None)).resolve()
    binary = shutil.which("redis-server")
    if binary is None:
        engine.dispose()
        raise ValueError("private_redis_dependency_missing")
    redis = subprocess.Popen([binary, "--port", "0", "--unixsocket", str(redis_folder / "redis.sock"),
        "--unixsocketperm", "700", "--dir", str(redis_folder), "--appendonly", "yes",
        "--appendfsync", "always", "--maxmemory", "64mb", "--maxmemory-policy", "noeviction",
        "--save", "", "--logfile", str(redis_folder / "redis.log")],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if (redis_folder / "redis.sock").exists():
                break
            if redis.poll() is not None:
                raise ValueError("private_redis_failed")
            await asyncio.sleep(.02)
        else:
            raise ValueError("private_redis_start_timeout")
    except BaseException:
        if redis.poll() is None:
            redis.terminate()
            await asyncio.to_thread(redis.wait, 5)
        engine.dispose()
        raise
    trial = Trial(folder, secret_source)
    trial.engine = engine
    server, server_task = None, None
    try:
        template_root, index_root = folder / "templates", folder / "index"
        template_root.mkdir(mode=0o700)
        index_root.mkdir(mode=0o700)
        storage = StorageConfig("full-acceptance-v1", redis_folder / "redis.sock", folder / "chroma", EMBEDDING_MODEL, DIMENSIONS)
        settings = secret_source.model_copy(update={"assistant_runtime": "echomind_travel_full_v2",
            "assistant_full_template_root": template_root, "assistant_full_redis_socket": storage.redis_socket,
            "assistant_full_chroma_path": storage.chroma_path, "assistant_full_namespace": storage.namespace,
            "assistant_full_reference_city": "310000", "assistant_full_reference_venue": "museum_people",
            "assistant_full_vector_receipt_root": index_root})
        trial.settings = settings
        with full_fixture_app(engine, folder / "resources/usage.identity", settings,
                trial.qwen_dependency, trial.tier) as (app, business):
            write_new(folder / "owned-runtime.json", {"redis_pid": redis.pid,
                "redis_socket": str(redis_folder / "redis.sock"), "api_port": 8812,
                "frontend_port": 3112, "original_services_changed": False})
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as api:
                trial.identity, trial.fixtures, cookies = await seed(api, engine, trial.manifest)
                write_new(folder / "storage-state.json", {"cookies": cookies, "origins": []})
                write_new(folder / "fixtures.json", {"fixtures": trial.fixtures, "synthetic_business": True})
                fixture_hash = hashlib.sha256((folder / "fixtures.json").read_bytes()).hexdigest()
                grant = {"schema_version": "echomind-full-acceptance-grant-v1", "user_statement": "无限次授权你，只要给我都测到位",
                    "finite_batch_limit": 66, "operation_order": [row["id"] for row in trial.manifest["preparation"] + trial.cases],
                    "real_amap_calls": 0, "judge_calls": 0, "old_allowances_reused": False,
                    "fixture_sha256": fixture_hash, "automatic_retry": False}
                write_new(folder / "grant.json", grant)
                previous = read_private_document(prior_ledgers_path)
                if not previous or not all(isinstance(key, str) and isinstance(value, str) for key, value in previous.items()):
                    raise ValueError("prior_ledger_paths_required")
                trial.options = {"fixture_binding": {"fixture": fixture_hash},
                    "prior_ledgers": {label: Path(path) for label, path in previous.items()},
                    "settings_summary": trusted_settings(endpoint)}
                freeze(folder / "protocol.json", **trial.options)
                trial.binding = ledger_binding(folder / "protocol.json", grant_sha256=hashlib.sha256((folder / "grant.json").read_bytes()).hexdigest(), **trial.options)
                trial.ledger = FullAcceptanceLedger.create(folder / FILENAME, binding=trial.binding)
                await wait_start(trial)
                owner = trial.identity["user"]["id"]
                first = trial.fixtures["f01"]["conversation"]["id"]
                # Resolve after the isolated fixture patches the module, not a
                # pre-imported alias to the user's original production store.
                from core.usage_runtime import get_usage_store
                usage = bind_usage_context(owner, f"/chat/conversations/{first}/assistant-turns", get_usage_store())
                try:
                    await prepare_templates(template_root, owner, "intent-index-v1", settings,
                        trial.client_for, trial.ledger, trial.op_binding)
                    scope = CandidateScope(owner, first, str(uuid4()), 1)
                    current = lambda: scope
                    resource = await CandidateResources.open(storage, scope, current)
                    try:
                        await prepare_index(index_root, resource, ReviewedKnowledgePort(get_index()), "310000", "museum_people",
                            settings, trial.client_for, trial.ledger, trial.op_binding)
                    finally:
                        await resource.aclose()
                finally:
                    reset_usage_context(usage)
                services = FullRuntimeServices(FullResourceConfig(storage, template_root,
                    reviewed_city_id="310000", reviewed_venue_id="museum_people", vector_receipt_root=index_root))
                try:
                    services.preflight(owner)
                finally:
                    await services.aclose()
                for case in trial.cases:
                    print("NEXT " + case["id"] + " " + case["channel"], flush=True)
                    if case["channel"] == "api":
                        headers = {"Origin": ORIGIN, "X-CSRF-Token": trial.identity["csrf_token"], "X-TripPilot-Surface": "dialogue-v3"}
                        response = await api.post(f"/chat/conversations/{trial.fixtures[case['id']]['conversation']['id']}/assistant-turns",
                            json=turn_body(case, trial.fixtures[case["id"]]), headers=headers, timeout=110)
                        await trial.collect(api, case, response)
                    else:
                        if server is None:
                            server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8812,
                                access_log=False, log_level="warning"))
                            server_task = asyncio.create_task(server.serve())
                            while not server.started:
                                if server_task.done():
                                    raise ValueError("browser_server_start_failed")
                                await asyncio.sleep(.02)
                        print("BROWSER_READY " + case["id"] + " :3112; select frozen conversation, prepare/generate explicitly", flush=True)
                        await asyncio.wait_for(trial.events[case["id"]].wait(), 1800)
                        await trial.collect(api, case)
                    trial.position += 1
                write_new(folder / "execution-summary.json", {"schema_version": VERSION,
                    "ledger": trial.ledger.summary(), "synthetic_business": business.summary(),
                    "real_amap_calls": 0, "judge_calls": 0, "quality": "pending-manual-review"})
                print("FIXED_PLAN_FINISHED; no additional calls permitted", flush=True)
    except BaseException as error:
        if trial.ledger is not None:
            try:
                trial.ledger.pause(reason="execution_error")
                write_new(folder / "execution-paused.json", {"schema_version": VERSION, "ledger": trial.ledger.summary(),
                    "reason": "execution_exception_preserve_no_retry", "real_amap_calls": 0,
                    "error_type": type(error).__name__ if re.fullmatch(r"[A-Za-z_]{1,80}", type(error).__name__) else "unknown",
                    "safe_code": str(error) if re.fullmatch(r"[a-z][a-z0-9_]{0,95}", str(error)) else "redacted"})
            except BaseException:
                pass
        raise
    finally:
        trial.armed = False
        if server is not None:
            server.should_exit = True
        if server_task is not None:
            await server_task
        await close_full_services()
        if redis.poll() is None:
            redis.terminate()
            await asyncio.to_thread(redis.wait, 5)
        engine.dispose()
        print("Owned API/Redis closed; independent fixture/evidence retained; original services unchanged", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--prior-ledgers", type=Path, required=True)
    args = parser.parse_args()
    try:
        asyncio.run(run(args.directory, args.prior_ledgers))
    except BaseException as error:
        # No raw HTTP/body/SQL/config exception is a safe diagnostic.
        print("STOP full_acceptance_execution_paused; inspect private receipts; no automatic retry", flush=True)
        return 130 if isinstance(error, KeyboardInterrupt) else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Independent production-handler fixture for one frozen full-runtime trial.

Only the caller's separately admitted Qwen dependency may send real requests.
POIs, routes and initial requirements are explicitly synthetic; no user store
is read or overwritten. This module has no sender, environment lookup or DDL.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager, ExitStack
from copy import deepcopy
import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import httpx
from sqlalchemy.engine import Engine

from core.config import Settings, get_settings
from core.schemas.chat import Intent
from core.schemas.dialogue import DialogueSelectionContext
from echomind_core.runtime import CandidateResources
from echomind_core.travel_domain import TravelIntentRecognizer
from echomind_core.travel_memory import TravelMemoryPort
from memory.chat_places_storage import ChatPlacesStore
from memory.chat_storage import ChatStore
from tooling.providers.chat_places import _place
from tools.echomind_travel_controlled import ORIGIN, PLACE_ROWS, canonical
from tools.echomind_travel_live_runner import fixture_app


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def write_new(path: Path, value: Any) -> None:
    """Exclusive private evidence, never a secret/prompt logging facility."""
    if not path.is_absolute() or path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError("private_evidence_path_required")
    parent = path.parent.stat()
    if parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
        raise ValueError("private_evidence_path_required")
    raw = canonical(value) + b"\n"
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("evidence_limit")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def stage_annotations() -> Iterator[None]:
    """Observation-only wrappers; inputs, return values and algorithms unchanged."""
    from tools.echomind_full_acceptance_transport import full_embedding_stage

    def annotated(original, stage):
        async def run(self, *args, **kwargs):
            with full_embedding_stage(stage):
                return await original(self, *args, **kwargs)
        return run

    with ExitStack() as stack:
        for cls, method, stage in (
            (TravelMemoryPort, "context_for", "memory.embedding"),
            (TravelIntentRecognizer, "recognize", "intent.embedding"),
            (CandidateResources, "search", "retrieval.embedding"),
        ):
            stack.enter_context(patch.object(cls, method, annotated(getattr(cls, method), stage)))
        yield


@contextmanager
def full_fixture_app(engine: Engine, anchor: Path, settings: Settings,
                     qwen_dependency: Callable[..., Any], current_tier: Callable[[], str]):
    """Reuse prior storage/business fixture, never its old runtime admission.

    The prior harness checks an empty independent DB and injects auth/usage and
    synthetic business transports. We replace ONLY its Settings dependency with
    the actual full runtime before requests; production route/preflight decides
    admission. No fake adapter, prepared vectors or model outputs are supplied.
    """
    from pydantic import SecretStr
    if settings.assistant_runtime != "echomind_travel_full_v2":
        raise ValueError("full_runtime_required")
    old_fixture_settings = settings.model_copy(update={"assistant_runtime": "echomind_travel_v1"})
    with fixture_app(engine, anchor, settings=old_fixture_settings,
                     qwen_dependency=qwen_dependency, origin=ORIGIN) as (app, upstream):
        def configured():
            tier = current_tier()
            if tier not in {"single", "collaboration", "reference"}:
                raise ValueError("frozen_tier_required")
            return settings.model_copy(update={"assistant_full_budget": tier,
                "amap_web_key": SecretStr("controlled-not-real"),
                "amap_js_key": SecretStr(""), "amap_js_security_code": SecretStr("")})
        app.dependency_overrides[get_settings] = configured
        with stage_annotations():
            yield app, upstream


def requirements(*, missing_dates: bool = False) -> dict[str, Any]:
    return Intent(destination_id="shanghai", start_date=None if missing_dates else "2026-11-01",
        end_date=None if missing_dates else "2026-11-02", travelers=2, budget_cny="3000",
        accommodation_hint="受控住宿甲", required_place_hints=["受控景点甲"],
        transport_mode="walking").model_dump(mode="json")


async def seed(client: httpx.AsyncClient, engine: Engine, manifest: dict) -> tuple[dict, dict, list[dict]]:
    """Known synthetic initial inputs, using normal ownership/store contracts."""
    bootstrap = (await client.get("/workspace/session")).json()
    response = await client.post("/workspace/session", json={},
        headers={"Origin": ORIGIN, "X-CSRF-Token": bootstrap["csrf_token"]})
    if response.status_code != 200:
        raise ValueError("workspace_bootstrap_failed")
    identity = response.json()
    owner = identity["user"]["id"]
    headers = {"Origin": ORIGIN, "X-CSRF-Token": identity["csrf_token"]}
    chat, places = ChatStore(engine), ChatPlacesStore(engine)
    fixtures = {}
    for case in manifest["operations"]:
        created = await client.post("/chat/conversations", json={"operation_id": str(uuid4())}, headers=headers)
        if created.status_code != 200:
            raise ValueError("fixture_conversation_failed")
        view, operation = created.json(), str(uuid4())
        initial_text = "上海，两个人，预算3000元，住宿参考点是受控住宿甲，必去受控景点甲，步行。"
        if case["id"] != "f01":
            initial_text += "11月1日至11月2日。"
        pending = await asyncio.to_thread(chat.begin_turn, owner, view["id"], operation,
            view["revision"], initial_text)
        view = await asyncio.to_thread(chat.finish_turn, owner, view["id"], operation,
            pending["revision"], requirements(missing_dates=case["id"] == "f01"),
            "本会话使用合成地点及路线作模型验收；不是实时高德数据，尚未生成行程。", [])
        if case["id"] != "f01":
            view = await asyncio.to_thread(chat.confirm, owner, view["id"], view["revision"], str(uuid4()))
        workspace = await asyncio.to_thread(places.sync, owner, view["id"], str(uuid4()), view["revision"], 0)
        for row, role in ((PLACE_ROWS[0], "accommodation"), (PLACE_ROWS[1], "required")):
            poi = _place(row, "310000").model_dump(mode="json")
            op, batch = str(uuid4()), str(uuid4())
            pending = await asyncio.to_thread(places.begin_operation, owner, view["id"], op,
                view["revision"], workspace["revision"], "search", {"keyword": row["name"], "role": role})
            workspace = await asyncio.to_thread(places.finish_operation, owner, view["id"], op,
                pending["revision"], view["revision"], batch={"id": batch, "city_id": "310000",
                    "keyword": row["name"], "role": role, "queried_at": "2026-10-24T00:00:00Z", "places": [poi]})
            workspace = await asyncio.to_thread(places.confirm, owner, view["id"], str(uuid4()),
                view["revision"], workspace["revision"], batch, poi["id"], role)
        # A frozen candidate batch is not another confirmed required place.
        if case["id"] == "f03":
            op, batch = str(uuid4()), str(uuid4())
            row = _place(PLACE_ROWS[1], "310000").model_dump(mode="json")
            pending = await asyncio.to_thread(places.begin_operation, owner, view["id"], op,
                view["revision"], workspace["revision"], "search", {"keyword": row["name"], "role": "required"})
            workspace = await asyncio.to_thread(places.finish_operation, owner, view["id"], op,
                pending["revision"], view["revision"], batch={"id": batch, "city_id": "310000",
                    "keyword": row["name"], "role": "required", "queried_at": "2026-10-24T00:00:00Z", "places": [row]})
        context = {"workspace_version": str(uuid4()), "display": "places" if case["id"] == "f03" else "none", "items": []}
        if case["id"] == "f03":
            context["items"] = [{"ordinal": 1, "type": "place", "id": PLACE_ROWS[1]["id"], "name": PLACE_ROWS[1]["name"]}]
        context = DialogueSelectionContext.model_validate(context).model_dump(mode="json")
        fixtures[case["id"]] = {"conversation": view, "places": workspace, "selection_context": context,
            "operation_id": str(uuid4()), "requirements_sha256": digest(view["requirements"]),
            "places_sha256": digest(workspace)}
    cookies = [{"name": cookie.name, "value": cookie.value, "domain": cookie.domain, "path": cookie.path,
        "expires": cookie.expires or -1, "httpOnly": True, "secure": cookie.secure, "sameSite": "Lax"}
        for cookie in client.cookies.jar]
    return identity, fixtures, cookies


def turn_body(case: dict, fixture: dict) -> dict:
    """No expected answers, case IDs, scores or protocol labels enter the wire."""
    return {"operation_id": fixture["operation_id"], "expected_revision": fixture["conversation"]["revision"],
        "text": case["text"], "dialogue_context": deepcopy(fixture["selection_context"])}

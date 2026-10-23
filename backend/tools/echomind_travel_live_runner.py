"""Isolated production-ASGI fixture for the fixed travel acceptance operations.

This module contains no live transport, credentials discovery, authorization or
model response script. The caller must separately provide an admitted exact-
client dependency. All non-model upstreams remain explicitly synthetic. Native
HTTP proposal success is never presented as browser execution.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import re
from typing import Any
from unittest.mock import patch
from uuid import uuid4
from pathlib import Path

import httpx
from sqlalchemy.engine import Engine
from sqlmodel import Session

from tools.echomind_travel_acceptance_plan import load
from tools.echomind_travel_controlled import ControlledError, ControlledUpstream, canonical
from tools.echomind_travel_trial_capture import _fixture

VERSION = "echomind-travel-live-fixture-v1"
ORIGIN = "http://127.0.0.1:3112"
NATIVE_IDS = tuple(f"m{index:02d}" for index in range(1, 8))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


@dataclass(frozen=True)
class TurnBinding:
    """In-memory authoritative capture; persisted projection hashes the owner."""
    case_id: str
    conversation_id: str
    owner_id: str
    operation_id: str
    workspace_version: str
    revision: int
    place_revision: int
    question: str
    conversation_sha256: str
    places_sha256: str
    selection_sha256: str
    command_sha256: str
    authority_sha256: str
    preferences_sha256: str
    messages_sha256: str
    _places_json: bytes = field(repr=False)

    def public(self) -> dict[str, Any]:
        return {"schema_version": VERSION, "case_id": self.case_id,
                "conversation_id": self.conversation_id, "owner_sha256": digest(self.owner_id),
                "operation_id": self.operation_id, "workspace_version": self.workspace_version,
                "revision": self.revision, "place_revision": self.place_revision,
                "question_sha256": digest(self.question),
                "conversation_sha256": self.conversation_sha256, "places_sha256": self.places_sha256,
                "selection_sha256": self.selection_sha256, "command_sha256": self.command_sha256,
                "authority_sha256": self.authority_sha256, "preferences_sha256": self.preferences_sha256,
                "messages_sha256": self.messages_sha256}

    @property
    def sha256(self) -> str:
        return digest(self.public())


class SyntheticBusinessUpstream(ControlledUpstream):
    """The model callback is never allowed to fall through to scripted outputs."""
    async def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != "restapi.amap.com":
            raise ControlledError("non_business_upstream_forbidden")
        return await super().handle(request)


@contextmanager
def fixture_app(engine: Engine, anchor: Path, *, settings: Any,
                qwen_dependency: Callable[..., Any], origin: str = ORIGIN) -> Iterator[tuple[Any, SyntheticBusinessUpstream]]:
    """Production handlers/stores with caller-owned model dependency only.

    Unlike controlled_app this does NOT replace AsyncHTTPTransport globally.
    The caller's admitted model transport remains solely responsible for its
    fixed endpoint, persistent pre-send claims, bounded input and output, and
    current-binding checks. No sender or true admission is supplied here.
    """
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

    _fixture(engine, anchor)
    if (type(settings) is not Settings or settings.assistant_runtime != "echomind_travel_v1"
            or settings.qwen_model != "qwen3.7-flash-2026-07-15"
            or not callable(qwen_dependency)
            or re.fullmatch(r"http://127\.0\.0\.1:(?:3112|3000)", origin) is None):
        raise ControlledError("fixed_candidate_dependency_required")
    # Reconstruct explicit configuration; a provider key must not leak into
    # business clients, and no browser-map credential is needed in this trial.
    from pydantic import SecretStr
    configured = settings.model_copy(update={"amap_web_key": SecretStr("controlled-not-real"),
        "amap_js_key": SecretStr(""), "amap_js_security_code": SecretStr("")})
    auth_settings = auth.AuthSettings(_env_file=None, auth_cookie_secure=False, auth_allowed_origins=(origin,))
    usage_settings = UsageSettings(_env_file=None, business_usage_identity_path=str(anchor),
        business_daily_limits={service: {"account": 1000 if service in {"qwen_text", "amap_poi", "amap_route"} else 0,
                                        "global": 2000 if service in {"qwen_text", "amap_poi", "amap_route"} else 0}
                               for service in SERVICES})
    usage = MySQLUsageStore(usage_settings, engine=engine)
    usage.verify()
    upstream = SyntheticBusinessUpstream()

    async def business_dependency():
        async with MeteredAsyncClient(transport=httpx.MockTransport(upstream.handle),
                                      follow_redirects=False, trust_env=False) as client:
            yield client

    def session_dependency():
        with Session(engine) as session:
            yield session

    overrides = {get_settings: lambda: configured, get_chat_store: lambda: ChatStore(engine),
        get_chat_places_store: lambda: ChatPlacesStore(engine), get_assistant_store: lambda: AssistantStore(engine),
        get_session: session_dependency, get_qwen_http_client: qwen_dependency,
        get_http_client: business_dependency, get_proxy_http_client: business_dependency}
    with ExitStack() as stack:
        for module in ("core.auth", "core.workspace_identity", "api.workspace"):
            stack.enter_context(patch(module + ".get_auth_settings", return_value=auth_settings))
        stack.enter_context(patch("core.workspace_identity.mysql_configured", return_value=True))
        stack.enter_context(patch("core.workspace_identity.current_mysql_engine", return_value=engine))
        stack.enter_context(patch("memory.mysql_storage._configured_url", return_value=engine.url.render_as_string(hide_password=False)))
        for module in ("core.usage_runtime", "core.business_usage", "api.usage"):
            stack.enter_context(patch(module + ".get_usage_store", return_value=usage))
        stack.enter_context(patch.dict(app.dependency_overrides, overrides))
        yield app, upstream


class NativeSession:
    """Normal cookie/CSRF API driver; each predetermined native operation once.

    M01 uses empty, M02/M03 share requirements. Formal operations deliberately
    require a browser-supplied conversation and selection; this driver cannot
    insert a formal ScheduleResponse or claim a local controller was executed.
    """
    def __init__(self, client: Any, engine: Engine, *, origin: str = ORIGIN):
        from memory.chat_storage import ChatStore
        from memory.chat_places_storage import ChatPlacesStore
        from memory.assistant_store import AssistantStore
        self.client, self.engine, self.origin = client, engine, origin
        self.chat, self.places = ChatStore(engine), ChatPlacesStore(engine)
        self.tasks = AssistantStore(engine)
        self.cases = {row.id: row for row in load().operations if row.branch == "native_candidate"}
        self.conversations: dict[str, str] = {}
        self.attempted: set[str] = set()
        self.outcomes: dict[str, dict] = {}
        self.active: TurnBinding | None = None
        self._bootstrap()

    def _bootstrap(self) -> None:
        seed = self.client.get("/workspace/session")
        if seed.status_code != 200:
            raise ControlledError("fixture_workspace_bootstrap_failed")
        identity = seed if seed.json().get("authenticated") is True else self.client.post(
            "/workspace/session", json={}, headers={"origin": self.origin, "x-csrf-token": seed.json()["csrf_token"]})
        if identity.status_code != 200:
            raise ControlledError("fixture_workspace_bootstrap_failed")
        self.owner = identity.json()["user"]["id"]
        self.headers = {"origin": self.origin, "x-csrf-token": identity.json()["csrf_token"],
                        "x-trippilot-surface": "dialogue-v2"}

    def conversation(self, fixture: str) -> str:
        if fixture not in {"empty", "requirements", "formal"}:
            raise ControlledError("fixture_unknown")
        if fixture not in self.conversations:
            response = self.client.post("/chat/conversations", headers=self.headers,
                                        json={"operation_id": str(uuid4())})
            if response.status_code != 200:
                raise ControlledError("fixture_conversation_failed")
            self.conversations[fixture] = response.json()["id"]
        return self.conversations[fixture]

    def bind_formal_browser(self, conversation_id: str) -> None:
        if "formal" in self.conversations or any(case in self.attempted for case in NATIVE_IDS[3:]):
            raise ControlledError("formal_fixture_already_bound")
        view = self.chat.get(self.owner, conversation_id)
        if view["confirmed_revision"] != view["revision"]:
            raise ControlledError("formal_fixture_requirements_not_confirmed")
        self.conversations["formal"] = conversation_id

    def current_binding(self) -> str:
        binding = self.active
        if binding is None:
            raise ControlledError("fixture_no_active_turn")
        chat = self.chat.get(self.owner, binding.conversation_id)
        places = self.places.get(self.owner, binding.conversation_id)
        before = chat["revision"] == binding.revision and digest(chat) == binding.conversation_sha256
        turn = chat["last_turn"]
        running = (chat["revision"] == binding.revision + 1 and turn is not None
                   and turn["operation_id"] == binding.operation_id and turn["state"] == "running")
        from core.llm_utils import strict_json
        expected_places = strict_json(binding._places_json)
        if running:
            # Match the existing trusted loader's intentional stale projection;
            # compare all fields rather than granting unchanged-revision data.
            expected_places.update(stale=True, batch=None, clarification=None, proposed_city_id=None, request_text=None)
            if (expected_places["revision"] == 0 and expected_places["city_id"] is None
                    and expected_places["last_operation"] is None):
                expected_places["intent_revision"] = binding.revision + 1
        authority = {key: chat[key] for key in ("requirements", "sources", "issues")}
        history_matches = (digest(chat["messages"]) == binding.messages_sha256 if before else
            bool(chat["messages"]) and digest(chat["messages"][:-1]) == binding.messages_sha256
            and chat["messages"][-1]["role"] == "user" and chat["messages"][-1]["text"] == binding.question)
        task = self.tasks.latest(self.owner, binding.conversation_id)
        task_matches = (not running or task is not None and task["operation_id"] == binding.operation_id
            and task["conversation_revision"] == binding.revision + 1 and task["state"] == "running" and not task["stale"])
        if (not (before or running) or places != expected_places or not history_matches or not task_matches
                or digest(authority) != binding.authority_sha256
                or digest(self.tasks.list_preferences(self.owner)) != binding.preferences_sha256
                or running and chat["confirmed_revision"] is not None):
            raise ControlledError("fixture_context_changed")
        return binding.sha256

    def submit(self, case_id: str, *, selection: dict[str, Any] | None = None) -> dict[str, Any]:
        if case_id not in self.cases:
            raise ControlledError("fixture_unplanned_or_duplicate_operation")
        case = self.cases[case_id]
        if case.fixture_id == "formal" and ("formal" not in self.conversations or selection is None):
            raise ControlledError("formal_browser_fixture_required")
        conversation = self.conversation(case.fixture_id)
        view = self.chat.get(self.owner, conversation)
        context = deepcopy(selection) if selection is not None else {"workspace_version": str(uuid4()), "display": "none", "items": []}
        command = {"operation_id": str(uuid4()), "expected_revision": view["revision"],
                   "text": case.text, "dialogue_context": context}
        self.prepare_browser_turn(case_id, conversation, command)
        try:
            response = self.client.post(f"/chat/conversations/{conversation}/assistant-turns", headers=self.headers, json=command)
            return self.finish_browser_turn(response.status_code, response.json())
        finally:
            self.active = None

    def prepare_browser_turn(self, case_id: str, conversation: str, command: dict[str, Any]) -> TurnBinding:
        """Before ordinary route admission, bind the actual browser submission.

        The server driver obtains owner from its established private workspace,
        never from request JSON. This is not an HTTP endpoint or authorization.
        A durable external journal still burns cases across process restarts.
        """
        if case_id not in self.cases or case_id in self.attempted or self.active is not None:
            raise ControlledError("fixture_unplanned_or_duplicate_operation")
        case = self.cases[case_id]
        for dependency in case.depends_on:
            if dependency.startswith("m") and (dependency not in self.outcomes or self.outcomes[dependency]["http_status"] != 200):
                raise ControlledError("fixture_dependency_not_succeeded")
        if case.fixture_id == "formal" and "formal" not in self.conversations:
            raise ControlledError("formal_browser_fixture_required")
        if conversation != self.conversation(case.fixture_id):
            raise ControlledError("fixture_wrong_conversation")
        view = self.chat.get(self.owner, conversation)
        places = self.places.get(self.owner, conversation)
        from core.schemas.dialogue import SubmitDialogueTurn
        parsed = SubmitDialogueTurn.model_validate(command)
        if (parsed.text != case.text or parsed.expected_revision != view["revision"]
                or parsed.dialogue_context is None):
            raise ControlledError("fixture_question_revision_or_selection_changed")
        context = parsed.dialogue_context.model_dump(mode="json")
        binding = TurnBinding(case_id, conversation, self.owner, command["operation_id"], context["workspace_version"],
            view["revision"], places["revision"], case.text, digest(view), digest(places), digest(context), digest(command),
            digest({key: view[key] for key in ("requirements", "sources", "issues")}),
            digest(self.tasks.list_preferences(self.owner)), digest(view["messages"]), canonical(places))
        self.attempted.add(case_id)
        self.active = binding
        return binding

    def finish_browser_turn(self, status: int, response: dict[str, Any]) -> dict[str, Any]:
        binding = self.active
        if binding is None or type(status) is not int or not 100 <= status <= 599 or type(response) is not dict:
            raise ControlledError("fixture_no_active_turn")
        try:
            conversation = binding.conversation_id
            readback = self.client.get(f"/chat/conversations/{conversation}")
            action = self.client.get(f"/chat/conversations/{conversation}/dialogue-action")
            state = self.client.get(f"/chat/conversations/{conversation}/assistant-state")
            if any(row.status_code != 200 for row in (readback, action, state)):
                raise ControlledError("fixture_readback_failed")
            result = {"case_id": binding.case_id, "binding": binding.public(), "binding_sha256": binding.sha256,
                "http_status": status, "response": deepcopy(response), "readback": readback.json(),
                "proposal": action.json(), "task_state": state.json(), "browser_execution_verified": False}
            self.outcomes[binding.case_id] = result
            return result
        finally:
            self.active = None

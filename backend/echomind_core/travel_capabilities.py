"""Explicit travel capability leaves for the source-preserved EchoMind engine.

This registry narrows the existing dialogue schemas; it neither executes their
browser actions nor implements another scheduler, cost calculator or retriever.
The optional reviewed-material port is a strict, local source projection. Its
``bound_recall`` callback can be registered with the original SafeToolManager;
query rewriting/reranking remain that manager's job, not an algorithm here.
No constructor reads credentials, opens a client or enables knowledge answers.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date
from enum import Enum
from types import MappingProxyType
from typing import Any, Awaitable, Callable

from pydantic import BaseModel

from core.llm_utils import RuntimeFailure, bounded_json, strict_json, validate_arguments
from core.schemas.dialogue import ACTION_FIELDS, GROUPS, TOOL_VERSION, group_schema, validate_action
from core.schemas.knowledge_national import METHOD, NationalKnowledgeRequest, NationalKnowledgeResponse
from echomind_core.execution import ExecutionError, execution_gate
from echomind_core.travel_bridge import NO_EXTRA_CONFIRMATION, TravelToolBridge
from echomind_core.travel_domain import TravelRole
from echomind_core.upstream.agents.tools import AgentToolSpec
from tooling.travel.knowledge import fingerprint
from tooling.travel.knowledge_national import NationalKnowledgeIndex

VERSION = "travel-capabilities-v1"
MATERIAL_VERSION = "travel-reviewed-materials-v1"
SOURCE_VERSION = "knowledge-corpus-v2"
SKILLS = ("travel_safety", "place_confirmation", "planning_boundaries")


class Effect(Enum):
    READ = "read"
    EXTERNAL_READ = "external_read"
    PROPOSAL = "proposal"
    CONFIRMED_WRITE = "confirmed_write"


_READ = frozenset({
    "preview_place", "read_schedule", "read_budget", "check_weather", "read_weather",
    "read_preview", "list_saved", "read_saved", "read_preferences", "search_materials",
    "show_map", "open_photo", "open_video",
})
_EXTERNAL = frozenset({
    "search_places", "prepare_candidates", "generate_schedule", "query_weather",
    "preview_exclusion", "explain_schedule",
})
_COMMIT = frozenset({
    "confirm_place", "remove_place", "confirm_requirements", "adopt_preview", "save_trip",
    "save_preference", "delete_preference",
})
_PLANNING = frozenset({
    "prepare_candidates", "exclude_candidate", "restore_candidate", "prepare_planning",
    "cancel_operation", "generate_schedule", "read_schedule", "set_stay", "set_lunch",
    "set_transport", "set_daily_window", "query_weather", "check_weather", "read_weather",
    "set_visit_mode", "preview_exclusion", "adopt_preview", "cancel_preview", "read_preview",
    "save_trip", "list_saved", "read_saved", "restore_saved", "explain_schedule", "show_map",
})
_BUDGET = frozenset({"read_budget", "set_cost", "read_preferences", "save_preference", "delete_preference"})
_PLACES = frozenset({
    "search_places", "preview_place", "confirm_place", "remove_place", "prepare_candidates",
    "exclude_candidate", "restore_candidate", "open_photo", "open_video", "search_materials",
})
_GRANTS = MappingProxyType({
    TravelRole.COORDINATOR: frozenset(ACTION_FIELDS),
    TravelRole.PLANNING: _PLANNING,
    TravelRole.BUDGET: _BUDGET,
    TravelRole.PLACE_REFERENCE: _PLACES,
})
# Trusted Python metadata, not a request-selected permission table.
ROLE_ACTIONS = _GRANTS
if (_READ & _EXTERNAL or _READ & _COMMIT or _EXTERNAL & _COMMIT
        or any(not actions <= ACTION_FIELDS.keys() for actions in _GRANTS.values())
        or not (_READ | _EXTERNAL | _COMMIT) <= ACTION_FIELDS.keys()):
    raise RuntimeError("travel_capability_registry_invalid")


@dataclass(frozen=True)
class Capability:
    """Domain effect is metadata, never permission to execute a browser action."""

    name: str
    group: str
    effect: Effect
    roles: tuple[TravelRole, ...]
    confirmation_required: bool
    tool_version: str = TOOL_VERSION
    registry_version: str = VERSION
    execution_port: str = "browser_proposal_only"
    skills: tuple[str, ...] = SKILLS


def capability_registry(role: TravelRole | None = None) -> tuple[Capability, ...]:
    if role is not None and type(role) is not TravelRole:
        raise ExecutionError("travel_role_invalid")
    result = []
    for group, (_, names) in GROUPS.items():
        for name in names:
            roles = tuple(item for item in TravelRole if name in _GRANTS[item])
            if role is not None and role not in roles:
                continue
            effect = (Effect.READ if name in _READ else Effect.EXTERNAL_READ if name in _EXTERNAL
                      else Effect.CONFIRMED_WRITE if name in _COMMIT else Effect.PROPOSAL)
            result.append(Capability(name, group, effect, roles, name not in NO_EXTRA_CONFIRMATION))
    return tuple(result)


def validate_role_action(role: TravelRole, group: str, arguments: object) -> dict[str, Any]:
    """Recheck the role AND selected leaf immediately before the old handler."""
    if type(role) is not TravelRole or type(group) is not str or group not in GROUPS:
        raise ExecutionError("travel_role_invalid")
    allowed = tuple(name for name in GROUPS[group][1] if name in _GRANTS[role])
    if not allowed:
        raise ExecutionError("travel_role_forbidden")
    try:
        validate_arguments(arguments, group_schema(allowed))
        action = arguments["action"]
        if action not in allowed:
            raise ExecutionError("travel_role_forbidden")
        checked = validate_action(action, {key: value for key, value in arguments.items() if key != "action"})
        return {"action": checked["name"], **checked["arguments"]}
    except (RuntimeFailure, ValueError, TypeError, KeyError, RecursionError):
        raise ExecutionError("travel_role_arguments") from None


class ReviewedKnowledgePort:
    """A complete-unit, source-bound view of an existing reviewed local index.

    No embedded vectors, new ranking, arbitrary URL, external fetch, model or
    generated answer. The private copy keeps the registry stable while a tool
    runs; matching returned identities does not prove semantic relevance.
    """

    def __init__(self, index: NationalKnowledgeIndex):
        if (type(index) is not NationalKnowledgeIndex or index.method != METHOD
                or index._vectors is not None or index._reranker is not None):
            raise ExecutionError("travel_materials_configuration")
        self._index = NationalKnowledgeIndex(index.corpus, index.manifest)
        self.corpus_hash, self.corpus_version = self._index.hash, self._index.corpus.corpus_version
        self._matches = {match.chunk_id: match.model_dump(mode="json") for match, _ in self._index.chunks}

    def schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {
            "city_id": {"type": "string", "enum": sorted(self._index.cities)},
            "venue_id": {"type": "string", "enum": sorted(self._index.venues)},
            "question": {"type": "string", "minLength": 2, "maxLength": 160},
            "limit": {"type": "integer", "minimum": 1, "maximum": 5},
            "visit_date": {"anyOf": [{"type": "string", "format": "date", "minLength": 10, "maxLength": 10}, {"type": "null"}]},
        }, "required": ["city_id", "venue_id", "question"], "additionalProperties": False}

    def _request(self, arguments: object) -> NationalKnowledgeRequest:
        try:
            validate_arguments(arguments, self.schema())
            checked = NationalKnowledgeRequest.model_validate_json(bounded_json(arguments, 4096))
            self._index.validate_request(checked)
            if (fingerprint(self._index.corpus.model_dump(mode="json")) != self.corpus_hash
                    or self._index.hash != self.corpus_hash):
                raise ValueError()
            return checked
        except (RuntimeFailure, ValueError, TypeError, RecursionError):
            raise ExecutionError("travel_materials_arguments") from None

    def read(self, arguments: object) -> dict[str, Any]:
        request = self._request(arguments)
        try:
            result = self._index.search(request)
            if type(result) is not NationalKnowledgeResponse:
                raise ValueError()
            _exact_model_objects(result)
            result = NationalKnowledgeResponse.model_validate_json(result.model_dump_json(warnings="error"))
            if (result.request != request or result.corpus_hash != self.corpus_hash
                    or result.corpus_version != self.corpus_version or result.method != METHOD
                    or result.city != self._index.cities[request.city_id]
                    or result.venue != self._index.venues[request.venue_id]):
                raise ValueError()
            for match in result.matches:
                expected = self._matches.get(match.chunk_id)
                if expected is None or any(match.model_dump(mode="json")[key] != expected[key]
                                           for key in ("chunk_id", "content_hash", "section", "source")):
                    raise ValueError()
            value = {"schema_version": MATERIAL_VERSION, "retrieval_only": True,
                "source_schema_version": SOURCE_VERSION, "index_version": METHOD,
                "status": result.status, "retrieval": result.model_dump(mode="json")}
            # Never truncate source units or conditions to fit a tool budget.
            return strict_json(bounded_json(value, 32_000))
        except (RuntimeFailure, ValueError, TypeError, RecursionError, KeyError):
            raise ExecutionError("travel_materials_invalid") from None

    def bound_recall(self, city_id: str, venue_id: str, visit_date: date | None = None
                     ) -> Callable[[dict[str, Any], dict[str, Any]], Awaitable[list[dict[str, Any]]]]:
        """Trusted scope for the original manager's query/top_k callback.

        The SafeToolManager still validates/injects owner/session/version and
        reserves attempts. A rewritten query cannot change this venue closure.
        """
        try:
            frozen = NationalKnowledgeRequest(city_id=city_id, venue_id=venue_id,
                question="资料范围核对", visit_date=visit_date)
            self._index.validate_request(frozen)
        except (ValueError, TypeError):
            raise ExecutionError("travel_materials_arguments") from None
        schema = {"type": "object", "properties": {
            "query": {"type": "string", "minLength": 2, "maxLength": 160},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 5},
        }, "required": ["query", "top_k"], "additionalProperties": False}

        async def recall(arguments: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
            try:
                validate_arguments(arguments, schema)
            except RuntimeFailure:
                raise ExecutionError("travel_materials_arguments") from None
            gate = execution_gate()
            gate.check()
            expected = {"owner_id": gate.scope.owner_id, "conversation_id": gate.scope.conversation_id,
                "session_id": gate.scope.session_id, "input_version": gate.scope.input_version}
            if type(context) is not dict or context != expected:
                raise ExecutionError("travel_context_changed")
            value = await asyncio.to_thread(self.read, {"city_id": frozen.city_id, "venue_id": frozen.venue_id,
                "question": arguments["query"], "limit": arguments["top_k"],
                "visit_date": frozen.visit_date.isoformat() if frozen.visit_date else None})
            gate.check()
            response = value["retrieval"]
            # Full content and stable identity allow the original manager's
            # merge/dedup; the original rerank's text limit is not changed here.
            return [{"document_id": match["source"]["id"], "chunk": match["chunk_id"],
                "title": match["source"]["title"], "content": match["section"]["summary"],
                "retrieval_only": True, "corpus_hash": self.corpus_hash,
                "corpus_version": self.corpus_version, **deepcopy(match)} for match in response["matches"]]
        return recall


def _exact_model_objects(value: Any) -> None:
    """Do not let model_copy/construct hide extra fields during serialization."""
    if isinstance(value, BaseModel):
        if (set(value.__dict__) - set(type(value).model_fields)
                or getattr(value, "__pydantic_extra__", None)):
            raise ValueError("model_fields")
        for item in value.__dict__.values():
            _exact_model_objects(item)
    elif type(value) is dict:
        for item in value.values():
            _exact_model_objects(item)
    elif type(value) in (list, tuple):
        for item in value:
            _exact_model_objects(item)


def make_role_tools(bridge: TravelToolBridge, role: TravelRole, *,
                    national: ReviewedKnowledgePort | None = None) -> dict[str, AgentToolSpec]:
    """Wrap the existing bridge handlers; never execute a domain operation here."""
    if (type(bridge) is not TravelToolBridge or type(role) is not TravelRole
            or national is not None and type(national) is not ReviewedKnowledgePort):
        raise ExecutionError("travel_capability_configuration")
    original = bridge.tools()
    result = {}
    for group, (_, names) in GROUPS.items():
        allowed = tuple(name for name in names if name in _GRANTS[role])
        if not allowed:
            continue
        spec = original[group]

        async def call(request, arguments, *, _group=group, _handler=spec.handler):
            await bridge._check(request)
            checked = validate_role_action(role, _group, arguments)
            value = await _handler(request, checked)
            await bridge._check(request)
            return value
        result[group] = replace(spec, input_schema=group_schema(allowed), handler=call)
    for name in ("inspect_trip", "travel_clarify", "travel_information"):
        result[name] = original[name]
    if role in {TravelRole.COORDINATOR, TravelRole.BUDGET}:
        result["read_preferences"] = original["read_preferences"]
    if role in {TravelRole.COORDINATOR, TravelRole.PLANNING}:
        result["update_trip_requirements"] = original["update_trip_requirements"]
    if role in {TravelRole.COORDINATOR, TravelRole.PLACE_REFERENCE}:
        result["search_materials"] = original["search_materials"]
        if national is not None:
            async def search(request, arguments):
                await bridge._check(request)
                value = await asyncio.to_thread(national.read, arguments)
                await bridge._check(request)
                found = value["retrieval"]
                lines = ["资料检索 · " + found["venue"]["name"],
                    "以下是整理摘要与原文短摘录；相关资料不等于问题已获回答，也不保证今天适用。知识库AI问答未开放。"]
                if not found["matches"]:
                    lines.append("未找到相关资料；不能据此认定免费、无需预约或可以入场。")
                for match in found["matches"]:
                    source, section = match["source"], match["section"]
                    lines.extend([source["title"] + " · " + section["heading"],
                        "整理摘要：" + section["summary"], "原文短摘录：" + section["excerpt"],
                        "官方来源：" + source["url"], "适用性：" + source["applicability"],
                        match["applicability_check"]["notice"],
                        "发布日期：" + str(source["published_at"] or "未知"),
                        "更新日期：" + str(source["updated_at"] or "未知"),
                        "实际获取时间：" + source["fetched_at"]])
                return await bridge._record("search_reviewed_materials", {"kind": "read", "executed": True,
                    "source": "local_reviewed_materials", "retrieval_only": True,
                    "material_version": MATERIAL_VERSION, "retrieval": found,
                    "message": "\n".join(lines), "binding": bridge.snapshot.binding.as_dict()}, request)
            result["search_reviewed_materials"] = AgentToolSpec("search_reviewed_materials",
                "只检索已审核城市与场馆资料，保留完整主题、来源及适用性；不生成答案。", national.schema(), search)
    return result

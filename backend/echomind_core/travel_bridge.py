"""Opt-in, program-rendered travel tools around existing domain contracts.

Nothing here is imported by a live route.  A trusted server captures existing
owner-scoped stores before dispatch; a model never supplies that authority.
Formal schedules/costs still live in the browser, so their actions are proposals
only.  This module does not execute an API, mutate a store, or generate an answer
from retrieved material.  Original Agent/composer prose is deliberately ignored.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import asyncio
import inspect
import re
import threading
from typing import Any, Awaitable, Callable
from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from core.llm_utils import RuntimeFailure, bounded_json, strict_json, validate_arguments
from core.schemas.chat import Conversation, Intent, Selection, Timestamp
from core.schemas.chat_places import ChatPlacesWorkspace
from core.schemas.dialogue import GROUPS, TOOL_VERSION, group_schema, obj, validate_action
from core.schemas.knowledge import KnowledgeRequest, KnowledgeResponse
from echomind_core.execution import CandidateScope, ExecutionError, execution_gate
from echomind_core.upstream.agents.agent_orchestrator import Request
from echomind_core.upstream.agents.tools import AgentToolSpec
from tooling.travel.dialogue import GUIDANCE, requirement_text
from tooling.travel.chat import apply_selection, provider_schema
from tooling.travel.knowledge import get_index

VERSION = "echomind-travel-bridge-v1"
MAX_RESULT_BYTES = 32_000
MAX_PROJECTION_BYTES = 96_000
MAX_RESULTS = 4
CLARIFICATIONS = {
    "destination_missing": "请明确本次想去的城市；多城要求需要先核对执行范围。",
    "date_missing": "请说明出发和结束日期，或要查看哪一天。",
    "travelers_missing": "请说明本次同行人数。",
    "budget_missing": "请说明全部旅客的全程总预算；没有填写的费用不能当作零。",
    "target_ambiguous": "请说明要核对或调整的具体地点、日期或项目。",
    "multiple_requests": "这次包含多项操作，请先明确要执行哪一项；不会静默执行其中一部分。",
    "unsupported": "当前旅行工具尚不能执行这项操作，不会改用其他能力假装完成。",
    "formal_schedule_missing": "请先在当前工作区主动生成有效正式草案，再核对日程或费用；服务端输入不等于已有草案。",
}
_PREFERENCE_KEYS = frozenset({"key", "value", "version", "source", "consent", "created_at", "updated_at"})
# This is the existing dialogue UI's confirmation policy, not authority to
# execute.  A proposal still needs the current browser's operation-time checks.
NO_EXTRA_CONFIRMATION = frozenset({
    "preview_place", "read_schedule", "read_budget", "read_weather", "check_weather", "read_preview",
    "list_saved", "read_saved", "read_preferences", "search_materials", "cancel_preview",
    "cancel_operation", "open_photo", "open_video",
})


def _json(value: Any, limit: int = MAX_RESULT_BYTES) -> str:
    try:
        return bounded_json(value, limit)
    except (RuntimeFailure, ValueError, TypeError, RecursionError):
        raise ExecutionError("travel_snapshot_invalid") from None


def _uuid4(value: Any) -> bool:
    try:
        parsed = UUID(value)
        return type(value) is str and str(parsed) == value and parsed.version == 4
    except (ValueError, TypeError, AttributeError):
        return False


def _requirements_schema() -> dict[str, Any]:
    """Equivalent provider schema within the existing 512-node safety bound.

    Inline trusted local definitions once, then remove only non-validation
    annotations.  The catalogue enum and every acceptance constraint remain.
    Generic schema limits are not raised for this one large city catalogue.
    """
    schema = provider_schema()
    definitions = schema.pop("$defs")
    def expand(value: Any) -> Any:
        if isinstance(value, list):
            return [expand(item) for item in value]
        if isinstance(value, dict):
            if "$ref" in value:
                reference = value["$ref"]
                if set(value) != {"$ref"} or not reference.startswith("#/$defs/"):
                    raise ExecutionError("travel_snapshot_invalid")
                return expand(definitions[reference.removeprefix("#/$defs/")])
            return {key: expand(item) for key, item in value.items() if key not in {"title", "default", "description"}}
        return value
    return expand(schema)


async def _read(callback: Callable[..., Any], *arguments: Any) -> Any:
    """Sync store reads run off-loop; cancellation drains an existing thread."""
    if inspect.iscoroutinefunction(callback):
        return await callback(*arguments)
    worker = asyncio.create_task(asyncio.to_thread(callback, *arguments))
    try:
        result = await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled():
            worker.exception()
        raise
    return await result if inspect.isawaitable(result) else result


@dataclass(frozen=True)
class TravelBinding:
    """Server-owned versions; ``scope`` must advance for A -> B -> A changes."""
    scope: CandidateScope
    intent_revision: int
    place_revision: int
    operation_id: str

    def __post_init__(self) -> None:
        if (type(self.scope) is not CandidateScope or not _uuid4(self.operation_id)
                or type(self.intent_revision) is not int or not 0 <= self.intent_revision <= 1000
                or type(self.place_revision) is not int or not 0 <= self.place_revision <= 10_000):
            raise ExecutionError("travel_snapshot_invalid")

    def as_dict(self) -> dict[str, Any]:
        return {"owner_id": self.scope.owner_id, "conversation_id": self.scope.conversation_id,
                "session_id": self.scope.session_id, "input_version": self.scope.input_version,
                "intent_revision": self.intent_revision, "place_revision": self.place_revision,
                "operation_id": self.operation_id}


def _checked_preferences(values: Any) -> list[dict[str, Any]]:
    if type(values) is not list or len(values) > 30:
        raise ExecutionError("travel_snapshot_invalid")
    result = []
    for value in values:
        if (type(value) is not dict or set(value) != _PREFERENCE_KEYS
                or type(value["key"]) is not str or re.fullmatch(r"[a-z][a-z0-9_]{0,39}", value["key"]) is None
                or type(value["value"]) is not str or not 1 <= len(value["value"]) <= 500
                or value["value"].strip() != value["value"] or any(ord(c) < 32 for c in value["value"])
                or type(value["version"]) is not int or not 1 <= value["version"] <= 1000
                or value["source"] != "user" or value["consent"] is not True):
            raise ExecutionError("travel_snapshot_invalid")
        try:
            for key in ("created_at", "updated_at"):
                TypeAdapter(Timestamp).validate_python(value[key], strict=True)
            if datetime.fromisoformat(value["updated_at"].replace("Z", "+00:00")) < datetime.fromisoformat(value["created_at"].replace("Z", "+00:00")):
                raise ValueError
        except (ValidationError, ValueError, TypeError):
            raise ExecutionError("travel_snapshot_invalid") from None
        result.append(dict(value))
    if len({item["key"] for item in result}) != len(result):
        raise ExecutionError("travel_snapshot_invalid")
    return sorted(result, key=lambda item: item["key"])


@dataclass(frozen=True)
class TrustedTravelSnapshot:
    """Immutable whitelist capture, NOT an authentication or HTTP body model.

    Only trusted composition may call ``capture`` with owner-scoped reads.  The
    validation proves internal consistency, not that a client owns these facts.
    ``ReadOnlyTravelLoader`` is the existing-store implementation of that read.
    """
    binding: TravelBinding
    _payload_json: str

    @classmethod
    def capture(cls, binding: TravelBinding, conversation: dict[str, Any],
                places: dict[str, Any], preferences: list[dict[str, Any]]) -> TrustedTravelSnapshot:
        if type(binding) is not TravelBinding:
            raise ExecutionError("travel_snapshot_invalid")
        try:
            # Reject unexpected fields rather than stripping a client-authored
            # formal schedule/budget into a seemingly trusted snapshot.
            chat = Conversation.model_validate(conversation)
            workspace = ChatPlacesWorkspace.model_validate(places)
        except (ValidationError, ValueError, TypeError):
            raise ExecutionError("travel_snapshot_invalid") from None
        if (chat.id != binding.scope.conversation_id or chat.revision != binding.intent_revision
                or workspace.conversation_id != chat.id or workspace.intent_revision != chat.revision
                or workspace.revision != binding.place_revision or workspace.stale
                or chat.last_turn is not None and chat.last_turn.state == "running"
                or workspace.last_operation is not None and workspace.last_operation.state in {"running", "uncertain"}):
            raise ExecutionError("travel_context_changed")
        from core.chat_cities import legacy_city_id
        city = legacy_city_id(chat.requirements.destination_id) or chat.requirements.destination_id
        if workspace.city_id is not None and workspace.city_id != city:
            raise ExecutionError("travel_context_changed")
        payload = {"requirements": chat.requirements.model_dump(mode="json"),
                   "requirements_status": "confirmed" if chat.confirmed_revision == chat.revision else "draft",
                   "confirmed_revision": chat.confirmed_revision,
                   "places": workspace.model_dump(mode="json"),
                   "preferences": _checked_preferences(preferences)}
        return cls(binding, _json(payload, 64_000))

    def payload(self) -> dict[str, Any]:
        return strict_json(self._payload_json)


class ReadOnlyTravelLoader:
    """Explicit store injection; no default engine, environment reads or DDL.

    Capture before ``ChatStore.begin_turn``.  During an explicitly chosen active
    turn, its +1 revision and operation must match; expected stale decoration on
    the old place view never grants a new confirmation token.  Uncertain tasks
    are not resumed here.  Reads are sync: the guarded tool boundary drains them
    in a worker thread when used by an Agent.
    """
    def __init__(self, chat: Any, places: Any, preferences: Any):
        from memory.chat_storage import ChatStore
        from memory.chat_places_storage import ChatPlacesStore
        from memory.assistant_store import AssistantStore
        if (not isinstance(chat, ChatStore) or not isinstance(places, ChatPlacesStore)
                or not isinstance(preferences, AssistantStore)
                or chat.engine is not places.engine or chat.engine is not preferences.engine):
            raise ExecutionError("travel_snapshot_invalid")
        self.chat, self.places, self.preferences = chat, places, preferences

    def capture(self, scope: CandidateScope, operation_id: str) -> TrustedTravelSnapshot:
        try:
            chat = self.chat.get(scope.owner_id, scope.conversation_id)
            places = self.places.get(scope.owner_id, scope.conversation_id)
            preferences = self.preferences.list_preferences(scope.owner_id)
            binding = TravelBinding(scope, chat["revision"], places["revision"], operation_id)
            snapshot = TrustedTravelSnapshot.capture(binding, chat, places, preferences)
            self.current(snapshot)
            return snapshot
        except ExecutionError:
            raise
        except Exception:
            raise ExecutionError("travel_context_changed") from None

    def current(self, snapshot: TrustedTravelSnapshot, *, active_turn: bool = False) -> TravelBinding:
        if type(snapshot) is not TrustedTravelSnapshot or type(active_turn) is not bool:
            raise ExecutionError("travel_snapshot_invalid")
        binding, data = snapshot.binding, snapshot.payload()
        try:
            chat = self.chat.get(binding.scope.owner_id, binding.scope.conversation_id)
            places = self.places.get(binding.scope.owner_id, binding.scope.conversation_id)
            preferences = _checked_preferences(self.preferences.list_preferences(binding.scope.owner_id))
            turn = chat["last_turn"]
            if active_turn:
                valid_turn = (chat["revision"] == binding.intent_revision + 1 and turn is not None
                              and turn["operation_id"] == binding.operation_id and turn["state"] == "running")
            else:
                valid_turn = chat["revision"] == binding.intent_revision and not (turn and turn["state"] == "running")
            expected_places = dict(data["places"])
            # Store read intentionally hides an old candidate batch while a
            # turn is running. Compare all remaining persisted data, but never
            # publish that hidden batch or treat it as current authorization.
            if active_turn:
                expected_places.update(stale=True, batch=None, clarification=None, proposed_city_id=None, request_text=None)
                # A never-initialized workspace has no persisted row: its
                # empty view derives intent_revision from the running chat.
                if (data["places"]["revision"] == 0 and data["places"]["city_id"] is None
                        and data["places"]["last_operation"] is None):
                    expected_places["intent_revision"] = binding.intent_revision + 1
            # begin_turn explicitly advances revision and clears confirmation.
            # Outside that exact running turn, confirmation must match the
            # captured state as well as the ordinary revision/context guards.
            expected_confirmed = None if active_turn else data["confirmed_revision"]
            expected_status = "draft" if active_turn else data["requirements_status"]
            current_status = "confirmed" if chat["confirmed_revision"] == chat["revision"] else "draft"
            if (not valid_turn or chat["confirmed_revision"] != expected_confirmed or current_status != expected_status
                    or chat["requirements"] != data["requirements"] or places != expected_places
                    or preferences != data["preferences"]):
                raise ExecutionError("travel_context_changed")
            return binding
        except ExecutionError:
            raise
        except Exception:
            raise ExecutionError("travel_context_changed") from None


@dataclass(frozen=True)
class TravelProjection:
    state: str
    message: str
    binding: TravelBinding
    _results_json: str

    @property
    def tool_results(self) -> list[dict[str, Any]]:
        return strict_json(self._results_json)

    def as_dict(self) -> dict[str, Any]:
        return {"schema_version": VERSION, "state": self.state, "message": self.message,
                "binding": self.binding.as_dict(), "tool_results": self.tool_results}


class TravelToolBridge:
    """Readonly tools: facts from frozen stores, or unexecuted browser actions.

    Captured labels/user strings are data rendered as ordinary text.  The model
    does not choose prices, coordinates, final prose, authority or callables.
    ``project`` reads this bridge's actual results, never Agent.content/traces.
    """
    def __init__(self, snapshot: TrustedTravelSnapshot, current: Callable[[], TravelBinding | Awaitable[TravelBinding]],
                 *, materials: Callable[[KnowledgeRequest], KnowledgeResponse] | None = None,
                 parallel_reads: bool = False):
        if (type(snapshot) is not TrustedTravelSnapshot or not callable(current)
                or type(parallel_reads) is not bool
                or materials is not None and not callable(materials)):
            raise ExecutionError("travel_snapshot_invalid")
        self.snapshot, self.current = snapshot, current
        self._materials = materials or (lambda request: get_index().search(request))
        self._rows: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self._invalid = False
        # Explicit full-profile exception: two independent READ proposals, not
        # two mutations. They remain unexecuted until the current browser checks
        # its formal-schedule/fee versions. Legacy admission stays one proposal.
        self.parallel_reads = parallel_reads

    async def _check(self, request: Request | None = None) -> None:
        try:
            binding = await _read(self.current)
            valid = not self._invalid and type(binding) is TravelBinding and binding == self.snapshot.binding
        except Exception:
            valid = False
        if not valid:
            self._invalid = True
            if request is not None:
                execution_gate().fail("travel_context_changed")
            raise ExecutionError("travel_context_changed")
        if request is not None:
            gate = execution_gate()
            gate.check(request)
            if gate.scope != binding.scope or request.request_id != binding.operation_id:
                self._invalid = True
                gate.fail("travel_context_changed")
                raise ExecutionError("travel_context_changed")

    def invalidate(self) -> None:
        """Permanent session invalidation; a caller returning to A cannot revive it."""
        self._invalid = True

    async def _record(self, name: str, data: dict[str, Any], request: Request) -> dict[str, Any]:
        await self._check(request)
        row = {"name": name, "data": strict_json(_json(data))}
        with self._lock:
            proposals = [item for item in self._rows if item["data"]["kind"] in {"proposal", "requirements_proposal"}]
            multiple = bool(data["kind"] in {"proposal", "requirements_proposal"} and proposals)
            read_pair = (self.parallel_reads and len(proposals) == 1
                and all(item["data"].get("kind") == "proposal" and item["data"].get("executed") is False
                        for item in [*proposals, row])
                and {(item["name"], item["data"].get("action", {}).get("name"))
                     for item in [*proposals, row]} == {
                         ("travel_schedule", "read_schedule"), ("travel_budget", "read_budget")})
            if len(self._rows) >= MAX_RESULTS or multiple and not read_pair:
                execution_gate().fail("travel_multiple_proposals")
                raise ExecutionError("travel_multiple_proposals")
            _json([*self._rows, row], MAX_PROJECTION_BYTES)
            self._rows.append(row)
        # An original Agent feeds tool results back to the model.  The program
        # keeps the full trusted row; the model sees no internal identities,
        # coordinates, entire candidate batch or arbitrary raw store fields.
        visible = {key: data[key] for key in (
            "kind", "executed", "source", "message", "action", "confirmation_required",
            "browser_context_check_required", "tool_version", "reason", "topic",
        ) if key in data}
        if "retrieval" in data:
            visible["material_count"] = len(data["retrieval"]["matches"])
        if "preferences" in data:
            visible["preference_count"] = len(data["preferences"])
        if "confirmed_places" in data:
            visible["confirmed_required_count"] = len(data["confirmed_places"]["required_places"])
        return strict_json(_json(visible))

    def tools(self) -> dict[str, AgentToolSpec]:
        empty = obj({})

        async def inspect_trip(request: Request, arguments: dict[str, Any]) -> dict[str, Any]:
            await self._check(request)
            validate_arguments(arguments, empty)
            data = self.snapshot.payload()
            requirements = Intent.model_validate(data["requirements"])
            places = data["places"]
            confirmed = {key: places[key] for key in ("city_id", "accommodation", "required_places")}
            names = (["住宿参考点：" + places["accommodation"]["name"]] if places["accommodation"] else [])
            names += [f"必去 {index}：{place['name']}" for index, place in enumerate(places["required_places"], 1)]
            heading = "当前已确认需求（请求捕获时）" if data["requirements_status"] == "confirmed" else "当前待确认输入"
            return await self._record("inspect_trip", {"kind": "read", "executed": True,
                "source": "owner_scoped_inputs", "requirements": data["requirements"],
                "requirements_status": data["requirements_status"], "confirmed_revision": data["confirmed_revision"],
                "confirmed_places": confirmed, "message": heading + "\n" + requirement_text(requirements, changed=False)
                    + ("\n" + "\n".join(names) if names else "\n尚无已确认真实地点。"),
                "binding": self.snapshot.binding.as_dict()}, request)

        async def read_preferences(request: Request, arguments: dict[str, Any]) -> dict[str, Any]:
            await self._check(request)
            validate_arguments(arguments, empty)
            preferences = self.snapshot.payload()["preferences"]
            message = ("已明确同意保存的偏好：\n" + "\n".join(f"{item['key']}：{item['value']}" for item in preferences)
                       if preferences else "当前工作区没有明确同意保存的偏好。")
            message += "\n偏好是沟通参考，不代表已经满足排程硬约束；本次明确要求优先。"
            return await self._record("read_preferences", {"kind": "read", "executed": True,
                "source": "owner_scoped_user_consent", "preferences": preferences, "message": message,
                "binding": self.snapshot.binding.as_dict()}, request)

        material_schema = obj({"venue_id": {"type": "string", "enum": ["museum_people", "museum_east", "natural"]},
            "question": {"type": "string", "minLength": 2, "maxLength": 160},
            "limit": {"type": "integer", "minimum": 1, "maximum": 5}})

        async def search_materials(request: Request, arguments: dict[str, Any]) -> dict[str, Any]:
            await self._check(request)
            validate_arguments(arguments, material_schema)
            try:
                query = KnowledgeRequest.model_validate(arguments)
                found = await _read(self._materials, query)
                if type(found) is not KnowledgeResponse or found.request != query:
                    raise ValueError
                found = KnowledgeResponse.model_validate_json(found.model_dump_json())
            except (ValidationError, ValueError, TypeError):
                raise ExecutionError("travel_materials_invalid") from None
            await self._check(request)
            lines = [f"资料检索 · {found.venue.name}", "以下为已收录资料，不等于问题已获回答，也不保证今天适用；知识库AI问答未开放。"]
            if not found.matches:
                lines.append("未找到相关资料。")
            for match in found.matches:
                source = match.source
                lines.extend([f"{source.title} · {match.section.heading}",
                    "整理摘要：" + match.section.summary, "原文短摘录：" + match.section.excerpt,
                    "官方来源：" + source.url, "适用性：" + source.applicability,
                    "来源日期：" + str(source.updated_at or source.published_at or "未知"),
                    "实际获取时间：" + source.fetched_at.isoformat()])
            return await self._record("search_materials", {"kind": "read", "executed": True,
                "source": "local_reviewed_materials", "retrieval": found.model_dump(mode="json"),
                "message": "\n".join(lines), "binding": self.snapshot.binding.as_dict()}, request)

        result = {
            "inspect_trip": AgentToolSpec("inspect_trip", "只读当前服务端确认输入与地点；不是读取正式草案。", empty, inspect_trip),
            "read_preferences": AgentToolSpec("read_preferences", "只读本工作区明确同意的偏好，不写入推断画像。", empty, read_preferences),
            "search_materials": AgentToolSpec("search_materials", "本地检索上海三馆区资料；完整摘要与短摘录分别显示，不生成答案。", material_schema, search_materials),
        }

        clarify_schema = obj({"reason": {"type": "string", "enum": list(CLARIFICATIONS)}})
        async def clarify(request: Request, arguments: dict[str, Any]) -> dict[str, Any]:
            await self._check(request)
            validate_arguments(arguments, clarify_schema)
            return await self._record("travel_clarify", {"kind": "clarification", "executed": False,
                "reason": arguments["reason"], "message": CLARIFICATIONS[arguments["reason"]],
                "binding": self.snapshot.binding.as_dict()}, request)
        information_schema = obj({"topic": {"type": "string", "enum": list(GUIDANCE)}})
        async def information(request: Request, arguments: dict[str, Any]) -> dict[str, Any]:
            await self._check(request)
            validate_arguments(arguments, information_schema)
            return await self._record("travel_information", {"kind": "guidance", "executed": False,
                "topic": arguments["topic"], "message": GUIDANCE[arguments["topic"]],
                "binding": self.snapshot.binding.as_dict()}, request)
        result["travel_clarify"] = AgentToolSpec("travel_clarify", "选择固定澄清原因；不猜测缺失数据，不执行操作。", clarify_schema, clarify)
        result["travel_information"] = AgentToolSpec("travel_information", "选择现有程序审定的一般旅行提示；不是核实后的具体场馆事实。", information_schema, information)

        async def requirements(request: Request, arguments: dict[str, Any]) -> dict[str, Any]:
            await self._check(request)
            try:
                selection = Selection.model_validate(arguments)
                proposed, sources = apply_selection(Intent.model_validate(self.snapshot.payload()["requirements"]), selection, request.message)
            except (ValidationError, ValueError, TypeError):
                execution_gate().fail("travel_requirements_invalid")
                raise ExecutionError("travel_requirements_invalid") from None
            return await self._record("update_trip_requirements", {"kind": "requirements_proposal", "executed": False,
                "requirements": proposed.model_dump(mode="json"), "sources": [source.model_dump(mode="json") for source in sources],
                "issues": selection.issues, "confirmation_required": True, "browser_context_check_required": True,
                "message": requirement_text(proposed, changed=True) + "\n这是待确认的需求提议，尚未写入会话或修改正式草案。",
                "binding": self.snapshot.binding.as_dict()}, request)
        result["update_trip_requirements"] = AgentToolSpec("update_trip_requirements", "只提出待确认需求；完整保留未改字段，改动须引用本次用户原话；不写库或规划。", _requirements_schema(), requirements)
        for group, (description, names) in GROUPS.items():
            schema = group_schema(names)
            async def propose(request: Request, arguments: dict[str, Any], *, _group=group, _names=names, _schema=schema) -> dict[str, Any]:
                await self._check(request)
                validate_arguments(arguments, _schema)
                action = arguments["action"]
                if action not in _names:
                    raise ExecutionError("travel_action_invalid")
                selected = validate_action(action, {key: value for key, value in arguments.items() if key != "action"})
                return await self._record(_group, {"kind": "proposal", "executed": False,
                    "action": selected, "confirmation_required": action not in NO_EXTRA_CONFIRMATION,
                    "browser_context_check_required": True, "tool_version": TOOL_VERSION,
                    "binding": self.snapshot.binding.as_dict(),
                    "message": "已准备一项操作提议，尚未执行。当前浏览器须核对上下文；需要确认的操作须由用户明确确认。"}, request)
            result[group] = AgentToolSpec(group, description + "；只产生浏览器提议，未执行领域动作。", schema, propose)
        guarded = {}
        for name, spec in result.items():
            async def handler(request: Request, arguments: dict[str, Any], *, _handler=spec.handler) -> Any:
                gate = execution_gate()
                try:
                    return await _handler(request, arguments)
                except asyncio.CancelledError:
                    gate.fail("cancelled")
                    raise
                except RuntimeFailure as error:
                    gate.fail(error.code)
                    raise
                except Exception:
                    gate.fail("travel_tool_failed")
                    raise ExecutionError("travel_tool_failed") from None
            guarded[name] = AgentToolSpec(spec.name, spec.description, spec.input_schema, handler,
                                         spec.allowed_agents, spec.retry_safe)
        return guarded

    async def project(self) -> TravelProjection:
        # The runner's provider/execution lease may already be closed.  This
        # own-authority check does not reopen it or permit another tool send.
        await self._check()
        with self._lock:
            rows = strict_json(_json(self._rows, MAX_PROJECTION_BYTES))
        messages = [row["data"]["message"] for row in rows]
        if not messages:
            return TravelProjection("no_tool_result", "未取得可核验的工具结果；本次没有执行旅行操作。",
                                    self.snapshot.binding, "[]")
        proposals = [row for row in rows if row["data"]["kind"] in {"proposal", "requirements_proposal"}]
        state = ("awaiting_confirmation" if proposals[0]["data"]["confirmation_required"] else "pending_browser") if proposals else (
            "clarification" if any(row["data"]["kind"] == "clarification" for row in rows) else "read_complete")
        await self._check()
        return TravelProjection(state, "\n\n".join(messages), self.snapshot.binding, _json(rows, MAX_PROJECTION_BYTES))

"""Account-bound EchoMind-derived travel assistant and durable execution receipts.

Models select registered tools; existing strict intent/POI stores retain
authority. A candidate search never confirms a POI or generates an itinerary.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import re
from typing import Annotated, Any, Callable, Literal
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agents.agent_orchestrator import AgentOrchestrator, AgentRequest
from agents.tools import ToolSpec
from core.llm_utils import RuntimeFailure
from core.qwen_gateway import QwenGateway
from core.skill_loader import SkillManager
from memory.assistant_store import AssistantError, AssistantStore, validate_assistant_extension
from api.chat import ChatRoute, User, body, connected, db_call, get_chat_store, safe_error
from api.chat_places import get_chat_places_store
from api.explanation import get_qwen_http_client
from core.business_usage import UsageError
from core.chat_cities import CITIES, legacy_city_id
from memory.chat_places_storage import ChatPlacesStore
from memory.chat_storage import ChatError, ChatStore
from core.config import Settings, get_settings
from tooling.providers.chat_places import ChatPlacesClient
from core.schemas.chat import Conversation, Identifier, Intent, Selection, SubmitTurn
from core.schemas.chat_places import Batch, SearchArguments
from tooling.travel.chat import apply_selection, provider_schema

router = APIRouter(prefix="/chat/conversations", tags=["travel-assistant"], route_class=ChatRoute)
Store = Annotated[ChatStore, Depends(get_chat_store)]
Places = Annotated[ChatPlacesStore, Depends(get_chat_places_store)]
_CODE = re.compile(r"[a-z][a-z0-9_]{0,79}")
_DIALOGUE_TOOLS = frozenset({"inspect_trip", "update_trip_requirements", "request_clarification"})


def get_assistant_store() -> AssistantStore:
    return AssistantStore()


def get_orchestrator() -> Callable[..., AgentOrchestrator]:
    """Explicit dependency boundary for controlled, non-network API tests."""
    return AgentOrchestrator


Tasks = Annotated[AssistantStore, Depends(get_assistant_store)]
Factory = Annotated[Callable[..., AgentOrchestrator], Depends(get_orchestrator)]


class StateStrict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


TaskState = Literal["queued", "running", "waiting", "awaiting_confirmation", "paused", "succeeded", "failed", "cancelled", "uncertain"]


class StepView(StateStrict):
    name: str = Field(min_length=1, max_length=80)
    state: Literal["queued", "running", "waiting", "awaiting_confirmation", "paused", "succeeded", "failed", "cancelled", "uncertain", "skipped"]
    elapsed_ms: int = Field(ge=0, le=120000)


class TaskView(StateStrict):
    id: Identifier
    state: TaskState
    model_calls: int = Field(ge=0, le=8)
    tool_calls: int = Field(ge=0, le=8)
    stop_reason: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,79}$")
    steps: list[StepView] = Field(max_length=8)


class SkillView(StateStrict):
    id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,80}$")
    name: str = Field(min_length=1, max_length=80)
    version: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,80}$")


class AssistantState(StateStrict):
    schema_version: Literal["assistant-state-v1"] = "assistant-state-v1"
    conversation_id: Identifier
    revision: int = Field(ge=0, le=1000)
    task: TaskView | None
    preferences: list = Field(max_length=0)
    skills: list[SkillView] = Field(max_length=32)
    migration_ready: bool


def ready(store: AssistantStore) -> bool:
    with store.engine.connect() as connection:
        return validate_assistant_extension(connection, required=False)


def skills() -> SkillManager:
    manager = SkillManager()
    manager.load()
    return manager


def _trace_rows(trace: dict, *, error_code: str | None = None) -> list[dict]:
    """Project runtime diagnostics into the storage's explicit safe trace list."""
    rows: list[dict] = []
    for event in trace.get("events", [])[:7]:
        stage = event.get("stage")
        row: dict[str, Any] = {"step": len(rows) + 1, "state": "succeeded"}
        if stage == "model":
            row["tool"] = "model"
            for key in ("input_tokens", "output_tokens"):
                value = event.get(key)
                if type(value) is int and 0 <= value <= 1_000_000:
                    row[key] = value
            model = event.get("model")
            if isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,100}", model):
                row["model"] = model
        elif stage == "tool" and isinstance(event.get("name"), str) and _CODE.fullmatch(event["name"]):
            row.update(tool=event["name"], elapsed_ms=min(120000, max(0, round(event.get("elapsed_ms", 0)))))
        else:
            continue
        rows.append(row)
    actual_models = sum(row.get("tool") == "model" for row in rows)
    actual_tools = sum(row.get("tool") != "model" for row in rows)
    for _ in range(max(0, min(3, trace.get("model_calls", 0)) - actual_models)):
        if len(rows) < 8:
            rows.append({"step": len(rows) + 1, "tool": "model", "state": "failed", "error_code": error_code or "request_failed"})
    for _ in range(max(0, min(4, trace.get("tool_calls", 0)) - actual_tools)):
        if len(rows) < 8:
            rows.append({"step": len(rows) + 1, "tool": "tool_attempt", "state": "failed", "error_code": error_code or "request_failed"})
    return rows


def _same_places(before: dict, after: dict) -> bool:
    return all(before.get(key) == after.get(key) for key in ("city_id", "accommodation", "required_places"))


def _expanded_selection_schema() -> dict:
    source = provider_schema()
    definitions = source.get("$defs", {})
    def expand(value: Any) -> Any:
        if isinstance(value, list):
            return [expand(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            return expand(definitions[value["$ref"].rsplit("/", 1)[-1]])
        return {key: expand(item) for key, item in value.items() if key not in {"$defs", "default", "title"}}
    return expand(source)


def _explicit_search(text: str, keyword: str) -> bool:
    actions = r"(?:搜索|查找|搜一下|搜一搜|帮我搜|帮我找|找一下|找找)"
    if re.search(r"(?:不要|不需要|别|停止|取消).{0,12}" + actions, text):
        return False
    return re.search(actions, text) is not None and keyword in text


@router.get("/{conversation_id}/assistant-state", response_model=AssistantState)
async def assistant_state(conversation_id: Identifier, raw: Request, store: Store, tasks: Tasks, user: User) -> dict:
    if raw.query_params:
        raise HTTPException(422, detail={"code": "assistant_invalid_request"})
    try:
        conversation = await db_call(store.get, user.id, conversation_id)
        is_ready = await db_call(ready, tasks)
        task = await db_call(tasks.latest, user.id, conversation_id) if is_ready else None
        projected = None
        if task is not None:
            rows = task["checkpoint"].get("trace", [])[:8]
            stop = task["checkpoint"].get("stop_reason")
            projected = {"id": task["id"], "state": task["state"],
                         "model_calls": sum(row.get("tool") == "model" for row in rows),
                         "tool_calls": sum(row.get("tool") not in (None, "model") for row in rows),
                         "stop_reason": stop if isinstance(stop, str) and _CODE.fullmatch(stop) else None,
                         "steps": [{"name": row.get("tool", "step"),
                                    "state": row.get("state", "failed") if row.get("state") in {"running", "waiting", "succeeded", "failed", "cancelled", "uncertain", "skipped"} else "failed",
                                    "elapsed_ms": min(120000, max(0, row.get("elapsed_ms", 0)))} for row in rows]}
            if task["stale"]:
                # A process can stop after the chat cancel/revision committed.
                # Preserve its historical task, but never present stale running
                # state as an actively executing or resumable current task.
                projected.update(state="uncertain", stop_reason="context_changed")
        return {"schema_version": "assistant-state-v1", "conversation_id": conversation_id, "revision": conversation["revision"],
                "task": projected, "preferences": [], "migration_ready": is_ready,
                "skills": [{"id": item["name"], "name": item["name"], "version": "travel-skills-v1"}
                           for item in skills().summary()["skills"]]}
    except ChatError as error:
        raise safe_error(error) from None


@router.post("/{conversation_id}/assistant-turns", response_model=Conversation)
async def assistant_turn(conversation_id: Identifier, raw: Request, store: Store, places: Places, tasks: Tasks,
                         user: User, factory: Factory,
                         settings: Annotated[Settings, Depends(get_settings)],
                         client: Annotated[httpx.AsyncClient, Depends(get_qwen_http_client)]) -> dict:
    # This selects a smaller presentation-compatible capability set, not an
    # authorization bypass or an arbitrary client-provided tool registry.
    # Missing preserves the established legacy callers. Reject duplicates,
    # combined values and unknown versions before reserving a durable turn.
    surfaces = raw.headers.getlist("x-trippilot-surface")
    if surfaces and surfaces != ["dialogue-v1"]:
        raise HTTPException(422, detail={"code": "assistant_invalid_surface"})
    dialogue_only = bool(surfaces)
    command = await body(raw, SubmitTurn)
    reservation: dict | None = None
    task: dict | None = None
    trace: dict = {}
    inherited: dict = {}
    place_reservation: dict | None = None
    finalized = False
    committed_followup_error = False
    try:
        # Missing extension fails before burning a turn or dispatching a model.
        initial = await db_call(store.get, user.id, conversation_id)
        if not await db_call(ready, tasks):
            raise AssistantError("assistant_migration_required")
        previous = await db_call(tasks.latest, user.id, conversation_id)
        preferences = await db_call(tasks.list_preferences, user.id)
        place_before = await db_call(places.get, user.id, conversation_id)
        if previous is not None and not previous["stale"] and not place_before["stale"]:
            inherited = {key: value for key, value in previous["checkpoint"].items()
                         if key in {"workspace", "preview", "plan", "drawing", "continuation"}}
        reservation = await db_call(store.begin_turn, user.id, conversation_id, command.operation_id,
                                    command.expected_revision, command.text)
        frozen, version = reservation["conversation"], reservation["revision"]
        task = await db_call(tasks.begin, user.id, conversation_id, command.operation_id, version)
        intent = Intent.model_validate(frozen["requirements"])
        changed_sources = frozen["sources"]
        source_by_field: dict[str, dict] = {}
        issues = frozen["issues"]
        pending_batch: dict | None = None
        search_started = False

        def confirmed_places() -> dict:
            city = legacy_city_id(intent.destination_id) or intent.destination_id
            valid = not place_before["stale"] and city is not None and place_before["city_id"] == city
            if not valid:
                return {"city_id": city, "accommodation": None, "required_places": [],
                        "state": "requires_sync_and_confirmation",
                        "message": "旧地点未与当前需求版本和城市一致，不作为本轮已确认地点。"}
            return {key: place_before[key] for key in ("city_id", "accommodation", "required_places")} | {"state": "confirmed_in_current_input"}

        async def current() -> dict:
            view = await db_call(store.get, user.id, conversation_id)
            if (view["revision"] != version or view["last_turn"] is None
                    or view["last_turn"]["operation_id"] != command.operation_id or view["last_turn"]["state"] != "running"):
                raise RuntimeFailure("context_changed")
            return view

        async def inspect_trip(arguments: dict) -> dict:
            await current()
            return {"requirements": intent.model_dump(mode="json"),
                    "confirmed_places": confirmed_places(),
                    "message": "以上是需求意向和已确认地点，不代表已有正式行程。"}

        async def update_requirements(arguments: dict) -> dict:
            nonlocal intent, changed_sources, issues, inherited
            await current()
            try:
                before = intent.model_dump(mode="json")
                selection = Selection.model_validate(arguments)
                intent, sources = apply_selection(intent, selection, command.text)
            except (ValueError, ValidationError):
                raise RuntimeFailure("requirements_invalid") from None
            for source in sources:
                source_by_field[source.field] = source.model_dump()
            changed_sources = [source_by_field[field] for field in Intent.model_fields
                               if intent.model_dump()[field] != frozen["requirements"][field] and field in source_by_field]
            issues = selection.issues
            if intent.model_dump(mode="json") != before:
                inherited = {}
            return {"requirements": intent.model_dump(mode="json"), "issues": issues,
                    "message": "已整理待确认的需求；未确认地点和未执行正式排程。"}

        async def clarify(arguments: dict) -> dict:
            await current()
            return {"message": {"missing_information": "请补充目的地、日期或要核对的具体内容。",
                                "date_ambiguous": "请说明具体日期或第几天。", "unsupported": "本轮工具暂不能执行该操作。",
                                "multiple_requests": "请先明确这次要执行的一项操作。"}[arguments["reason"]],
                    "reason": arguments["reason"]}

        async def search(arguments: dict) -> dict:
            nonlocal pending_batch, search_started
            await current()
            checked = SearchArguments.model_validate(arguments)
            if search_started:
                raise RuntimeFailure("search_limit")
            if not _explicit_search(command.text, checked.keyword):
                return {"message": "请明确提出搜索请求，并写出要搜索的地点名称；尚未发送高德查询。", "status": "explicit_search_required"}
            city = legacy_city_id(intent.destination_id) or intent.destination_id
            if place_before["city_id"] is None or checked.city_id != place_before["city_id"] or checked.city_id != city:
                return {"message": "请先在地点卡确认本次城市；尚未发送高德查询。", "status": "city_confirmation_required"}
            key = settings.amap_web_key.get_secret_value().strip()
            if not key:
                return {"message": "地点搜索尚未配置，未发送请求。", "status": "places_not_configured"}
            search_started = True
            found = await ChatPlacesClient(client, key).search(checked.keyword, checked.city_id)
            await current()
            pending_batch = Batch(id=str(uuid4()), city_id=checked.city_id, keyword=checked.keyword,
                                  role=checked.role, queried_at=datetime.now(timezone.utc).isoformat(), places=found).model_dump(mode="json")
            return {"message": "已取得真实候选，请在地点卡预览地址并明确确认；同名命中不等于身份已核实。",
                    "candidate_count": len(found), "city_id": checked.city_id, "confirmation_required": True}

        async def prepare(arguments: dict) -> dict:
            await current()
            return {"message": "请在下方行程面板核对真实地点与规则，再主动生成；本次对话没有发送排程请求。"}

        async def day(arguments: dict) -> dict:
            await current()
            # The accepted PlanningWorkspace still owns the formal response in
            # this cutover. A generic task checkpoint is not a verified formal
            # schedule and must never be read as one merely because it has days.
            return {"date": arguments["date"], "status": "local_workspace_required",
                    "message": f"请在下方正式行程面板查看 {arguments['date']} 的时间轴。正式草案目前在本地工作区，本次对话没有读取或重新生成服务端行程。"}

        async def budget(arguments: dict) -> dict:
            await current()
            return {"message": "请查看下方费用面板：它按当前正式时间轴和用户估算核算。未知不当零，小计不是完整总费用；本次未另行计算或修改费用。"}

        empty = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
        registered = [
            ToolSpec("inspect_trip", "读取本次需求与已确认地点", empty, inspect_trip),
            ToolSpec("update_trip_requirements", "完整整理待确认需求，每个改动必须引用本次原话", _expanded_selection_schema(), update_requirements, read_only=False),
            ToolSpec("request_clarification", "信息缺失或范围不支持时停止并提示", {"type": "object", "properties": {"reason": {"type": "string", "enum": ["missing_information", "date_ambiguous", "unsupported", "multiple_requests"]}}, "required": ["reason"], "additionalProperties": False}, clarify, terminal=True),
            ToolSpec("search_places", "用户明确要求且城市已确认时，仅搜索一个地点候选", {"type": "object", "properties": {"city_id": {"type": "string", "enum": list(CITIES)}, "keyword": {"type": "string", "minLength": 1, "maxLength": 80}, "role": {"type": "string", "enum": ["accommodation", "required"]}}, "required": ["city_id", "keyword", "role"], "additionalProperties": False}, search, terminal=True),
            ToolSpec("prepare_itinerary", "提示用户在正式行程卡主动生成，本工具不排程", empty, prepare, terminal=True),
            ToolSpec("get_day_schedule", "引导查看本地正式行程面板的一个日期，不把任务检查点当正式草案", {"type": "object", "properties": {"date": {"type": "string", "format": "date", "minLength": 10, "maxLength": 10}}, "required": ["date"], "additionalProperties": False}, day, terminal=True),
            ToolSpec("get_budget_summary", "提示当前费用核对入口，不复制金额计算", empty, budget, terminal=True),
        ]
        if dialogue_only:
            registered = [spec for spec in registered if spec.name in _DIALOGUE_TOOLS]
        cities = [{"id": city.id, "name": city.name} for city in CITIES.values()
                  if city.id == (legacy_city_id(intent.destination_id) or intent.destination_id) or city.name.removesuffix("市") in command.text]
        context = {"requirements": frozen["requirements"], "recent_messages": [{"role": item["role"], "text": item["text"]} for item in initial["messages"][-4:]],
                   "city_options": cities[:16], "confirmed_places": confirmed_places(),
                   "user_confirmed_preferences": [{key: item[key] for key in ("key", "value", "version", "source")}
                                                  for item in preferences if item["key"] == "travel_preferences"],
                   "other_confirmed_preference_count": sum(item["key"] != "travel_preferences" for item in preferences),
                   "preference_scope": "用户明确同意的沟通参考，未承诺转成排程硬约束；本次要求优先。"}
        if dialogue_only:
            context["interaction_surface"] = {
                "id": "dialogue-v1",
                "available_actions": sorted(_DIALOGUE_TOOLS),
                "limitations": "当前纯聊天界面只整理需求或澄清；未提供真实POI预览确认、正式行程生成、日期时间轴或费用读取。不能指向不存在的地点卡、行程或费用面板，也不能声称已执行这些操作。",
            }
        orchestrator = factory(QwenGateway(settings, client), registered, skill_manager=skills())

        async def execute() -> dict:
            nonlocal trace, finalized, inherited, place_reservation
            await current()
            outcome = await orchestrator.run(AgentRequest(command.text, user.id, conversation_id, command.operation_id, context))
            trace = outcome.trace
            await current()
            reply = (("助手建议（尚需核实）\n" + outcome.reply)
                     if trace.get("reply_source") == "model_suggestion" else outcome.reply)
            if len(reply) > 3000:
                raise RuntimeFailure("reply_limit")
            if intent.model_dump(mode="json") != initial["requirements"]:
                inherited = {}
            result = await db_call(store.finish_turn, user.id, conversation_id, command.operation_id, version,
                                   intent.model_dump(mode="json"), reply, changed_sources, issues)
            latest_places = await db_call(places.get, user.id, conversation_id)
            synced = await db_call(places.sync, user.id, conversation_id, str(uuid4()), result["revision"], latest_places["revision"])
            if intent.model_dump(mode="json") != initial["requirements"] or not _same_places(place_before, synced):
                inherited = {}
            if pending_batch is not None:
                place_reservation = await db_call(places.begin_operation, user.id, conversation_id, str(uuid4()),
                                                  result["revision"], synced["revision"], "search",
                                                  {"keyword": pending_batch["keyword"], "role": pending_batch["role"]})
                await db_call(lambda: places.finish_operation(user.id, conversation_id, place_reservation["operation_id"],
                    place_reservation["revision"], result["revision"], batch=pending_batch))
            data = inherited | {"trace": _trace_rows(trace), "stop_reason": trace.get("stop_reason", "answered"),
                                "result": {"tool_results": outcome.tool_results}, "waiting_for": "user" if trace.get("stop_reason") == "awaiting_user" else None}
            await db_call(tasks.finalize, user.id, command.operation_id, task["revision"], result["revision"], data,
                          "waiting" if trace.get("stop_reason") in {"awaiting_user", "model_budget"} else "succeeded")
            finalized = True
            latest = await db_call(store.get, user.id, conversation_id)
            if latest["revision"] != result["revision"] or latest["last_turn"]["operation_id"] != command.operation_id:
                raise RuntimeFailure("context_changed")
            return result

        return await connected(raw, (user.id, conversation_id, command.operation_id), execute)
    except BaseException as error:
        if isinstance(error, (RuntimeFailure, UsageError)) and getattr(error, "trace", None):
            trace = error.trace
        if reservation is not None and not finalized:
            try:
                latest = await db_call(store.get, user.id, conversation_id)
                active = latest["last_turn"]
                cancelled = isinstance(error, (asyncio.CancelledError, HTTPException))
                if active and active["operation_id"] == command.operation_id and active["state"] == "running":
                    if cancelled:
                        latest = await db_call(store.cancel_turn, user.id, conversation_id, command.operation_id)
                    else:
                        latest = await db_call(store.fail_turn, user.id, conversation_id, command.operation_id, reservation["revision"], "request_failed")
                if place_reservation is not None:
                    try:
                        if latest["last_turn"] and latest["last_turn"]["state"] == "succeeded":
                            await db_call(lambda: places.finish_operation(user.id, conversation_id,
                                place_reservation["operation_id"], place_reservation["revision"], latest["revision"],
                                error_code="request_uncertain"))
                        else:
                            await db_call(places.cancel, user.id, conversation_id, place_reservation["operation_id"], place_reservation["revision"])
                    except ChatError:
                        pass
                if task is not None and latest["last_turn"] and latest["last_turn"]["operation_id"] == command.operation_id:
                    state = latest["last_turn"]["state"]
                    committed_followup_error = state == "succeeded"
                    code = getattr(error, "code", "cancelled" if cancelled else "request_failed")
                    code = code if isinstance(code, str) and _CODE.fullmatch(code) else "request_failed"
                    recorded = await db_call(tasks.get, user.id, command.operation_id)
                    if recorded["revision"] != task["revision"]:
                        # finalize may have COMMITted and lost its acknowledgement.
                        # Reading its actual receipt is safe; overwriting it or
                        # replaying external work is not.
                        finalized = True
                    elif state == "succeeded":
                        await db_call(tasks.finalize, user.id, command.operation_id, task["revision"], latest["revision"],
                            inherited | {"trace": _trace_rows(trace, error_code=code),
                                         "stop_reason": "post_commit_uncertain", "waiting_for": "readback",
                                         "continuation": {"kind": "read_receipts_only", "operation_id": command.operation_id},
                                         "result": {"chat_committed": True, "post_commit_error": code, "readback_required": True}},
                            "uncertain")
                    else:
                        await db_call(tasks.finalize, user.id, command.operation_id, task["revision"], latest["revision"],
                                      inherited | {"trace": _trace_rows(trace, error_code=code), "stop_reason": code},
                                      state if state in {"failed", "cancelled", "uncertain"} else "uncertain")
            except ChatError:
                # A newer operation owns state; never close/overwrite that turn.
                pass
        if committed_followup_error and not isinstance(error, (asyncio.CancelledError, HTTPException)):
            raise HTTPException(503, detail={"code": "assistant_post_commit_readback_required",
                "message": ("会话可能已经提交，但后续处理未完整确认。请重新读取会话与执行记录；不要重发同一操作。" if dialogue_only else
                            "会话或候选可能已经提交，但后续处理未完整确认。请重新读取会话、地点卡与执行记录；不要重发同一操作。")}) from None
        if isinstance(error, ChatError):
            raise safe_error(error) from None
        if isinstance(error, RuntimeFailure):
            raise HTTPException(502, detail={"code": "assistant_" + error.code,
                                "message": "本轮助手未完成，未自动重试；请核对会话与执行记录。"}) from None
        raise

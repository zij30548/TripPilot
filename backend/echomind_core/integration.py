"""Explicit HTTP admission adapter for the preserved travel engine.

Imported lazily only for the trusted server setting.  No route, DDL, process,
embedding, Redis/Chroma startup, environment discovery or alternate transport.
The API retains durable ChatStore/AssistantStore reservation and commit control.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Awaitable, Callable
from uuid import uuid4

from core.config import Settings
from core.llm_utils import RuntimeFailure, bounded_json, strict_json
from core.schemas.chat import Conversation, Intent, Selection
from core.schemas.chat_places import ChatPlacesWorkspace
from core.schemas.dialogue import GROUPS, TOOL_VERSION, validate_action
from core.schemas.dialogue import DialogueSelectionContext
from core.usage_runtime import MeteredAsyncClient
from echomind_core.execution import CandidateScope, ExecutionGate
from echomind_core.provider import MODEL, TaskBudget
from echomind_core.travel_bridge import TravelBinding, TravelToolBridge, TrustedTravelSnapshot, VERSION as BRIDGE_VERSION
from echomind_core.travel_engine import TravelAssistantCandidate, TravelTurn, VERSION as ENGINE_VERSION
from echomind_core.upstream.agents.agent_orchestrator import Request
from tooling.travel.chat import apply_selection
from tooling.travel.dialogue import requirement_text

RUNTIME = "echomind_travel_v1"
_CODE = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")


def _detach(value: Any, limit: int = 96_000) -> Any:
    return strict_json(bounded_json(value, limit))


@dataclass(frozen=True)
class IntegratedOutcome:
    requirements: dict[str, Any]
    sources: list[dict[str, Any]]
    issues: list[str]
    message: str
    tool_results: list[dict[str, Any]]
    trace: dict[str, Any]
    metadata: dict[str, Any]


class AdmittedTravelTurn:
    """One operation captured before the existing durable begin_turn call.

    A stale place workspace is not promoted to current input.  Only an explicit
    empty/current projection is shown; the original store view remains frozen
    separately for the running-operation consistency check. No sync/write occurs
    during preparation, and confirmed POIs are never synthesized from hints.
    """
    def __init__(self, owner: str, operation: str, text: str, conversation: dict,
                 places: dict, preferences: list[dict], selection_context: DialogueSelectionContext | None = None):
        checked = Conversation.model_validate(conversation)
        workspace = ChatPlacesWorkspace.model_validate(places)
        if checked.id != workspace.conversation_id:
            raise RuntimeFailure("context_changed")
        self.initial = checked.model_dump(mode="json")
        self.original_places = workspace.model_dump(mode="json")
        self.preferences = _detach(preferences)
        self.text, self.operation = text, operation
        self.selection_context = (DialogueSelectionContext.model_validate_json(selection_context.model_dump_json())
                                  if selection_context is not None else None)
        # A unique operation/session plus monotonic conversation revision; never
        # just a city name, generated_at or a reused conversation identifier.
        self.scope = CandidateScope(owner, checked.id, str(uuid4()), checked.revision + 1)
        self.binding = TravelBinding(self.scope, checked.revision, workspace.revision, operation)
        from core.chat_cities import legacy_city_id
        city = legacy_city_id(checked.requirements.destination_id) or checked.requirements.destination_id
        valid_places = not workspace.stale and workspace.intent_revision == checked.revision and workspace.city_id in {None, city}
        if not valid_places:
            projected_places = {"schema_version": "chat-places-v1", "conversation_id": checked.id,
                "intent_revision": checked.revision, "revision": workspace.revision,
                "city_id": None, "accommodation": None, "required_places": [], "batch": None,
                "last_operation": None, "clarification": None, "proposed_city_id": None,
                "request_text": None, "stale": False}
        else:
            projected_places = self.original_places
        self.snapshot = TrustedTravelSnapshot.capture(self.binding, self.initial, projected_places, self.preferences)
        self._used = False

    async def run(self, settings: Settings, client: MeteredAsyncClient,
                  current: Callable[[], Awaitable[dict]], read_places: Callable[[], Awaitable[dict]],
                  read_preferences: Callable[[], Awaitable[list[dict]]]) -> IntegratedOutcome:
        if self._used or type(client) is not MeteredAsyncClient:
            raise RuntimeFailure("runtime_configuration")
        self._used = True
        authority = self.authority(current, read_places, read_preferences)

        bridge = TravelToolBridge(self.snapshot, authority)
        budget = TaskBudget()
        gate = ExecutionGate(self.scope, lambda: self.scope, budget)
        candidate = TravelAssistantCandidate(bridge, selection_context=self.selection_context)
        request = Request(message=self.text, user_id=self.scope.owner_id,
            conv_id=self.scope.conversation_id, request_id=self.operation,
            history=[{"role": row["role"], "content": row["text"]} for row in self.initial["messages"][-6:]])
        result = await candidate.run(request, gate, settings, client)
        if gate._usage_error is not None:
            gate._usage_error.trace = {"model_calls": result.counts[0], "tool_calls": result.counts[1], "events": []}
            raise gate._usage_error
        try:
            return self._project(result)
        except RuntimeFailure as error:
            # Rendering/compatibility limits must not erase consumed attempts.
            if not error.trace:
                error.trace = {"runtime_version": ENGINE_VERSION, "role": result.role,
                    "model_calls": result.counts[0], "tool_calls": result.counts[1],
                    "output_tokens_reserved": result.counts[2], "usage_has_unknown": True,
                    "elapsed_ms": result.elapsed_ms, "stop_reason": error.code, "events": []}
            raise

    def authority(self, current: Callable[[], Awaitable[dict]], read_places: Callable[[], Awaitable[dict]],
                  read_preferences: Callable[[], Awaitable[list[dict]]]) -> Callable[[], Awaitable[TravelBinding]]:
        """The same durable authority check for both explicit runtime profiles."""
        async def check() -> TravelBinding:
            view = await current()
            live_places, live_preferences = await read_places(), await read_preferences()
            expected_places = deepcopy(self.original_places)
            expected_places.update(stale=True, batch=None, clarification=None, proposed_city_id=None, request_text=None)
            if expected_places["revision"] == 0 and expected_places["city_id"] is None and expected_places["last_operation"] is None:
                expected_places["intent_revision"] = self.scope.input_version
            if (view["requirements"] != self.initial["requirements"] or view["sources"] != self.initial["sources"]
                    or view["issues"] != self.initial["issues"] or view["confirmed_revision"] is not None
                    or live_places != expected_places or live_preferences != self.preferences):
                raise RuntimeFailure("context_changed")
            # Recheck the durable running receipt after the other reads.
            await current()
            return self.binding
        return check

    def model_events(self, turn: TravelTurn) -> list[dict]:
        return [{"stage": "model", "model": MODEL} for _ in range(turn.counts[0])]

    def format_message(self, messages: list[str]) -> str:
        message = "\n\n".join(messages)
        if len(message) > 3000:
            raise RuntimeFailure("reply_limit")
        return message

    def _project(self, turn: TravelTurn) -> IntegratedOutcome:
        trace = {"runtime_version": ENGINE_VERSION, "role": turn.role,
                 "model_calls": turn.counts[0], "tool_calls": turn.counts[1],
                 "output_tokens_reserved": turn.counts[2], "usage_has_unknown": True,
                 "elapsed_ms": turn.elapsed_ms, "stop_reason": turn.state, "events": []}
        # The original source does not expose per-send usage in its run result.
        # Counts are conservative budget reservations, never invented Token use.
        if turn.state == "failed" or turn.projection is None:
            code = turn.code if isinstance(turn.code, str) and _CODE.fullmatch(turn.code) else "execution_failed"
            error = RuntimeFailure(code)
            error.trace = trace
            raise error
        # A complete run has received each successful model response. Token
        # values and per-step timings remain unknown; do not let the legacy
        # trace fallback label these known completed stages as failed attempts.
        trace["events"] = self.model_events(turn)
        projection = turn.projection
        if (set(projection) != {"schema_version", "state", "message", "binding", "tool_results"}
                or projection["schema_version"] != BRIDGE_VERSION or projection["binding"] != self.binding.as_dict()
                or not isinstance(projection["tool_results"], list) or not 1 <= len(projection["tool_results"]) <= 4):
            raise RuntimeFailure("projection_invalid")
        intent, sources, issues = self.initial["requirements"], self.initial["sources"], self.initial["issues"]
        rows, messages = [], []
        for row in projection["tool_results"]:
            if not isinstance(row, dict) or set(row) != {"name", "data"} or not isinstance(row["data"], dict):
                raise RuntimeFailure("projection_invalid")
            name, data = row["name"], row["data"]
            if data.get("binding") != self.binding.as_dict():
                raise RuntimeFailure("projection_invalid")
            kind, message = data.get("kind"), data.get("message")
            if not isinstance(message, str) or not message.strip():
                raise RuntimeFailure("projection_invalid")
            if kind == "proposal":
                value = data.get("action")
                if (name not in GROUPS or data.get("executed") is not False or data.get("tool_version") != TOOL_VERSION
                        or type(value) is not dict or set(value) != {"name", "arguments"}
                        or value["name"] not in GROUPS[name][1]):
                    raise RuntimeFailure("projection_invalid")
                action = validate_action(value["name"], value["arguments"])
                # Exact existing wire; this is an unexecuted browser proposal.
                rows.append({"name": name, "data": {"message": message, "dialogue_action": action}})
            elif kind == "requirements_proposal":
                if name != "update_trip_requirements" or data.get("executed") is not False:
                    raise RuntimeFailure("projection_invalid")
                selected = Selection.model_validate({key: data[key] for key in ("requirements", "sources", "issues")})
                changed, quoted = apply_selection(Intent.model_validate(self.initial["requirements"]), selected, self.text)
                intent, sources, issues = changed.model_dump(mode="json"), [source.model_dump() for source in quoted], selected.issues
                message = requirement_text(changed, changed=True) + "\n已整理为当前会话的待确认需求草稿；尚未正式确认，未生成或采用行程。"
                rows.append({"name": name, "data": {"message": message, "draft_requirements_updated": True, "confirmed": False}})
            elif kind in {"read", "guidance", "clarification"}:
                # No binding, coordinate, candidate batch or arbitrary raw store
                # body in the public task receipt. Program text is sufficient.
                rows.append({"name": name, "data": {"message": message, "kind": kind,
                    "executed": data.get("executed") is True}})
            else:
                raise RuntimeFailure("projection_invalid")
            messages.append(message)
            trace["events"].append({"stage": "tool", "name": name, "elapsed_ms": 0})
        # A stored draft is not a formal user confirmation. All other results
        # remain current-input reads, fixed guidance or an unexecuted proposal.
        message = self.format_message(messages)
        trace["stop_reason"] = "awaiting_user" if turn.state in {"awaiting_confirmation", "pending_browser", "clarification"} else "answered"
        return IntegratedOutcome(_detach(intent), _detach(sources), list(issues), message, _detach(rows), trace,
            {"runtime": RUNTIME, "engine_version": ENGINE_VERSION, "bridge_version": BRIDGE_VERSION,
             "domain_version": turn.versions["domain"], "skills": _detach(turn.versions["skills"]),
             "requested_model": MODEL, "upstream_model": "unknown", "per_step_elapsed": "unknown",
             "model_calls": turn.counts[0], "tool_calls": turn.counts[1],
             "output_tokens_reserved": turn.counts[2], "usage": "unknown"})

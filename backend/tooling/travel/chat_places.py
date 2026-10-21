"""Small typed capability catalogue and deterministic chat-2 projections.

This is one tool selection, not a loop, MCP transport or general plug-in host.
"""
from dataclasses import dataclass

from core.schemas.chat_places import ChatPlacesWorkspace


@dataclass(frozen=True)
class Capability:
    name: str
    version: str
    permission: str
    side_effect: str
    max_model_calls: int
    max_logical_searches: int
    timeout_seconds: int
    cancellation: str
    confirmation: str
    input_contract: str
    output_contract: str
    quota_services: tuple[str, ...]
    max_upstream_attempts: int
    stop_condition: str
    external_effect: str
    required_user_confirmation: bool


CAPABILITIES = (
    Capability("search_places", "chat-place-search-v1", "owner_conversation", "temporary_write",
               1, 1, 45, "cancel_and_recheck_versions", "candidate_is_not_confirmed",
               "SearchArguments", "Batch", ("qwen_text", "amap_poi"), 3, "one_selection_then_preview_or_error",
               "read_only", False),
    Capability("request_clarification", "chat-place-clarification-v1", "owner_conversation", "temporary_write",
               1, 0, 20, "cancel_and_recheck_versions", "does_not_change_city",
               "ClarificationArguments", "ChatPlacesWorkspace", ("qwen_text",), 1, "await_explicit_user_input",
               "none", False),
)
CAPABILITY_NAMES = frozenset(item.name for item in CAPABILITIES)
MAX_OPERATION_SECONDS = 45


@dataclass(frozen=True)
class SkillDefinition:
    """Description of the concrete RPC flow, not a dynamic workflow language."""
    name: str
    version: str
    input_contract: str
    output_contract: str
    steps: tuple[str, ...]
    permission: str
    side_effect: str
    bindings: tuple[str, ...]
    max_required_places: int
    max_model_calls_per_request: int
    max_logical_searches_per_request: int
    request_deadline_seconds: int
    quota_services: tuple[str, ...]
    user_only_actions: tuple[str, ...]
    stop_conditions: tuple[str, ...]


PREPARE_PLACE = SkillDefinition(
    name="prepare_place", version="chat-prepare-place-v1",
    input_contract="ChatPlacesRequest", output_contract="ChatPlacesWorkspace",
    steps=("explicit_city_confirmation", "explicit_search_or_single_tool_selection", "candidate_preview", "explicit_poi_confirmation"),
    permission="owner_conversation", side_effect="temporary_write",
    bindings=("account", "conversation", "intent_revision", "workspace_revision", "operation_id", "batch_id"),
    max_required_places=6, max_model_calls_per_request=1, max_logical_searches_per_request=1,
    request_deadline_seconds=MAX_OPERATION_SECONDS, quota_services=("qwen_text", "amap_poi"),
    user_only_actions=("set_city", "confirm", "remove", "sync", "cancel"),
    stop_conditions=("needs_city_confirmation", "needs_clarification", "candidate_preview", "explicit_confirmation_complete",
                     "invalid_output", "quota_denied", "request_uncertain", "cancelled", "context_changed", "deadline"),
)
SKILLS = (PREPARE_PLACE,)


def model_context(workspace: ChatPlacesWorkspace, question: str) -> dict:
    """Only this account's bounded question and city; never geometry or money."""
    return {
        "question": question,
        "confirmed_city_id": workspace.city_id,
        "required_count": len(workspace.required_places),
        "accommodation_confirmed": workspace.accommodation is not None,
        "request_binding": {
            "conversation_id": workspace.conversation_id,
            "intent_revision": workspace.intent_revision,
            "workspace_revision": workspace.revision,
        },
    }

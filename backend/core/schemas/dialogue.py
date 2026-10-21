"""Finite dialogue proposals; the browser owns execution and formal snapshots.

This module never executes domain operations or accepts model-authored travel
facts. A proposal is meaningful only with its enclosing conversation revision
and turn identity; the current browser must validate its own state again.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.llm_utils import RuntimeFailure, validate_arguments
from core.schemas.chat import Identifier, Strict, SubmitTurn

VERSION = "dialogue-action-v1"
TOOL_VERSION = "travel-dialogue-v2"


class DialogueSelectionItem(Strict):
    ordinal: int = Field(ge=1, le=20)
    id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    type: Literal["place", "saved", "date"]

    @field_validator("id", "name")
    @classmethod
    def bounded_label(cls, value: str) -> str:
        if value != value.strip() or not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("invalid_selection_label")
        return value


class DialogueSelectionContext(Strict):
    """Untrusted browser display labels, never domain facts or authority."""
    workspace_version: Identifier
    display: Literal["none", "candidates", "schedule", "preview", "budget", "history", "places"]
    items: list[DialogueSelectionItem] = Field(max_length=20)

    @model_validator(mode="after")
    def unique_items(self) -> "DialogueSelectionContext":
        if (len({item.ordinal for item in self.items}) != len(self.items)
                or len({(item.type, item.id) for item in self.items}) != len(self.items)
                or self.display == "none" and self.items):
            raise ValueError("invalid_selection_context")
        return self


class SubmitDialogueTurn(SubmitTurn):
    dialogue_context: DialogueSelectionContext | None = None


def obj(fields: dict[str, dict]) -> dict:
    return {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}


def _load_registry() -> tuple[dict[str, dict], dict[str, tuple[str, tuple[str, ...]]]]:
    source = Path(__file__).resolve().parents[3] / "shared" / "dialogue-actions.json"
    data = json.loads(source.read_text(encoding="utf-8"))
    if (set(data) != {"schema_version", "tool_version", "actions", "groups"}
            or data["schema_version"] != VERSION or data["tool_version"] != TOOL_VERSION):
        raise RuntimeError("dialogue_registry_invalid")
    actions = {}
    for row in data["actions"]:
        if (set(row) != {"name", "arguments"} or row["name"] in actions
                or row["arguments"] != obj(row["arguments"].get("properties", {}))):
            raise RuntimeError("dialogue_registry_invalid")
        actions[row["name"]] = row["arguments"]["properties"]
    groups = {}
    seen = []
    for row in data["groups"]:
        if (set(row) != {"name", "description", "actions"} or row["name"] in groups
                or not 1 <= len(row["actions"]) <= 8 or any(name not in actions for name in row["actions"])):
            raise RuntimeError("dialogue_registry_invalid")
        groups[row["name"]] = (row["description"], tuple(row["actions"]))
        seen.extend(row["actions"])
    if len(seen) != len(set(seen)) or set(seen) != set(actions) or len(groups) > 12:
        raise RuntimeError("dialogue_registry_invalid")
    return actions, groups


ACTION_FIELDS, GROUPS = _load_registry()


def validate_action(name: str, arguments: object) -> dict:
    if name not in ACTION_FIELDS:
        raise RuntimeFailure("dialogue_action_invalid")
    validate_arguments(arguments, obj(ACTION_FIELDS[name]))
    if any(isinstance(value, str) and key != "value" and (not value.strip() or value != value.strip())
           for key, value in arguments.items()):
        raise RuntimeFailure("dialogue_action_invalid")
    if name == "read_schedule" and arguments["date"] is not None and arguments["day"] is not None:
        raise RuntimeFailure("dialogue_action_invalid")
    if name in {"set_lunch", "set_daily_window"} and arguments["start"] >= arguments["end"]:
        raise RuntimeFailure("dialogue_action_invalid")
    if name == "set_cost" and ((arguments["kind"] in {"ticket", "meal"}) != (arguments["selector"] is not None)):
        raise RuntimeFailure("dialogue_action_invalid")
    return {"name": name, "arguments": deepcopy(arguments)}


def group_schema(names: tuple[str, ...]) -> dict:
    # Root is an object for native function protocols. Each anyOf branch has
    # its own exact keys, so arguments belonging to another action are illegal.
    variants = [obj({"action": {"type": "string", "const": name}, **ACTION_FIELDS[name]}) for name in names]
    union_fields: dict[str, dict] = {"action": {"type": "string", "enum": list(names)}}
    for name in names:
        union_fields.update(deepcopy(ACTION_FIELDS[name]))
    return {"type": "object", "properties": union_fields, "required": ["action"],
            "additionalProperties": False, "anyOf": variants}


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str
    arguments: dict[str, Any]

    @model_validator(mode="after")
    def validated_arguments(self) -> "Action":
        try:
            validate_action(self.name, self.arguments)
        except RuntimeFailure:
            raise ValueError("invalid_dialogue_action") from None
        return self


class DialogueAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["dialogue-action-v1"] = VERSION
    conversation_id: Identifier
    revision: int = Field(ge=0, le=1000)
    operation_id: Identifier | None
    action: Action | None

    @model_validator(mode="after")
    def proposal_has_turn(self) -> "DialogueAction":
        if self.action is not None and self.operation_id is None:
            raise ValueError("dialogue_action_turn_required")
        return self

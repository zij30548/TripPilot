"""TripPilot travel tool registry, adapted from EchoMind's AgentToolSpec.

The handler owns account/version checks and explicit business confirmation.
Metadata and prompts do not grant permission to modify a formal itinerary.
"""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

ROLES = frozenset({"coordinator", "places", "planning", "budget"})


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
    allowed_roles: tuple[str, ...] = ("coordinator", "places", "planning", "budget")
    read_only: bool = True
    terminal: bool = False
    timeout_seconds: float = 15.0
    version: str = "travel-tool-v1"

    def __post_init__(self) -> None:
        if (re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.name) is None
                or not 1 <= len(self.description) <= 800 or not callable(self.handler)
                or not self.allowed_roles or set(self.allowed_roles) - ROLES
                or not 0 < self.timeout_seconds <= 30
                or self.input_schema.get("type") != "object"
                or self.input_schema.get("additionalProperties") is not False):
            raise ValueError("invalid_tool_spec")

    def native_definition(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.name,
                "description": self.description, "parameters": self.input_schema}}

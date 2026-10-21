"""Qwen native tools adapter for the EchoMind-derived travel runtime.

Reuses TripPilot's fixed Beijing endpoint, 20-second whole-operation deadline,
bounded response and durable dispatch metering. No Anthropic protocol, retries,
provider fallback, untrusted origin or arbitrary model selection is introduced.
"""
from __future__ import annotations

from typing import Any

from core.config import Settings
from tooling.providers.qwen import MODEL, QwenClient, QwenError
from core.usage_runtime import MeteredAsyncClient
from core.llm_utils import ProviderResponse, RuntimeFailure, bounded_json, parse_native_response

PROMPT_VERSION = "trippilot-echo-travel-v1"


class QwenGateway:
    def __init__(self, settings: Settings, client: MeteredAsyncClient) -> None:
        if not isinstance(client, MeteredAsyncClient):
            raise ValueError("metered_qwen_client_required")
        self.settings = settings
        self.client = client

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], *, max_tokens: int = 2048) -> ProviderResponse:
        if self.settings.qwen_model != MODEL or not 1 <= max_tokens <= 2048:
            raise RuntimeFailure("configuration")
        payload: dict[str, Any] = {
            "model": MODEL, "stream": False, "enable_thinking": False,
            "max_tokens": max_tokens, "parallel_tool_calls": False,
            "messages": messages,
        }
        if tools:
            payload.update({"tools": tools, "tool_choice": "auto"})
        bounded_json(payload, 48_000)
        try:
            result = await QwenClient(self.settings, self.client)._request(payload)
        except QwenError as error:
            raise RuntimeFailure(error.code) from None
        return parse_native_response(result, max_tokens)

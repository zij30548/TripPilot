"""Offline harness for the unchanged upstream assertions, never product code.

The original tests construct SDK clients with dummy keys before replacing their
model methods. Replace only that constructor here; an unscripted model call is
an error. The real Qwen bridge has its own tests outside this fixture's scope.
All INET socket connections are forbidden, including local live user services.
The original MCP tests use stdio and in-memory ASGI, not external endpoints.
"""
from __future__ import annotations

import socket
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


class OfflineReferenceClient:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.messages = SimpleNamespace(create=AsyncMock(side_effect=AssertionError("unscripted_model_call")))

    async def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def offline_reference_boundary(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    monkeypatch.setenv("INTENT_EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("ECHOMIND_SEED_DEMO_DOCS", "false")
    monkeypatch.setenv("ANONYMIZED_TELEMETRY", "False")
    for name, module in tuple(sys.modules.items()):
        if name.startswith("echomind_core.upstream.") and hasattr(module, "AsyncAnthropic"):
            monkeypatch.setattr(module, "AsyncAnthropic", OfflineReferenceClient)

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def connect(sock: socket.socket, address: object):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError("offline_suite_forbids_network_connections")
        return original_connect(sock, address)

    def connect_ex(sock: socket.socket, address: object):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError("offline_suite_forbids_network_connections")
        return original_connect_ex(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    yield

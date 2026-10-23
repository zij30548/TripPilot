"""Explicit stage-2 composition. No route, environment discovery or auto-indexing.

This joins the preserved algorithms to scoped local storage and the one metered
Qwen transport. Opening resources sends nothing. A trusted caller must establish
the existing durable owner/usage context and explicitly run a bounded task.
MySQL remains authoritative; none of these cache operations adopts a plan,
commits a conversation, infers consent or restores a production task.
"""
from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable

from core.config import Settings
from core.usage_runtime import MeteredAsyncClient, _context
from echomind_core.embedding import DIMENSIONS, EMBEDDING_MODEL, QwenEmbeddingClient, QwenIntentEmbedding
from echomind_core.execution import (
    CandidateRun, CandidateRunner, CandidateScope, ExecutionError, ExecutionGate,
    SafeOrchestrator, SafeToolManager, bind_execution, execution_gate,
)
from echomind_core.provider import bind_provider
from echomind_core.storage import IsolatedMemoryStore, StorageConfig
from echomind_core.upstream.agents.agent_orchestrator import Request
from echomind_core.upstream.tooling.tool_manager import Tool


class CandidateResources:
    """One owner/conversation/session; never a shared global application singleton.

    The explicit ``task`` context is for trusted index/cache preparation and
    integration checks, not a model-exposed write tool. It shares exactly the
    same task limits/counter as an ordinary run and does not grant paid calls.
    Large intent-template preparation requires a separately reviewed workflow;
    cold original templates fail before sending, rather than auto-filling them.
    """

    def __init__(self) -> None:
        raise ExecutionError("use_explicit_open")

    @classmethod
    async def open(cls, config: StorageConfig, scope: CandidateScope,
                   current: Callable[[], CandidateScope]) -> CandidateResources:
        if (type(config) is not StorageConfig or type(scope) is not CandidateScope or not callable(current)
                or config.embedding_model != EMBEDDING_MODEL
                or config.embedding_dimensions != DIMENSIONS
                ):
            raise ExecutionError("configuration")
        try:
            latest = current()
            valid = type(latest) is CandidateScope and latest == scope
        except Exception:
            valid = False
        if not valid:
            raise ExecutionError("context_changed")
        self = object.__new__(cls)
        self.scope, self.current = scope, current
        self._closed, self._active = False, None
        self._latest_version = -1
        self._embedding_client = QwenEmbeddingClient()
        # Revision is the explicit template/index schema revision, NOT an input
        # version. Request versions are enforced independently by ExecutionGate.
        self.embedding = QwenIntentEmbedding(scope.owner_id, "intent-index-v1")

        async def vectors(texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
            gate = self._gate()
            result = await self._embedding_client.embed(list(texts))
            gate.check()
            return result.vectors

        self._store = await IsolatedMemoryStore.open(config, scope.owner_id, vectors)
        try:
            try:
                latest = current()
                valid = type(latest) is CandidateScope and latest == scope
            except Exception:
                valid = False
            if not valid:
                raise ExecutionError("context_changed")
            self.tool_manager = SafeToolManager()

            async def search(values: dict[str, Any], context: dict[str, Any]) -> Any:
                return await self.search(values["query"], values.get("top_k", 5))

            self.tool_manager.register_read_only(Tool(
                name="knowledge_search", description="Scoped local candidate index; not verified travel facts",
                schema={"type": "object", "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 5}},
                    "required": ["query"], "additionalProperties": False},
                handler=search, timeout_s=20,
            ))
            self.orchestrator = SafeOrchestrator(self.embedding, rag_tool_manager=self.tool_manager)
            self.runner = CandidateRunner(self.orchestrator)
        except BaseException:
            await self._store.aclose()
            raise
        return self

    def _gate(self) -> ExecutionGate:
        gate = execution_gate()
        if self._closed or self._active is not gate or (
                gate.scope.owner_id, gate.scope.conversation_id, gate.scope.session_id) != (
                self.scope.owner_id, self.scope.conversation_id, self.scope.session_id):
            raise ExecutionError("context_changed")
        gate.check()
        self._usage()
        return gate

    def _usage(self) -> None:
        context = _context()
        if (context.owner_id != self.scope.owner_id
                or context.request_path != f"/chat/conversations/{self.scope.conversation_id}/assistant-turns"):
            raise ExecutionError("context_changed")

    def _start(self, gate: ExecutionGate) -> None:
        if self._closed or self._active is not None or gate.current is not self.current or (
                gate.scope.owner_id, gate.scope.conversation_id, gate.scope.session_id) != (
                self.scope.owner_id, self.scope.conversation_id, self.scope.session_id) or (
                gate.scope.input_version < self._latest_version):
            raise ExecutionError("context_changed")
        gate.check()
        self._usage()
        self._latest_version = gate.scope.input_version
        self._active = gate

    @asynccontextmanager
    async def task(self, gate: ExecutionGate, settings: Settings,
                   client: MeteredAsyncClient) -> AsyncIterator[CandidateResources]:
        self._start(gate)
        task = asyncio.current_task()
        if task is not None:
            gate._pending.add(task)
        try:
            with bind_execution(gate), bind_provider(settings, client, gate.budget):
                async with asyncio.timeout(gate.budget.remaining_seconds):
                    yield self
                gate.check()
        finally:
            if task is not None:
                gate._pending.discard(task)
            self._active = None

    async def run(self, request: Request, gate: ExecutionGate, settings: Settings,
                  client: MeteredAsyncClient) -> CandidateRun:
        self._start(gate)
        try:
            # No implicit context embedding, index preparation or preference
            # update. Domain composition/authoritative MySQL reads are stage 3.
            return await self.runner.run(request, gate, settings, client)
        finally:
            self._active = None

    async def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        gate = self._gate()
        result = await self._store.search(query, top_k)
        gate.check()
        return result

    async def context(self, revision: int, query: str = "") -> Any:
        gate = self._gate()
        result = await self._store.get_context(self.scope.conversation_id, revision, query)
        gate.check()
        return result

    async def publish_cache(self, revision: int, messages: list[dict[str, str]],
                            state: dict[str, Any] | None = None) -> None:
        gate = self._gate()
        await self._store.publish_working(self.scope.conversation_id, revision, messages, state)
        gate.check()

    async def import_documents(self, documents: list[dict[str, str]]) -> int:
        gate = self._gate()
        result = await self._store.add_documents(documents)
        gate.check()
        return result

    async def aclose(self) -> None:
        self._closed = True
        if self._active is not None:
            self._active.invalidate()
        await self._store.aclose()

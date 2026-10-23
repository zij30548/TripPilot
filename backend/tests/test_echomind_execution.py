"""Original algorithms + stage2 execution boundary, entirely controlled wire.

Temporary durable counters are real; model data is MockTransport, never paid.
No user store, service, downloader or environment credential is instantiated.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import importlib.util
import io
import json
import logging
import threading
import unittest
from unittest.mock import AsyncMock

import httpx

if any(importlib.util.find_spec(name) is None for name in ("jsonschema", "prometheus_client")):
    raise unittest.SkipTest("EchoMind execution checks require the explicit echomind extra")

from core.config import Settings
from core.usage_runtime import MeteredAsyncClient
from echomind_core.diagnostics import SafeEvent, emit, quarantine_upstream_logging
from echomind_core.execution import (
    CandidateRunner, CandidateScope, ExecutionError, ExecutionGate, GuardedToolRegistry,
    SafeIntentRecognizer, SafeJudge, SafeOrchestrator, SafeToolManager, ToolDeclaration, bind_execution,
)
from echomind_core.provider import MODEL, TaskBudget, bind_provider
from echomind_core.upstream.agents.agent_orchestrator import AgentType, Request
from echomind_core.upstream.agents.tools import AgentToolSpec
from echomind_core.upstream.core.intent_recognizer import IntentCategory, UrgencyLevel
from echomind_core.upstream.monitor.performance_monitor import PerformanceMonitor
from echomind_core.upstream.tooling.tool_manager import CircuitBreaker, CircuitState, Tool
from tests.usage_fixture import UsageFixture

SCOPE = CandidateScope("a" * 32, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                       "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", 0)
PATH = "/chat/conversations/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/assistant-turns"
SCHEMA = {"type": "object", "properties": {"query": {"type": "string", "maxLength": 100}},
          "required": ["query"], "additionalProperties": False}


class ControlledEmbedding:
    enabled = True

    def __init__(self):
        self.remote_templates, self.local_templates = {}, {}

    async def vectors(self, message, templates, local_embed):
        return ({key: [[1.0, 0.0] for _ in texts] for key, texts in templates.items()},
                [1.0, 0.0], "controlled-only")


def request(**values):
    defaults = {"message": "controlled read", "user_id": SCOPE.owner_id, "conv_id": SCOPE.conversation_id,
                "intent": IntentCategory.QUERY, "intent_group": "query", "urgency": UrgencyLevel.LOW}
    return Request(**(defaults | values))


def native(text: str = "controlled answer", *, tool: str | None = None, arguments: str = '{"query":"safe"}'):
    message = {"role": "assistant", "content": text}
    if tool is not None:
        message = {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call_test", "type": "function", "function": {"name": tool, "arguments": arguments},
        }]}
    return {"model": MODEL, "choices": [{"finish_reason": "tool_calls" if tool else "stop", "message": message}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3}}


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = UsageFixture()
        self.addCleanup(self.fixture.close)
        self.settings = Settings(_env_file=None, qwen_api_key="controlled-not-real",
                                 qwen_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", qwen_model=MODEL)
        self.current, self.sent = SCOPE, []

    def gate(self, budget=None):
        return ExecutionGate(SCOPE, lambda: self.current, budget or TaskBudget())

    def client(self, responses):
        def controlled(message):
            self.sent.append(json.loads(message.content))
            response = responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return httpx.Response(200, json=response)
        return MeteredAsyncClient(transport=httpx.MockTransport(controlled), follow_redirects=False)

    def tool(self, handler):
        return AgentToolSpec("read_data", "Trusted local read only", SCHEMA, handler)

    def registry(self, handler):
        spec = self.tool(handler)
        return GuardedToolRegistry((ToolDeclaration(spec, True),)).wrap(spec)

    async def test_original_agent_native_feedback_executes_registered_handler_once(self):
        handler = AsyncMock(return_value={"source": "controlled", "success": True})
        runner = CandidateRunner(SafeOrchestrator(ControlledEmbedding(), {"read_data": self.tool(handler)}))
        gate = self.gate()
        with self.fixture.bind(PATH):
            async with self.client([native(tool="read_data"), native()]) as client:
                result = await runner.run(request(), gate, self.settings, client)
        self.assertEqual((result.state, result.code, result.counts), ("complete", None, (2, 1, 1800)))
        handler.assert_awaited_once()
        self.assertEqual(self.sent[1]["messages"][-1]["role"], "tool")
        self.assertEqual(result.result.tools_used, ["read_data"])
        self.assertTrue(gate.budget.stopped)  # lease closed, cannot reuse children

    async def test_out_of_tool_budget_never_executes_handler(self):
        handler = AsyncMock(return_value={"success": True})
        tool = self.registry(handler)
        gate = self.gate(TaskBudget(tool_limit=1))
        with bind_execution(gate):
            await tool.handler(request(), {"query": "first"})
            with self.assertRaisesRegex(ExecutionError, "task_budget"):
                await tool.handler(request(), {"query": "second"})
        self.assertEqual(handler.await_count, 1)
        self.assertEqual(gate.budget.counts[1], 1)

    async def test_scope_validates_identity_versions_and_unsafe_declarations(self):
        for field, value in (("owner_id", "model-owner"), ("session_id", "old"),
                             ("input_version", True), ("input_version", -1)):
            with self.assertRaises(ExecutionError):
                replace(SCOPE, **{field: value})
        spec = self.tool(lambda *_: {})
        for declaration in (ToolDeclaration(spec, False), ToolDeclaration(replace(spec, retry_safe=False), True)):
            with self.assertRaisesRegex(ExecutionError, "tool_registry"):
                GuardedToolRegistry((declaration,))

    async def test_model_cannot_replace_trusted_handler_or_extend_schema(self):
        first = self.tool(lambda *_: {})
        registry = GuardedToolRegistry((ToolDeclaration(first, True),))
        for spec in (replace(first, handler=lambda *_: {}), replace(first, name="fake"),
                     replace(first, input_schema={"type": "object"})):
            with self.assertRaisesRegex(ExecutionError, "tool_registry"):
                registry.wrap(spec)

    async def test_invalid_arguments_do_not_reserve_or_execute(self):
        handler = AsyncMock(return_value={})
        tool, gate = self.registry(handler), self.gate()
        with bind_execution(gate):
            for arguments in ({"query": "x", "private-key-name": "private-value"}, {"query": 3}, {"query": "x" * 101}):
                with self.assertRaisesRegex(ExecutionError, "tool_arguments"):
                    await tool.handler(request(), arguments)
        handler.assert_not_awaited()
        self.assertEqual(gate.budget.counts, (0, 0, 0))

    async def test_wrong_owner_context_rejected_before_tool(self):
        handler = AsyncMock(return_value={})
        with bind_execution(self.gate()):
            with self.assertRaisesRegex(ExecutionError, "context_changed"):
                await self.registry(handler).handler(request(user_id="b" * 32), {"query": "x"})
        handler.assert_not_awaited()

    async def test_late_response_after_input_change_is_discarded(self):
        async def handler(*_):
            self.current = replace(SCOPE, input_version=1)
            return {"private": "must not publish"}
        with bind_execution(self.gate()):
            with self.assertRaisesRegex(ExecutionError, "context_changed"):
                await self.registry(handler).handler(request(), {"query": "x"})

    async def test_aba_explicit_invalidation_cannot_restore_old_scope(self):
        handler = AsyncMock(return_value={})
        gate = self.gate()
        with bind_execution(gate):
            self.current = replace(SCOPE, input_version=1)
            gate.invalidate()
            self.current = SCOPE
            with self.assertRaisesRegex(ExecutionError, "context_changed"):
                await self.registry(handler).handler(request(), {"query": "x"})
        handler.assert_not_awaited()

    async def test_cancellation_keeps_reserved_attempt_and_cannot_restart(self):
        started, finish = asyncio.Event(), asyncio.Event()
        async def handler(*_):
            started.set()
            await finish.wait()
            return {}
        gate, tool = self.gate(), self.registry(handler)
        with bind_execution(gate):
            task = asyncio.create_task(tool.handler(request(), {"query": "x"}))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            with self.assertRaisesRegex(ExecutionError, "cancelled"):
                await tool.handler(request(), {"query": "x"})
        self.assertEqual(gate.budget.counts[1], 1)

    async def test_sync_handler_cancellation_drains_thread_before_scope_release(self):
        started, finish = threading.Event(), threading.Event()
        def handler(*_):
            started.set()
            finish.wait(2)
            return {}
        gate, tool = self.gate(), self.registry(handler)
        try:
            with bind_execution(gate):
                task = asyncio.create_task(tool.handler(request(), {"query": "x"}))
                await asyncio.to_thread(started.wait, 1)
                task.cancel()
                await asyncio.sleep(.01)
                self.assertFalse(task.done())
                task.cancel()  # repeated cancellation must not orphan thread
                finish.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        finally:
            finish.set()
        self.assertFalse(gate._pending)

    async def test_caller_mutation_during_model_wait_cannot_change_frozen_request(self):
        started, finish = asyncio.Event(), asyncio.Event()
        original = request(entities={"field": ["captured"]})
        seen = []
        async def handler(req, _):
            seen.append(req.entities)
            return {"success": True}
        runner = CandidateRunner(SafeOrchestrator(ControlledEmbedding(), {"read_data": self.tool(handler)}))
        wire_count = 0
        async def controlled(message):
            nonlocal wire_count
            wire_count += 1
            if wire_count == 1:
                started.set()
                await finish.wait()
                return httpx.Response(200, json=native(tool="read_data"))
            return httpx.Response(200, json=native())
        with self.fixture.bind(PATH):
            async with MeteredAsyncClient(transport=httpx.MockTransport(controlled), follow_redirects=False) as client:
                task = asyncio.create_task(runner.run(original, self.gate(), self.settings, client))
                await started.wait()
                original.entities["field"][0] = "later-mutated"
                finish.set()
                result = await task
        self.assertEqual(result.state, "complete")
        self.assertEqual(seen, [{"field": ["captured"]}])
        self.assertEqual(original.tool_attempts, [])

    async def test_deadline_releases_pending_and_counts_attempt(self):
        async def handler(*_):
            await asyncio.sleep(1)
        gate = self.gate(TaskBudget(seconds=.01))
        with bind_execution(gate):
            with self.assertRaisesRegex(ExecutionError, "task_deadline"):
                await self.registry(handler).handler(request(), {"query": "x"})
        self.assertFalse(gate._pending)
        self.assertEqual(gate.budget.counts[1], 1)

    async def test_provider_failure_cannot_be_hidden_by_original_fallback(self):
        runner = CandidateRunner(SafeOrchestrator(ControlledEmbedding()))
        with self.fixture.bind(PATH):
            async with self.client([httpx.ConnectError("secret upstream exception")]) as client:
                result = await runner.run(request(), self.gate(), self.settings, client)
        self.assertEqual(result.state, "failed")
        self.assertIsNone(result.result)
        self.assertEqual(len(self.sent), 1)

    async def test_model_usage_owner_or_conversation_mismatch_sends_nothing(self):
        for path, owner in ((PATH, "b" * 32), (PATH.replace("aaaaaaaa", "cccccccc"), SCOPE.owner_id)):
            runner, gate = CandidateRunner(SafeOrchestrator(ControlledEmbedding())), self.gate()
            with self.fixture.bind(path, owner_id=owner):
                async with self.client([]) as client:
                    result = await runner.run(request(), gate, self.settings, client)
            self.assertEqual((result.state, result.code, result.counts[0]), ("failed", "context_changed", 0))
        self.assertEqual(self.sent, [])

    async def test_input_invalidation_aborts_pending_model_no_late_result_or_next_send(self):
        started, stopped = asyncio.Event(), asyncio.Event()
        gate = self.gate()
        runner = CandidateRunner(SafeOrchestrator(ControlledEmbedding()))
        sent = []
        async def controlled(message):
            sent.append(message)
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        with self.fixture.bind(PATH):
            async with MeteredAsyncClient(transport=httpx.MockTransport(controlled), follow_redirects=False) as client:
                task = asyncio.create_task(runner.run(request(), gate, self.settings, client))
                await started.wait()
                self.current = replace(SCOPE, input_version=1)
                gate.invalidate()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertTrue(stopped.is_set())
        self.assertFalse(gate._pending)
        self.assertEqual((len(sent), gate.budget.counts[0]), (1, 1))

    async def test_model_budget_denial_is_explicit_no_fallback_success(self):
        handler = AsyncMock(return_value={"success": True})
        runner = CandidateRunner(SafeOrchestrator(ControlledEmbedding(), {"read_data": self.tool(handler)}))
        with self.fixture.bind(PATH):
            async with self.client([native(tool="read_data")]) as client:
                result = await runner.run(request(), self.gate(TaskBudget(model_limit=1)), self.settings, client)
        self.assertEqual((result.state, result.code, len(self.sent)), ("failed", "task_budget", 1))
        self.assertIsNone(result.result)

    async def test_intent_injection_retains_three_way_fusion_without_raw_constructor(self):
        recognizer = SafeIntentRecognizer(ControlledEmbedding())
        answer = native('{"intent":"technical_login","confidence":0.9,"reasoning":"controlled"}')
        gate = self.gate()
        with self.fixture.bind(PATH):
            async with self.client([answer]) as client:
                with bind_execution(gate), bind_provider(self.settings, client, gate.budget):
                    result = await recognizer.recognize("登录报401")
        self.assertEqual(result.intent, IntentCategory.TECHNICAL_LOGIN)
        self.assertEqual(set(result.source_scores), {"llm", "embedding", "pattern"})

    async def test_intent_cache_does_not_cross_session_and_learning_is_not_global(self):
        recognizer = SafeIntentRecognizer(ControlledEmbedding())
        answer = native('{"intent":"technical_login","confidence":0.9,"reasoning":"controlled"}')
        with self.fixture.bind(PATH):
            async with self.client([answer]) as client:
                gate = self.gate()
                with bind_execution(gate), bind_provider(self.settings, client, gate.budget):
                    await recognizer.recognize("登录报401")
                other = replace(SCOPE, session_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc")
                with bind_execution(ExecutionGate(other, lambda: other, TaskBudget())):
                    with self.assertRaisesRegex(ExecutionError, "context_changed"):
                        await recognizer.recognize("登录报401")
        with self.assertRaisesRegex(ExecutionError, "configuration"):
            recognizer.learn("private correction", IntentCategory.QUERY)
        self.assertEqual(len(self.sent), 1)

    async def test_safe_judge_one_attempt_invalid_output_unscored_no_repair(self):
        gate = self.gate()
        with self.fixture.bind(PATH):
            async with self.client([native('{"accuracy":"bad"}')]) as client:
                with bind_execution(gate), bind_provider(self.settings, client, gate.budget):
                    result = await SafeJudge().judge("controlled question", "controlled answer")
        self.assertTrue(result.judge_failed)
        self.assertIsNone(result.overall)
        self.assertEqual((result.attempts, len(self.sent)), (1, 1))
        self.assertEqual(self.sent[0]["max_tokens"], 1024)

    async def test_safe_judge_valid_scores_use_original_parser(self):
        gate = self.gate()
        answer = native('{"relevance":0.5,"accuracy":1,"completeness":0.5,"helpfulness":1}')
        with self.fixture.bind(PATH):
            async with self.client([answer]) as client:
                with bind_execution(gate), bind_provider(self.settings, client, gate.budget):
                    result = await SafeJudge().judge("question", "answer", "controlled reference")
        self.assertEqual(result.overall, .75)
        self.assertFalse(result.judge_failed)

    async def test_original_rewrite_parallel_recall_rerank_cache_and_circuit_preserved(self):
        manager = SafeToolManager()
        handler = AsyncMock(side_effect=lambda params, context: [{"content": params["query"], "document_id": params["query"]}])
        manager.register_read_only(Tool("knowledge_search", "controlled search", handler, {
            "type": "object", "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}},
            "required": ["query", "top_k"], "additionalProperties": False,
        }, cache_ttl=60))
        gate = self.gate()
        # Original rewrite adds the original query if absent: 4 logical recalls.
        with self.fixture.bind(PATH):
            async with self.client([native('["one","two"]'), native('[2,0,1]')]) as client:
                with bind_execution(gate), bind_provider(self.settings, client, gate.budget):
                    result = await manager.search_with_rewrite("knowledge_search", "original", top_k=1)
                    cached = await manager.call("knowledge_search", {"query": "one", "top_k": 5})
        self.assertTrue(result.success)
        self.assertTrue(result.reranked)
        self.assertTrue(cached.cached)
        self.assertEqual(handler.await_count, 3)
        self.assertEqual((len(self.sent), gate.budget.counts[1]), (2, 3))
        self.assertEqual(manager._tools["knowledge_search"].breaker.state, CircuitState.CLOSED)

    async def test_direct_manager_call_without_scope_or_forged_context_refused(self):
        manager = SafeToolManager()
        handler = AsyncMock(return_value=[])
        manager.register_read_only(Tool("read", "read", handler, {"type": "object", "properties": {}, "additionalProperties": False}))
        with self.assertRaisesRegex(ExecutionError, "provider_unbound"):
            await manager.call("read", {})
        with bind_execution(self.gate()):
            with self.assertRaisesRegex(ExecutionError, "context_changed"):
                await manager.call("read", {}, {"owner_id": "model-chosen"})
        handler.assert_not_awaited()

    async def test_manager_registration_authority_cannot_change_inside_task(self):
        manager = SafeToolManager()
        tool = Tool("read", "read", AsyncMock(return_value=[]), {"type": "object", "properties": {}, "additionalProperties": False})
        manager.register_read_only(tool)
        with bind_execution(self.gate()):
            with self.assertRaisesRegex(ExecutionError, "tool_registry"):
                manager.unregister("read")
            with self.assertRaisesRegex(ExecutionError, "tool_registry"):
                manager.register_read_only(replace(tool, name="other"))
        self.assertEqual(set(manager._tools), {"read"})

    async def test_original_circuit_breaker_stops_failed_handler_without_retry(self):
        manager = SafeToolManager()
        handler = AsyncMock(side_effect=ValueError("private raw error must not escape"))
        tool = Tool("read", "read", handler, {"type": "object", "properties": {}, "additionalProperties": False})
        tool.breaker = CircuitBreaker(failure_threshold=2, recovery_s=60)
        manager.register_read_only(tool)
        with bind_execution(self.gate()):
            first = await manager.call("read", {})
            second = await manager.call("read", {})
            stopped = await manager.call("read", {})
        self.assertEqual(handler.await_count, 2)
        self.assertFalse(first.success or second.success or stopped.success)
        self.assertNotIn("private", first.error)
        self.assertEqual(manager._tools["read"].breaker.state, CircuitState.OPEN)

    async def test_original_monitor_feedback_still_changes_safe_pool_routing(self):
        from prometheus_client import CollectorRegistry
        orchestrator, manager = SafeOrchestrator(ControlledEmbedding()), SafeToolManager()
        specialist = orchestrator._pool[AgentType.TECHNICAL][0]
        for _ in range(11):
            specialist.stats.total += 1
            specialist.stats.total_ms += 6000
            specialist.stats.record(False, 6000)
        monitor = PerformanceMonitor(orchestrator, manager, registry=CollectorRegistry())
        try:
            self.assertIs(orchestrator._best_agent(AgentType.TECHNICAL), specialist)
            await monitor._collect()
            self.assertIsNone(orchestrator._best_agent(AgentType.TECHNICAL))
            self.assertGreaterEqual(specialist.stats.monitor_penalty, .5)
            self.assertTrue(monitor.summary()["suggestions"])
        finally:
            await monitor.stop()

    async def test_runner_caches_cannot_cross_owner_or_session(self):
        runner = CandidateRunner(SafeOrchestrator(ControlledEmbedding()))
        with self.fixture.bind(PATH):
            async with self.client([native()]) as client:
                first = await runner.run(request(), self.gate(), self.settings, client)
                other = replace(SCOPE, owner_id="b" * 32)
                with self.assertRaisesRegex(ExecutionError, "context_changed"):
                    await runner.run(request(user_id=other.owner_id), ExecutionGate(other, lambda: other, TaskBudget()), self.settings, client)
        self.assertEqual(first.state, "complete")
        self.assertEqual(len(self.sent), 1)

    async def test_no_raw_candidate_logs_or_unknown_fields_leak(self):
        sink, unrelated = io.StringIO(), io.StringIO()
        root = logging.getLogger()
        handler = logging.StreamHandler(sink)
        root.addHandler(handler)
        prior = root.level
        root.setLevel(logging.INFO)
        self.addCleanup(root.removeHandler, handler)
        self.addCleanup(root.setLevel, prior)
        raw = logging.getLogger("echomind_core.upstream.agents.agent_orchestrator")
        quarantine_upstream_logging()
        raw.error("secret prompt %s", {"private-field": "private-value"})
        emit(SafeEvent("evil-stage-private", "secret-exception", count=-1, model="secret-key"))
        logging.getLogger("unrelated.application").warning("unrelated remains available")
        self.assertNotIn("secret", sink.getvalue())
        self.assertNotIn("private", sink.getvalue())
        self.assertIn('"code":"unknown"', sink.getvalue())
        self.assertIn("unrelated remains available", sink.getvalue())
        with self.assertRaisesRegex(TypeError, "safe_event_required"):
            emit({"private-field": "value"})


if __name__ == "__main__":
    unittest.main()

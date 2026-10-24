"""Actual preserved full pipeline on controlled native HTTP, never live quality."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import importlib.util
import json
import unittest
from unittest.mock import patch

import httpx

if any(importlib.util.find_spec(name) is None for name in ("jsonschema", "prometheus_client")):
    raise unittest.SkipTest("Explicit echomind extra required")

from core.config import Settings
from core.usage_runtime import MeteredAsyncClient
from echomind_core.embedding import DIMENSIONS, EMBEDDING_MODEL, QwenIntentEmbedding
from echomind_core.execution import ExecutionError, ExecutionGate, bind_execution
from echomind_core.provider import MODEL, MAX_INPUT_BYTES, bind_provider
from echomind_core.travel_bridge import TravelToolBridge
from echomind_core.travel_budget import TravelTaskBudget, TravelBudgetTier
from echomind_core.travel_domain import TravelIntent, TravelIntentRecognizer
from echomind_core.travel_full_engine import FullTravelCandidate, ReviewedRetrieval
from echomind_core.upstream.agents.agent_orchestrator import AgentOrchestrator, AgentType, ResponseComposer
from tests.test_echomind_travel_bridge import BINDING, SCOPE, capture, request
from tests.test_echomind_travel_engine import native_text, native_tools, FABRICATED, PATH
from tests.usage_fixture import UsageFixture


class FullTravelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = UsageFixture()
        self.addCleanup(self.fixture.close)
        self.settings = Settings(_env_file=None, qwen_api_key="controlled-not-real", qwen_model=MODEL,
            qwen_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.binding, self.scope, self.sent = BINDING, SCOPE, []
        snapshot, *_ = capture()
        self.bridge = TravelToolBridge(snapshot, lambda: self.binding, parallel_reads=True)
        self.embedding = QwenIntentEmbedding(SCOPE.owner_id, "travel-intent-index-v1")
        self.recognizer = TravelIntentRecognizer(self.embedding)

    def gate(self, tier=TravelBudgetTier.REFERENCE):
        return ExecutionGate(SCOPE, lambda: self.scope, TravelTaskBudget(tier))

    async def prepare(self):
        # Clearly synthetic vectors test the mechanics, NOT semantic quality.
        vectors = []
        for category, texts in self.recognizer.templates.items():
            for _ in texts:
                value = [0.0] * DIMENSIONS
                value[list(TravelIntent).index(category)] = 1.0
                vectors.append(value)
        gate = self.gate()
        with self.fixture.bind(PATH):
            async with MeteredAsyncClient(transport=httpx.MockTransport(lambda _: self.fail("unexpected send"))) as client:
                with bind_execution(gate), bind_provider(self.settings, client, gate.budget):
                    self.embedding.load_prepared(self.recognizer.templates, vectors,
                        fingerprint=self.embedding.template_fingerprint(self.recognizer.templates),
                        model=EMBEDDING_MODEL, dimensions=DIMENSIONS)

    async def run_full(self, *, message="核对当前费用", intent=TravelIntent.BUDGET,
                       candidate=None, tool=None, hook=None, fail=False):
        candidate = candidate or FullTravelCandidate(self.bridge, self.recognizer)
        async def controlled(incoming):
            payload = json.loads(incoming.content)
            self.sent.append((incoming.url.path, payload, len(incoming.content)))
            if hook:
                await hook(payload)
            if incoming.url.path.endswith("/embeddings"):
                vector = [0.0] * DIMENSIONS
                vector[list(TravelIntent).index(intent)] = 1.0
                return httpx.Response(200, json={"object": "list", "model": EMBEDDING_MODEL, "data": [
                    {"index": 0, "object": "embedding", "embedding": vector}], "usage": {"total_tokens": 4}})
            if "tools" not in payload:
                system = payload["messages"][0].get("content", "")
                if "阶段契约：classifier" in system:
                    response = native_text(json.dumps({"intent": intent.value, "confidence": .99, "reasoning": "受控分类"}))
                elif "阶段契约：rewrite" in system:
                    response = native_text('["儿童预约", "儿童陪同", "免费条件"]')
                elif "阶段契约：rerank" in system:
                    response = native_text("[1,0]")
                else:
                    response = native_text()
            else:
                names = {t["function"]["name"] for t in payload["tools"]}
                if tool is not None:
                    response = tool(payload)
                elif names == {"travel_schedule"}:
                    response = native_tools(("schedule_native", "travel_schedule",
                        {"action": "read_schedule", "date": None, "day": 1}))
                else:
                    response = native_tools(("budget_native", "travel_budget", {"action": "read_budget"}))
            if fail and "tools" in payload:
                response = native_tools(("illegal_native", "unknown_payment", {}))
            return httpx.Response(200, json=response)
        with self.fixture.bind(PATH):
            async with MeteredAsyncClient(transport=httpx.MockTransport(controlled)) as client:
                result = await candidate.run(request(message=message), self.gate(), self.settings, client)
        return result, candidate

    async def test_fusion_really_feeds_original_router_and_specialist_on_one_budget(self):
        await self.prepare()
        seen = []
        original = AgentOrchestrator._execute
        async def observed(engine, req, slot):
            seen.append((req.intent, slot))
            return await original(engine, req, slot)
        with patch.object(AgentOrchestrator, "_execute", observed):
            result, candidate = await self.run_full()
        self.assertEqual(result.state, "pending_browser")
        self.assertEqual(seen, [(TravelIntent.BUDGET, AgentType.BILLING)])
        self.assertEqual(result.counts[:2], (3, 1))  # classifier + query embedding + specialist
        self.assertEqual(len(self.sent), 3)
        self.assertEqual(result.versions["routing"]["primary_agent"], "travel_budget")
        self.assertEqual(result.versions["classification"]["source"], "original_fusion")
        self.assertNotIn(FABRICATED, result.message)
        self.assertFalse(result.projection["tool_results"][0]["data"]["executed"])
        self.assertTrue(all(size <= MAX_INPUT_BYTES for _, _, size in self.sent))
        self.assertTrue(all(len(agent.get_tools()) <= 16 for agents in candidate.orchestrator._pool.values() for agent in agents))

    async def test_original_parallel_and_composer_actually_execute_without_fact_fabrication(self):
        await self.prepare()
        called = []
        parallel, compose = AgentOrchestrator.run_parallel, ResponseComposer.compose_result
        async def spy_parallel(engine, req, decision):
            called.append("parallel")
            return await parallel(engine, req, decision)
        async def spy_compose(composer, req, responses):
            called.append("composer")
            return await compose(composer, req, responses)
        with patch.object(AgentOrchestrator, "run_parallel", spy_parallel), \
                patch.object(ResponseComposer, "compose_result", spy_compose):
            result, candidate = await self.run_full(message="查看当前行程和费用", intent=TravelIntent.SCHEDULE)
        self.assertEqual(called, ["parallel", "composer"])
        self.assertEqual(result.state, "pending_browser")
        self.assertEqual(result.counts[:2], (5, 2))
        self.assertEqual({row["name"] for row in result.projection["tool_results"]}, {"travel_schedule", "travel_budget"})
        self.assertTrue(all(row["data"]["executed"] is False for row in result.projection["tool_results"]))
        self.assertNotIn(FABRICATED, result.message)
        self.assertTrue(any("阶段契约：composer" in row[1]["messages"][0]["content"]
                            for row in self.sent if "messages" in row[1]))
        composer_payload = next(row[1] for row in self.sent if "messages" in row[1]
            and "阶段契约：composer" in row[1]["messages"][0]["content"])
        self.assertIn("尚未执行", composer_payload["messages"][-1]["content"])
        self.assertNotIn(FABRICATED, str(composer_payload))
        self.assertNotIn("本轮交还用户或浏览器", str(composer_payload))
        self.assertFalse(result.versions["composer_fact_authority"])
        # Specialist registries are restored after the narrow dispatch.
        self.assertGreater(len(candidate.orchestrator._pool[AgentType.TECHNICAL][0].get_tools()), 1)

    async def test_no_cold_index_auto_preparation_or_hidden_fallback(self):
        result, _ = await self.run_full()
        self.assertEqual(result.code, "embedding_preparation_required")
        self.assertEqual(result.counts, (0, 0, 0))
        self.assertEqual(self.sent, [])

    async def test_skill_context_rejects_bad_input_before_any_paid_branch_and_accepts_runner_bound(self):
        await self.prepare()
        result, _ = await self.run_full(message="a" * 4000)
        self.assertEqual(result.state, "pending_browser")
        self.assertEqual(len(self.sent), 3)
        snapshot, *_ = capture()
        bridge = TravelToolBridge(snapshot, lambda: self.binding, parallel_reads=True)
        recognizer = TravelIntentRecognizer(self.embedding)
        candidate = FullTravelCandidate(bridge, recognizer)
        before = len(self.sent)
        failed, _ = await self.run_full(candidate=candidate, message="unsafe\x00")
        self.assertEqual(failed.code, "travel_package_input")
        self.assertEqual(len(self.sent), before)

    async def test_caller_cannot_inject_classification_or_context(self):
        candidate = FullTravelCandidate(self.bridge, self.recognizer)
        with self.fixture.bind(PATH):
            async with MeteredAsyncClient(transport=httpx.MockTransport(lambda _: self.fail("send"))) as client:
                result = await candidate.run(request(intent=TravelIntent.BUDGET, intent_group="budget"),
                    self.gate(), self.settings, client)
        self.assertEqual(result.code, "travel_classification_required")
        self.assertEqual(result.counts[0], 0)

    async def test_stale_success_rejected_and_operation_cannot_be_reused(self):
        await self.prepare()
        async def change(payload):
            if "tools" in payload:
                self.binding = replace(BINDING, place_revision=4)
        result, candidate = await self.run_full(hook=change)
        self.assertEqual(result.state, "failed")
        self.assertIsNone(result.projection)
        with self.assertRaisesRegex(ExecutionError, "travel_context_changed"):
            await self.run_full(candidate=candidate)
        self.assertEqual(len(self.sent), 3)

    async def test_context_change_during_final_projection_await_cannot_publish(self):
        await self.prepare()
        original = self.bridge.project
        count = 0
        async def changed():
            nonlocal count
            value = await original()
            count += 1
            # First call supplies program content to the Agent. The final
            # publication call must ALSO check scope after this await.
            if count == 2:
                self.scope = replace(SCOPE, input_version=SCOPE.input_version + 2)
            return value
        with patch.object(self.bridge, "project", changed):
            result, _ = await self.run_full()
        self.assertEqual(result.code, "travel_context_changed")
        self.assertIsNone(result.projection)
        self.assertEqual(result.counts[:2], (3, 1))

    async def test_bad_native_tool_fails_and_free_prose_cannot_be_business_answer(self):
        await self.prepare()
        result, _ = await self.run_full(fail=True)
        self.assertEqual(result.state, "failed")
        self.assertIsNone(result.projection)
        self.assertNotIn(FABRICATED, result.message)
        self.assertEqual(len(self.sent), 3)

    async def test_bridge_legacy_still_rejects_multiple_proposals_and_full_only_allows_read_pair(self):
        from echomind_core.execution import GuardedToolRegistry, ToolDeclaration
        for parallel, second, expected in ((False, "read_budget", False), (True, "read_budget", True),
                                            (True, "open_budget", False)):
            snapshot, *_ = capture()
            bridge = TravelToolBridge(snapshot, lambda: BINDING, parallel_reads=parallel)
            gate = self.gate()
            with bind_execution(gate):
                async def invoke(name, args):
                    spec = bridge.tools()[name]
                    return await GuardedToolRegistry((ToolDeclaration(spec, True),)).wrap(spec).handler(request(), args)
                await invoke("travel_schedule", {"action": "read_schedule", "date": None, "day": 1})
                if expected:
                    await invoke("travel_budget", {"action": second})
                    self.assertEqual(len(bridge._rows), 2)
                else:
                    with self.assertRaises(Exception):
                        await invoke("travel_budget", {"action": second})
                    self.assertEqual(len(bridge._rows), 1)

    async def test_original_full_retrieval_really_rewrites_parallel_recalls_dedups_and_reranks(self):
        from copy import deepcopy
        from echomind_core.travel_capabilities import ReviewedKnowledgePort
        from echomind_core.travel_package import TravelDomainPackage
        from echomind_core.execution import GuardedToolRegistry, ToolDeclaration
        from echomind_core.upstream.tooling.tool_manager import MCPToolManager
        from tooling.travel.knowledge_national import NationalKnowledgeIndex
        from tests.test_knowledge_national import corpus_values, reviewed
        raw = corpus_values()
        for index in range(4):
            doc = deepcopy(raw["documents"][0])
            doc["id"] = f"sh-extra-{index}"
            doc["url"] = f"https://museum.example.test/extra-{index}"
            doc["sections"][0]["summary"] += f"受控补充主题{index}。"
            raw["documents"].append(doc)
        port = ReviewedKnowledgePort(NationalKnowledgeIndex(*reviewed(raw)))
        retrieval = ReviewedRetrieval(self.bridge, port, "310000", "shanghai-museum",
                                      package=TravelDomainPackage().capture())
        invoked = []
        originals = {name: getattr(MCPToolManager, name) for name in
                     ("search_with_rewrite", "rewrite_query", "_rerank_with_status")}
        async def search(manager, *args, **kwargs):
            invoked.append("search")
            return await originals["search_with_rewrite"](manager, *args, **kwargs)
        async def rewrite(manager, *args, **kwargs):
            invoked.append("rewrite")
            return await originals["rewrite_query"](manager, *args, **kwargs)
        async def rank(manager, *args, **kwargs):
            invoked.append("rank")
            return await originals["_rerank_with_status"](manager, *args, **kwargs)
        sent = []
        async def controlled(incoming):
            payload = json.loads(incoming.content)
            sent.append(payload)
            stage = payload["messages"][0]["content"]
            return httpx.Response(200, json=native_text('["儿童预约", "儿童陪同", "免费条件"]'
                if "阶段契约：rewrite" in stage else "[1,0,2,3,4]"))
        spec, gate = retrieval.spec(), self.gate()
        with self.fixture.bind(PATH):
            async with MeteredAsyncClient(transport=httpx.MockTransport(controlled)) as client:
                with bind_execution(gate), bind_provider(self.settings, client, gate.budget), \
                        patch.object(MCPToolManager, "search_with_rewrite", search), \
                        patch.object(MCPToolManager, "rewrite_query", rewrite), \
                        patch.object(MCPToolManager, "_rerank_with_status", rank):
                    await GuardedToolRegistry((ToolDeclaration(spec, True),)).wrap(spec).handler(
                        request(), {"query": "儿童免票陪同", "top_k": 1})
        self.assertEqual(invoked, ["search", "rewrite", "rank"])
        self.assertEqual(len(sent), 2)
        self.assertEqual(gate.budget.counts[:2], (2, 5))
        row = (await self.bridge.project()).tool_results[0]["data"]
        self.assertTrue(row["reranked"])
        self.assertTrue(row["retrieval_only"])
        self.assertEqual(len(row["items"]), 1)
        self.assertIn("未满6周岁免票，但不含另行收费项目", row["message"])
        self.assertIn("官方来源：https://museum.example.test/", row["message"])
        self.assertEqual(row["corpus_hash"], port.corpus_hash)

    async def test_native_send_cancel_counts_and_one_shot_stays_closed(self):
        await self.prepare()
        started = asyncio.Event()
        async def held(payload):
            if "tools" in payload:
                started.set()
                await asyncio.Future()
        candidate = FullTravelCandidate(self.bridge, self.recognizer)
        task = asyncio.create_task(self.run_full(candidate=candidate, hook=held))
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(self.sent), 3)
        with self.assertRaisesRegex(ExecutionError, "travel_context_changed"):
            await self.run_full(candidate=candidate)

    async def test_parallel_coordinator_fallback_cannot_obtain_mutating_tools(self):
        await self.prepare()
        engine = FullTravelCandidate(self.bridge, self.recognizer)
        seen = []
        original = AgentOrchestrator._execute
        async def inspect_fallback(runtime, req, slot):
            coordinator = runtime._pool[AgentType.GENERAL][0]
            tools = coordinator.get_tools()
            seen.append({name: spec.input_schema["properties"]["action"]["enum"] for name, spec in tools.items()})
            return await original(runtime, req, slot)
        with patch.object(AgentOrchestrator, "_execute", inspect_fallback):
            result, _ = await self.run_full(candidate=engine, message="查看当前行程和费用", intent=TravelIntent.SCHEDULE)
        self.assertEqual(result.state, "pending_browser")
        self.assertEqual(seen, [{"travel_schedule": ["read_schedule"], "travel_budget": ["read_budget"]}] * 2)


if __name__ == "__main__":
    unittest.main()

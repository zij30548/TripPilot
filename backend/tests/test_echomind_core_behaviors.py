"""Controlled cross-module checks for the imported EchoMind core.

These exercise the imported algorithms, not the active travel application.  All
model responses and tool data are injected; no service, user database, embedding
download or paid endpoint is contacted.  The upstream unit suite is retained
separately, so this file concentrates on boundaries between its modules.
"""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import tempfile
import unittest
from collections import deque
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx

_missing_dependencies = [
    name for name in ("jsonschema", "prometheus_client")
    if importlib.util.find_spec(name) is None
]
if _missing_dependencies:
    raise unittest.SkipTest(
        "EchoMind candidate checks require the opt-in echomind extra; missing: "
        + ", ".join(_missing_dependencies)
    )

from core.config import Settings
from core.usage_runtime import MeteredAsyncClient
from echomind_core.provider import MODEL, QwenMessagesClient, TaskBudget, bind_provider
from echomind_core.upstream.agents.agent_orchestrator import (
    AgentOrchestrator,
    AgentResponse,
    AgentType,
    BillingAgent,
    GeneralAgent,
    Request,
    ResponseComposer,
    RoutingDecision,
    TechnicalAgent,
)
from echomind_core.upstream.core import intent_recognizer as intent_module
from echomind_core.upstream.core.intent_embedding import IntentEmbedding
from echomind_core.upstream.core.intent_recognizer import (
    IntentCategory,
    IntentRecognizer,
    UrgencyLevel,
)
from echomind_core.upstream.core.skill_loader import SkillManager
from echomind_core.upstream.evaluation import evaluator as evaluator_module
from echomind_core.upstream.evaluation.evaluator import EndToEndEvaluator, LLMJudge
from echomind_core.upstream.monitor.performance_monitor import PerformanceMonitor
from echomind_core.upstream.tooling.tool_manager import (
    CircuitBreaker,
    CircuitState,
    MCPToolManager,
    Tool,
)
from tests.usage_fixture import UsageFixture


def text_response(text: str, stop: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(content=[{"type": "text", "text": text}], stop_reason=stop)


def controlled_client(*responses: Any) -> SimpleNamespace:
    return SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(side_effect=responses)))


def recognizer(client: Any) -> IntentRecognizer:
    """Initialize only algorithm state; never construct a real provider client."""
    result = IntentRecognizer.__new__(IntentRecognizer)
    result.client, result.model, result.threshold = client, "controlled-model", 0.5
    with patch.dict("os.environ", {"INTENT_EMBEDDING_PROVIDER": "local"}):
        result._embedding = IntentEmbedding()
    result._embedding_enabled = True
    result._cache, result._cache_expiries = {}, {}
    result.cache_hits = result.cache_misses = 0
    return result


def tool_manager(client: Any) -> MCPToolManager:
    result = MCPToolManager.__new__(MCPToolManager)
    result._client, result._model = client, "controlled-model"
    result._tools, result._cache_versions, result._cache = {}, {}, {}
    return result


def request(**changes: Any) -> Request:
    values = dict(
        message="登录时报401，同时核对两笔扣款",
        user_id="controlled-owner",
        conv_id="controlled-conversation",
        intent=IntentCategory.TECHNICAL_LOGIN,
        intent_group="technical",
        urgency=UrgencyLevel.LOW,
        entities={"error_code": ["401"]},
    )
    values.update(changes)
    return Request(**values)


def orchestrator(pool: dict[AgentType, list[Any]], composer_client: Any) -> AgentOrchestrator:
    result = AgentOrchestrator.__new__(AgentOrchestrator)
    result._pool = pool
    result._composer = ResponseComposer(composer_client, "controlled-model")
    result._recent_tool_traces = deque(maxlen=20)
    return result


class ImportedCoreBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def test_original_agent_roundtrip_runs_through_native_qwen_bridge_and_real_temp_counter(self) -> None:
        """Prove the real imported loop accepts facade blocks, not just mocks."""
        fixture = UsageFixture()
        self.addCleanup(fixture.close)
        settings = Settings(_env_file=None, qwen_api_key="controlled-not-a-real-key",
                            qwen_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", qwen_model=MODEL)
        budget, wire = TaskBudget(), []

        def upstream(message: httpx.Request) -> httpx.Response:
            wire.append(json.loads(message.content))
            if len(wire) == 1:
                choice = {"finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": None, "tool_calls": [{
                        "id": "controlled_call", "type": "function", "function": {
                            "name": "lookup_error_code", "arguments": '{"error_code":"401"}',
                        },
                    }],
                }}
            else:
                choice = {"finish_reason": "stop", "message": {"role": "assistant", "content": "受控诊断说明"}}
            return httpx.Response(200, json={"id": "controlled_response", "model": MODEL,
                                          "choices": [choice], "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

        agent = TechnicalAgent(QwenMessagesClient(), MODEL)
        native_tools = agent.get_tools()
        spec = native_tools["lookup_error_code"]

        async def bounded_tool(req: Request, args: dict[str, Any]) -> Any:
            # The imported core itself does not yet reserve task tool budgets;
            # a trusted integration wrapper must do so before dispatch.
            await budget.reserve_tool()
            return spec.handler(req, args)

        native_tools[spec.name] = replace(spec, handler=bounded_tool)
        with fixture.bind("/chat/conversations/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/assistant-turns"):
            async with MeteredAsyncClient(transport=httpx.MockTransport(upstream), follow_redirects=False) as client:
                with bind_provider(settings, client, budget), patch.object(agent, "get_tools", return_value=native_tools):
                    result = await agent.handle(request())
        self.assertTrue(result.success)
        self.assertEqual(result.content, "受控诊断说明")
        self.assertEqual(result.tools_used, ["lookup_error_code"])
        self.assertEqual(budget.counts, (2, 1, 2400))
        self.assertEqual(len(wire), 2)
        self.assertEqual(wire[1]["messages"][-1]["role"], "tool")
        self.assertEqual(wire[1]["messages"][-1]["tool_call_id"], "controlled_call")
        tool_result = json.loads(wire[1]["messages"][-1]["content"])
        self.assertEqual(tool_result["meaning"], "认证失败")
        self.assertFalse(tool_result["server_log_checked"])

    async def test_fusion_uses_three_sources_and_learn_invalidates_all_caches(self) -> None:
        client = controlled_client(text_response(json.dumps({
            "intent": "refund", "confidence": 0.9, "reasoning": "controlled classification",
        })))
        selected = recognizer(client)
        templates = copy.deepcopy(intent_module._TEMPLATES)
        with patch.object(intent_module, "_TEMPLATES", templates):
            result = await selected.recognize("我要申请退款")
            self.assertIs(result.intent, IntentCategory.REFUND)
            self.assertEqual(set(result.source_scores), {"llm", "embedding", "pattern"})
            self.assertEqual(result.embedding_source, "local-ngram")
            self.assertGreater(result.source_scores["embedding"], 0)
            self.assertGreater(result.source_scores["pattern"], 0)
            self.assertEqual(result.source_scores["llm"], 0.9)
            self.assertIs(await selected.recognize("我要申请退款"), result)
            self.assertEqual(client.messages.create.await_count, 1)
            self.assertEqual(selected.cache_hits, 1)
            self.assertTrue(selected._embedding.local_templates)
            selected._embedding.remote_templates["controlled-cache"] = {}
            selected.learn("一个新的纠正样本", IntentCategory.REFUND)
            self.assertIn("一个新的纠正样本", templates[IntentCategory.REFUND])
            self.assertFalse(selected._cache)
            self.assertFalse(selected._cache_expiries)
            self.assertFalse(selected._embedding.local_templates)
            self.assertFalse(selected._embedding.remote_templates)
            selected.learn("一个新的纠正样本", IntentCategory.REFUND)
            self.assertEqual(templates[IntentCategory.REFUND].count("一个新的纠正样本"), 1)

    async def test_invalid_llm_confidence_falls_back_to_local_source_not_default_success(self) -> None:
        client = controlled_client(text_response('{"intent":"refund","confidence":true}'))
        selected = recognizer(client)
        result = await selected.recognize("我要申请退款")
        self.assertIs(result.intent, IntentCategory.REFUND)
        self.assertEqual(result.source_scores["llm"], 0)
        self.assertEqual(result.reasoning, "LLM 失败")
        self.assertEqual(result.embedding_source, "local-ngram")
        self.assertEqual(client.messages.create.await_count, 1)

    async def test_parallel_agents_execute_native_tools_then_compose_in_primary_order(self) -> None:
        started: set[str] = set()
        both_started = asyncio.Event()

        def agent_client(name: str, tool: str, arguments: dict[str, Any]) -> SimpleNamespace:
            async def create(**kwargs: Any) -> SimpleNamespace:
                if len(calls) == 0:
                    calls.append(kwargs)
                    started.add(name)
                    if len(started) == 2:
                        both_started.set()
                    await asyncio.wait_for(both_started.wait(), timeout=1)
                    return SimpleNamespace(content=[{
                        "type": "tool_use", "id": f"controlled-{name}",
                        "name": tool, "input": arguments,
                    }], stop_reason="tool_use")
                calls.append(kwargs)
                return text_response(f"{name} controlled evidence")
            calls: list[dict[str, Any]] = []
            return SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(side_effect=create)), calls=calls)

        technical_client = agent_client("technical", "lookup_error_code", {"error_code": "401"})
        billing_client = agent_client("billing", "compare_amounts", {"amount_a": 12, "amount_b": 10})
        composer_client = controlled_client(text_response("controlled combined reply"))
        runtime = orchestrator({
            AgentType.TECHNICAL: [TechnicalAgent(technical_client, "controlled-model")],
            AgentType.BILLING: [BillingAgent(billing_client, "controlled-model")],
        }, composer_client)
        result = await runtime.run_parallel(request(), RoutingDecision(
            AgentType.TECHNICAL, [AgentType.BILLING], "controlled collaboration", 0.9,
        ))
        self.assertEqual(started, {"technical", "billing"})
        self.assertEqual(result.status, "success")
        self.assertEqual(result.response, "controlled combined reply")
        self.assertEqual(result.tools_used, ["lookup_error_code", "compare_amounts"])
        self.assertEqual([t["agent_type"] for t in result.tool_traces], ["technical", "billing"])
        for client, tool in ((technical_client, "lookup_error_code"), (billing_client, "compare_amounts")):
            self.assertEqual(client.messages.create.await_count, 2)
            feedback = client.calls[1]["messages"][-1]["content"][0]
            self.assertEqual(feedback["type"], "tool_result")
            self.assertFalse(feedback["is_error"])
            self.assertIn(tool, [spec["name"] for spec in client.calls[0]["tools"]])
        prompt = composer_client.messages.create.call_args.kwargs["messages"][0]["content"]
        self.assertLess(prompt.index("technical controlled evidence"), prompt.index("billing controlled evidence"))
        self.assertEqual(runtime.get_tool_trace(result.request_id)["tools_used"], result.tools_used)

    async def test_agent_fallback_preserves_failure_trace_and_stops_after_unsafe_attempt(self) -> None:
        technical = TechnicalAgent(controlled_client(), "controlled-model")
        general = GeneralAgent(controlled_client(), "controlled-model")
        technical.handle = AsyncMock(return_value=AgentResponse(
            AgentType.TECHNICAL, "controlled failure", False, status="failed",
            tool_traces=[{"tool_name": "controlled_lookup", "success": False}],
        ))
        general.handle = AsyncMock(return_value=AgentResponse(AgentType.GENERAL, "controlled fallback", True))
        runtime = orchestrator({AgentType.TECHNICAL: [technical], AgentType.GENERAL: [general]}, controlled_client())
        result = await runtime._execute(request(), AgentType.TECHNICAL)
        self.assertTrue(result.success)
        self.assertEqual(result.tool_traces[0]["tool_name"], "controlled_lookup")
        self.assertEqual(result.tool_traces[1]["event"], "agent_fallback")
        general.handle.reset_mock()
        req = request()
        req.tool_attempts.append({"tool_name": "controlled_write", "retry_safe": False})
        result = await runtime._execute(req, AgentType.TECHNICAL)
        self.assertFalse(result.success)
        general.handle.assert_not_awaited()

    async def test_rewrite_parallel_recall_dedup_and_rerank_form_one_chain(self) -> None:
        client = controlled_client(text_response('["退款政策","退款时效","退款流程"]'), text_response('[3,0]'))
        manager = tool_manager(client)
        started: list[str] = []
        all_started = asyncio.Event()

        async def handler(params: dict[str, Any], context: dict[str, Any]) -> list[dict[str, str]]:
            started.append(params["query"])
            if len(started) == 3:
                all_started.set()
            await asyncio.wait_for(all_started.wait(), timeout=1)
            return [
                {"document_id": "common", "title": "共同依据", "content": "共同规则"},
                {"document_id": params["query"], "title": params["query"], "content": params["query"]},
            ]

        manager.register(Tool("controlled_search", "", handler, {
            "type": "object", "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}},
            "required": ["query", "top_k"], "additionalProperties": False,
        }, cache_ttl=30))
        result = await manager.search_with_rewrite("controlled_search", "退款流程", 2, {"user_id": "a"})
        self.assertTrue(result.success)
        self.assertTrue(result.reranked)
        self.assertEqual(started, ["退款流程", "退款政策", "退款时效"])
        self.assertEqual([row["document_id"] for row in result.data], ["退款时效", "common"])
        self.assertEqual(client.messages.create.await_count, 2)
        # Cache belongs to the explicit context, not merely the query text.
        cached = await manager.call("controlled_search", {"query": "退款流程", "top_k": 5}, {"user_id": "a"})
        self.assertTrue(cached.cached)
        cached.data[0]["title"] = "caller mutation"
        again = await manager.call("controlled_search", {"query": "退款流程", "top_k": 5}, {"user_id": "a"})
        self.assertEqual(again.data[0]["title"], "共同依据")
        fresh_owner = await manager.call("controlled_search", {"query": "退款流程", "top_k": 5}, {"user_id": "b"})
        self.assertFalse(fresh_owner.cached)

    async def test_tool_circuit_stops_dispatch_and_schema_errors_do_not_consume_health(self) -> None:
        manager = tool_manager(controlled_client())
        handler = AsyncMock(side_effect=RuntimeError("controlled upstream failure"))
        tool = Tool("controlled_lookup", "", handler, {
            "type": "object", "properties": {"id": {"type": "string"}},
            "required": ["id"], "additionalProperties": False,
        })
        tool.breaker = CircuitBreaker(failure_threshold=2, recovery_s=60)
        manager.register(tool)
        for _ in range(3):
            self.assertFalse((await manager.call(tool.name, {})).success)
        handler.assert_not_awaited()
        self.assertEqual(tool.breaker.state, CircuitState.CLOSED)
        for _ in range(3):
            self.assertFalse((await manager.call(tool.name, {"id": "controlled"})).success)
        self.assertEqual(handler.await_count, 2)
        self.assertEqual(tool.breaker.state, CircuitState.OPEN)
        self.assertEqual(tool.stats.failed, 2)

    async def test_monitor_penalty_changes_actual_specialist_selection(self) -> None:
        from prometheus_client import CollectorRegistry

        technical = TechnicalAgent(controlled_client(), "controlled-model")
        general = GeneralAgent(controlled_client(), "controlled-model")
        runtime = orchestrator({AgentType.TECHNICAL: [technical], AgentType.GENERAL: [general]}, controlled_client())
        # The raw monitor applies penalties at five recent samples and emits
        # routing suggestions only after more than ten cumulative samples.
        for _ in range(11):
            technical.stats.total += 1
            technical.stats.record(False, 6000)
            technical.stats.total_ms += 6000
        monitor = PerformanceMonitor(runtime, tool_manager(controlled_client()), registry=CollectorRegistry())
        try:
            self.assertIs(runtime._best_agent(AgentType.TECHNICAL), technical)
            await monitor._collect()
            self.assertGreaterEqual(technical.stats.monitor_penalty, 0.5)
            self.assertIsNone(runtime._best_agent(AgentType.TECHNICAL))
            general.handle = AsyncMock(return_value=AgentResponse(AgentType.GENERAL, "controlled fallback", True))
            self.assertTrue((await runtime._execute(request(), AgentType.TECHNICAL)).success)
            general.handle.assert_awaited_once()
            self.assertTrue(monitor.summary()["suggestions"])
        finally:
            await monitor.stop()

    async def test_skill_reload_changes_same_agent_prompt_without_reconstruction(self) -> None:
        with tempfile.TemporaryDirectory(prefix="echomind-controlled-skill-") as root:
            path = Path(root, "SKILL.md")
            path.write_text("---\nname: controlled\nagents: general\nkeywords: 退款\n---\n# controlled\n旧规则", encoding="utf-8")
            manager = SkillManager(root)
            manager.load()
            agent = GeneralAgent(controlled_client(), "controlled-model", manager)
            req = request(message="退款事项")
            self.assertIn("旧规则", agent._build_system_prompt(req))
            path.write_text(path.read_text(encoding="utf-8").replace("旧规则", "新规则"), encoding="utf-8")
            manager.reload()
            self.assertIn("新规则", agent._build_system_prompt(req))
            self.assertNotIn("旧规则", agent._build_system_prompt(req))
            self.assertNotIn("新规则", agent._build_system_prompt(request(message="其他事项")))

    async def test_judge_invalid_output_remains_unscored_and_is_retained_in_report(self) -> None:
        valid = {"relevance": 0.9, "accuracy": 0.8, "completeness": 0.7, "helpfulness": 0.8}
        client = controlled_client(text_response(json.dumps(valid)), text_response("{}"), text_response("not-json"))
        judge = LLMJudge(client, "controlled-judge")
        runtime = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(
            response="controlled answer", agent_type=AgentType.GENERAL, intent=None,
        )))
        evaluator = EndToEndEvaluator.__new__(EndToEndEvaluator)
        evaluator._orchestrator, evaluator._judge = runtime, judge
        evaluator._run_lock = asyncio.Lock()
        evaluator._history, evaluator._baseline, evaluator._baseline_path = [], None, None
        # Upstream Judge has a repair retry. Preserve and expose that behavior;
        # these are controlled calls, not a grant to run it in the live product.
        with patch.object(evaluator_module.asyncio, "sleep", new=AsyncMock()):
            report = await evaluator.run(dialog_cases=[{"question": "controlled first"}, {"question": "controlled second"}])
        self.assertEqual(report.total, 2)
        self.assertEqual(report.valid_total, 1)
        self.assertEqual(report.evaluation_errors, 1)
        self.assertEqual(report.status, "partial")
        self.assertFalse(report.results[1].passed)
        self.assertEqual(report.results[1].scores, {})
        self.assertEqual(report.results[1].metadata["judge_attempts"], 2)
        self.assertEqual(report.results[1].metadata["judge_error_type"], "invalid_json")
        self.assertEqual(client.messages.create.await_count, 3)
        self.assertEqual(report.avg_scores, valid)
        for bad in (True, -0.1, 2, "NaN", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                LLMJudge._parse_scores(json.dumps({**valid, "accuracy": bad}))


if __name__ == "__main__":
    unittest.main()

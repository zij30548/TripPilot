"""Controlled tests; no real model, AMap, storage or app startup."""
from __future__ import annotations

import asyncio
import json
import unittest

import httpx

from agents.agent_orchestrator import AgentOrchestrator, AgentRequest
from agents.tools import ToolSpec
from core.execution import ExecutionBudget, ExecutionState
from core.intent_recognizer import IntentRecognizer
from core.llm_utils import NativeToolCall, ProviderResponse, RuntimeFailure, parse_native_response, validate_arguments
from core.qwen_gateway import QwenGateway
from core.skill_loader import SkillManager
from evaluation.agent_evaluator import ExpectedOutcome, evaluate_outcome
from core.config import Settings
from core.business_usage import UsageError
from core.usage_runtime import MeteredAsyncClient
from tests.usage_fixture import UsageFixture


def request(text: str = "搜索一个地点") -> AgentRequest:
    return AgentRequest(text, "owner-a", "conversation-a", "request-a", {"city": "上海"})


def native(name: str = "search_places", args: dict | None = None, identifier: str = "call_1") -> ProviderResponse:
    return ProviderResponse(tool_call=NativeToolCall(identifier, name, args if args is not None else {"keyword": "博物馆"}), output_tokens=20)


class FakeProvider:
    def __init__(self, answers: list[ProviderResponse]) -> None:
        self.answers = list(answers)
        self.calls: list[dict] = []

    async def complete(self, messages, tools, *, max_tokens):
        self.calls.append({"messages": list(messages), "tools": list(tools), "max_tokens": max_tokens})
        return self.answers.pop(0)


SCHEMA = {"type": "object", "properties": {"keyword": {"type": "string", "minLength": 1, "maxLength": 40}},
          "required": ["keyword"], "additionalProperties": False}


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_program_only_builds_current_capabilities_without_legacy_skill_execution_advice(self):
        from unittest.mock import Mock
        manager = Mock(spec=SkillManager)
        manager.prompt_for.return_value = "LEGACY: 先调用 get_budget_summary 再读取工具结果继续。"
        selected = []
        async def propose(args):
            selected.append(args)
            return {"message": "请确认本次核对费用。", "dialogue_action": {"name": "read_budget", "arguments": {}}}
        async def hidden(_):
            self.fail("wrong role cannot execute hidden capability")
        schema = {"type": "object", "properties": {"action": {"type": "string", "enum": ["read_budget"]}},
                  "required": ["action"], "additionalProperties": False}
        provider = FakeProvider([native("travel_budget", {"action": "read_budget"})])
        result = await AgentOrchestrator(provider, [
            ToolSpec("travel_budget", "提议核对本地费用", schema, propose, terminal=True, allowed_roles=("budget",)),
            ToolSpec("hidden_place_tool", "其他角色工具", SCHEMA, hidden, allowed_roles=("places",)),
        ], skill_manager=manager, program_only=True).run(request("当前预算是多少"))
        # Actual request construction + handler execution, not an assertion
        # that adding words to a prompt makes model semantics correct.
        sent = provider.calls[0]
        self.assertEqual([tool["function"]["name"] for tool in sent["tools"]], ["travel_budget"])
        self.assertEqual(selected, [{"action": "read_budget"}])
        self.assertEqual(result.tool_results[0]["name"], "travel_budget")
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(result.reply, "请确认本次核对费用。")
        manager.prompt_for.assert_not_called()
        self.assertNotIn("get_budget_summary", sent["messages"][0]["content"])
        self.assertNotIn("hidden_place_tool", sent["messages"][0]["content"])
        self.assertIn("本次实际可调用函数：travel_budget", sent["messages"][0]["content"])

    async def test_legacy_skill_and_feedback_request_remain_unchanged(self):
        from unittest.mock import Mock
        from agents.agent_orchestrator import SYSTEM
        manager = Mock(spec=SkillManager)
        manager.prompt_for.return_value = "旧界面可信规则：调用 search_places 后核对工具结果。"
        executed = []
        async def search(arguments):
            executed.append(arguments)
            return {"candidate_count": 1, "confirmation_required": True}
        provider = FakeProvider([native(), ProviderResponse(content="请核对候选。", output_tokens=8)])
        result = await AgentOrchestrator(provider, [ToolSpec("search_places", "搜索", SCHEMA, search)],
                                        skill_manager=manager).run(request())
        manager.prompt_for.assert_called_once_with("搜索一个地点", "places")
        self.assertEqual(provider.calls[0]["messages"][0]["content"],
                         f"{SYSTEM}\n当前角色：places\n{manager.prompt_for.return_value}")
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(executed, [{"keyword": "博物馆"}])
        self.assertEqual(provider.calls[1]["messages"][-1]["role"], "tool")
        self.assertEqual(result.reply, "请核对候选。")

    async def test_native_tool_result_is_returned_to_model_and_displayed_separately(self):
        calls = []
        async def search(args):
            calls.append(args)
            return {"places": [{"id": "poi1", "name": "真实候选"}], "confirmation_required": True}
        provider = FakeProvider([native(), ProviderResponse(content="请核对候选地址后确认。", output_tokens=15)])
        outcome = await AgentOrchestrator(provider, [ToolSpec("search_places", "搜索候选", SCHEMA, search)]).run(request())
        self.assertEqual(calls, [{"keyword": "博物馆"}])
        self.assertEqual(provider.calls[1]["messages"][-1]["role"], "tool")
        self.assertEqual(provider.calls[1]["messages"][-1]["tool_call_id"], "call_1")
        self.assertTrue(outcome.tool_results[0]["data"]["confirmation_required"])
        self.assertEqual(outcome.trace["model_calls"], 2)
        self.assertEqual(outcome.trace["reply_source"], "model_suggestion")
        self.assertTrue(evaluate_outcome(outcome, ExpectedOutcome(("search_places",)))["passed"])
        self.assertNotIn("博物馆", str(outcome.trace))
        self.assertNotIn("真实候选", str(outcome.trace))

    async def test_terminal_clarification_stops_without_summary_call(self):
        async def clarify(args):
            return {"message": "请明确目的地。", "reason": args["reason"]}
        schema = {"type": "object", "properties": {"reason": {"type": "string", "enum": ["city_ambiguous"]}},
                  "required": ["reason"], "additionalProperties": False}
        provider = FakeProvider([native("request_clarification", {"reason": "city_ambiguous"})])
        outcome = await AgentOrchestrator(provider, [ToolSpec("request_clarification", "澄清", schema, clarify, terminal=True)]).run(request())
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(outcome.reply, "请明确目的地。")
        self.assertEqual(outcome.trace["stop_reason"], "awaiting_user")

    async def test_unknown_tool_does_not_execute_or_retry(self):
        provider = FakeProvider([native("delete_everything")])
        with self.assertRaises(RuntimeFailure) as raised:
            await AgentOrchestrator(provider, []).run(request())
        self.assertEqual(raised.exception.code, "tool_not_allowed")
        self.assertEqual(len(provider.calls), 1)

    async def test_extra_argument_does_not_reach_handler(self):
        called = []
        async def handler(args):
            called.append(args)
            return {}
        provider = FakeProvider([native(args={"keyword": "博物馆", "owner_id": "other"})])
        with self.assertRaises(RuntimeFailure) as raised:
            await AgentOrchestrator(provider, [ToolSpec("search_places", "搜索", SCHEMA, handler)]).run(request())
        self.assertEqual(raised.exception.code, "tool_arguments")
        self.assertFalse(called)

    async def test_role_scope_hides_and_blocks_wrong_role_tool(self):
        async def handler(args):
            self.fail("must not execute")
        provider = FakeProvider([native()])
        spec = ToolSpec("search_places", "搜索", SCHEMA, handler, allowed_roles=("places",))
        with self.assertRaises(RuntimeFailure):
            await AgentOrchestrator(provider, [spec]).run(request("当前预算是多少"))
        self.assertEqual(provider.calls[0]["tools"], [])

    async def test_duplicate_tool_is_no_progress_and_not_repeated(self):
        called = []
        async def handler(args):
            called.append(args)
            return {"candidates": []}
        provider = FakeProvider([native(), native(identifier="call_2")])
        with self.assertRaises(RuntimeFailure) as raised:
            await AgentOrchestrator(provider, [ToolSpec("search_places", "搜索", SCHEMA, handler)]).run(request())
        self.assertEqual(raised.exception.code, "no_progress")
        self.assertEqual(len(called), 1)

    async def test_three_rounds_share_one_budget_and_stop_with_real_results(self):
        async def handler(args):
            return {"searched": args["keyword"]}
        provider = FakeProvider([native(args={"keyword": str(i)}, identifier=f"call_{i}") for i in range(3)])
        outcome = await AgentOrchestrator(provider, [ToolSpec("search_places", "搜索", SCHEMA, handler)]).run(request())
        self.assertEqual(len(provider.calls), 3)
        self.assertEqual(outcome.trace["model_calls"], 3)
        self.assertEqual(outcome.trace["stop_reason"], "model_budget")
        self.assertEqual(len(outcome.tool_results), 3)

    async def test_cancellation_stops_handler_and_does_not_start_next_model(self):
        started, stopped = asyncio.Event(), asyncio.Event()
        async def handler(args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        provider = FakeProvider([native()])
        task = asyncio.create_task(AgentOrchestrator(provider, [ToolSpec("search_places", "搜索", SCHEMA, handler)]).run(request()))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(stopped.is_set())
        self.assertEqual(len(provider.calls), 1)

    async def test_whole_operation_deadline(self):
        class Waiting:
            async def complete(self, *args, **kwargs):
                await asyncio.Event().wait()
        with self.assertRaises(RuntimeFailure) as raised:
            await AgentOrchestrator(Waiting(), [], budget=ExecutionBudget(deadline_seconds=.01)).run(request())
        self.assertEqual(raised.exception.code, "deadline")
        self.assertEqual(raised.exception.trace["model_calls"], 1)

    async def test_handler_error_is_safe_and_not_retried(self):
        async def handler(args):
            raise ValueError("private-secret-should-not-leak")
        provider = FakeProvider([native()])
        with self.assertRaises(RuntimeFailure) as raised:
            await AgentOrchestrator(provider, [ToolSpec("search_places", "搜索", SCHEMA, handler)]).run(request())
        self.assertEqual(raised.exception.code, "tool_failed")
        self.assertNotIn("private-secret", str(raised.exception.trace))
        self.assertEqual(len(provider.calls), 1)

    async def test_quota_error_is_preserved_with_safe_trace_and_no_retry(self):
        error = UsageError("business_quota_exhausted", service="amap_poi", scope="account", retry_after_seconds=20)
        async def handler(args):
            raise error
        provider = FakeProvider([native()])
        orchestrator = AgentOrchestrator(provider, [ToolSpec("search_places", "搜索", SCHEMA, handler)])
        with self.assertRaises(UsageError) as raised:
            await orchestrator.run(request())
        self.assertIs(raised.exception, error)
        self.assertEqual(error.trace["model_calls"], 1)
        self.assertEqual(error.trace["tool_calls"], 1)
        self.assertEqual(error.trace["stop_reason"], "business_quota_exhausted")
        self.assertEqual(error.retry_after_seconds, 20)
        self.assertEqual(orchestrator.monitor.summary()["successful"], 0)
        self.assertEqual(len(provider.calls), 1)
        self.assertNotIn("博物馆", str(error.trace))

    async def test_invalid_context_blocks_before_provider(self):
        provider = FakeProvider([])
        bad = AgentRequest("你好", "owner", "conv", "req", {"value": float("nan")})
        with self.assertRaises(RuntimeFailure):
            await AgentOrchestrator(provider, []).run(bad)
        self.assertFalse(provider.calls)

    async def test_small_total_output_limit_does_not_replenish_unknown_usage(self):
        async def handler(args):
            return {"found": False}
        provider = FakeProvider([ProviderResponse(tool_call=native().tool_call, output_tokens=None)])
        with self.assertRaises(RuntimeFailure) as raised:
            await AgentOrchestrator(provider, [ToolSpec("search_places", "搜索", SCHEMA, handler)],
                                    budget=ExecutionBudget(max_total_output_tokens=100)).run(request())
        self.assertEqual(raised.exception.code, "model_budget")
        self.assertEqual(provider.calls[0]["max_tokens"], 100)
        self.assertEqual(len(provider.calls), 1)


class ParserAndRegistryTests(unittest.TestCase):
    def response(self, *, finish="tool_calls", content=None, calls=None):
        return {"choices": [{"finish_reason": finish, "message": {"role": "assistant", "content": content,
                "tool_calls": calls if calls is not None else [{"id": "call_1", "type": "function",
                "function": {"name": "search_places", "arguments": '{"keyword":"上海"}'}}]}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20}}

    def test_native_tool_protocol_is_parsed(self):
        response = parse_native_response(self.response())
        self.assertEqual(response.tool_call.arguments, {"keyword": "上海"})
        self.assertIsNone(response.content)

    def test_single_native_call_allows_only_optional_integer_zero_index(self):
        envelope = self.response()
        envelope["choices"][0]["message"]["tool_calls"][0]["index"] = 0
        self.assertEqual(parse_native_response(envelope).tool_call.name, "search_places")
        for invalid in (1, -1, True, False, "0", None):
            envelope["choices"][0]["message"]["tool_calls"][0]["index"] = invalid
            with self.subTest(index=invalid), self.assertRaises(RuntimeFailure):
                parse_native_response(envelope)
        envelope["choices"][0]["message"]["tool_calls"][0].update(index=0, extra="rejected")
        with self.assertRaises(RuntimeFailure):
            parse_native_response(envelope)

    def test_multiple_calls_unknown_fields_duplicate_json_and_mixed_content_rejected(self):
        normal = self.response()["choices"][0]["message"]["tool_calls"][0]
        values = [self.response(calls=[normal, normal]), self.response(content="同时回答"),
                  self.response(calls=[normal | {"unknown": True}]),
                  self.response(calls=[normal | {"function": {"name": "search_places", "arguments": '{"keyword":"A","keyword":"B"}'}}])]
        for value in values:
            with self.subTest(value=value), self.assertRaises(RuntimeFailure):
                parse_native_response(value)

    def test_truncated_is_not_accepted_as_partial_answer(self):
        with self.assertRaises(RuntimeFailure) as raised:
            parse_native_response(self.response(finish="length"))
        self.assertEqual(raised.exception.code, "truncated")

    def test_normal_text_requires_stop_and_no_tools(self):
        response = parse_native_response(self.response(finish="stop", content="请告诉我出行日期。", calls=[]))
        self.assertEqual(response.content, "请告诉我出行日期。")
        with self.assertRaises(RuntimeFailure):
            parse_native_response(self.response(finish="stop", content="", calls=[]))

    def test_argument_types_dates_and_nonfinite_are_rejected(self):
        date_schema = {"type": "string", "format": "date", "minLength": 10, "maxLength": 10}
        validate_arguments("2028-02-29", date_schema)
        for value in ("2026-02-29", "2026-2-3"):
            with self.assertRaises(RuntimeFailure):
                validate_arguments(value, date_schema)
        for value in (True, float("inf"), float("nan")):
            with self.assertRaises(RuntimeFailure):
                validate_arguments(value, {"type": "number"})

    def test_unknown_schema_keyword_fails_closed(self):
        with self.assertRaises(RuntimeFailure) as raised:
            validate_arguments("hello", {"type": "string", "unimplementedConstraint": True})
        self.assertEqual(raised.exception.code, "tool_schema")

    def test_skills_are_local_fixed_names_and_fit_without_truncation(self):
        manager = SkillManager()
        self.assertEqual(len(manager.load()), 3)
        self.assertIn("明确确认", manager.prompt_for("hello"))
        self.assertEqual(manager.summary()["count"], 3)
        self.assertTrue(all(len(item["sha256"]) == 64 for item in manager.summary()["skills"]))

    def test_routing_is_source_labelled_and_no_confidence_claim(self):
        route = IntentRecognizer().recognize("预算还差多少")
        self.assertEqual(route.role, "budget")
        self.assertEqual(route.source, "deterministic_lexical_v1")
        self.assertFalse(hasattr(route, "confidence"))

    def test_execution_budget_rejects_expansion_and_unknown_usage_reserves_tokens(self):
        for kwargs in ({"max_model_calls": 4}, {"max_tool_calls": 5}, {"deadline_seconds": 46}, {"max_tokens_per_call": 2049}):
            with self.assertRaises(ValueError):
                ExecutionBudget(**kwargs)
        state = ExecutionState(ExecutionBudget())
        allowance = state.begin_model()
        state.record_output(None, allowance)
        self.assertEqual(state.output_tokens, 2048)
        self.assertTrue(state.output_tokens_unknown)

    def test_gateway_requires_metered_transport(self):
        with self.assertRaises(ValueError):
            QwenGateway(object(), httpx.AsyncClient())


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    def settings(self, base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"):
        return Settings(_env_file=None, qwen_api_key="controlled-placeholder", qwen_base_url=base)

    async def test_native_payload_fixed_endpoint_and_durable_one_attempt(self):
        fixture = UsageFixture()
        self.addCleanup(fixture.close)
        sent = []
        def upstream(request):
            sent.append(request)
            return httpx.Response(200, json=ParserAndRegistryTests().response())
        with fixture.bind("/chat/conversations/12345678-1234-4234-8234-123456789abc/turns"):
            async with MeteredAsyncClient(transport=httpx.MockTransport(upstream)) as client:
                answer = await QwenGateway(self.settings(), client).complete(
                    [{"role": "user", "content": "controlled"}], [], max_tokens=2048)
        self.assertEqual(len(sent), 1)
        self.assertEqual(str(sent[0].url), "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
        payload = json.loads(sent[0].content)
        self.assertFalse(payload["enable_thinking"])
        self.assertFalse(payload["stream"])
        self.assertFalse(payload["parallel_tool_calls"])
        self.assertEqual(payload["max_tokens"], 2048)
        self.assertEqual(answer.tool_call.name, "search_places")
        summary = fixture.store.own_usage("a" * 32)
        self.assertEqual(summary["services"]["qwen_text"]["used"], 1)

    async def test_bad_origin_and_wrong_model_block_before_transport(self):
        sent = []
        async with MeteredAsyncClient(transport=httpx.MockTransport(lambda request: sent.append(request) or httpx.Response(200))) as client:
            for settings in (self.settings("https://example.invalid/compatible-mode/v1"),
                             self.settings().model_copy(update={"qwen_model": "another-model"})):
                with self.subTest(model=settings.qwen_model), self.assertRaises(RuntimeFailure) as raised:
                    await QwenGateway(settings, client).complete([], [])
                self.assertEqual(raised.exception.code, "configuration")
        self.assertEqual(sent, [])

    async def test_provider_auth_failure_sends_once_no_fallback(self):
        fixture = UsageFixture()
        self.addCleanup(fixture.close)
        sent = []
        with fixture.bind("/chat/conversations/12345678-1234-4234-8234-123456789abc/turns"):
            async with MeteredAsyncClient(transport=httpx.MockTransport(
                    lambda request: sent.append(request) or httpx.Response(401, json={"error": {"code": "InvalidApiKey"}}))) as client:
                with self.assertRaises(RuntimeFailure) as raised:
                    await QwenGateway(self.settings(), client).complete([], [])
        self.assertEqual(raised.exception.code, "authentication")
        self.assertEqual(len(sent), 1)
        self.assertEqual(fixture.store.own_usage("a" * 32)["services"]["qwen_text"]["used"], 1)


if __name__ == "__main__":
    unittest.main()

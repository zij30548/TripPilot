"""Repaired travel-only wait boundaries through real ASGI and isolated MySQL.

Native responses are scripted, not new model quality measurements.  Every
outbound request uses ControlledUpstream; an unused response traps accidental
continuation after a tool that must hand control back to the user/browser.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import CancelledError as FutureCancelledError, ThreadPoolExecutor
from dataclasses import replace
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

if any(importlib.util.find_spec(name) is None for name in ("jsonschema", "prometheus_client")):
    raise unittest.SkipTest("requires explicit optional candidate dependencies")

from fastapi.testclient import TestClient

from echomind_core.execution import ExecutionError, ExecutionGate, GuardedToolRegistry, ToolDeclaration, bind_execution
from echomind_core.provider import MessageResponse, MessageUsage, TaskBudget, ToolUseBlock
from echomind_core.travel_bridge import TravelToolBridge
from echomind_core.travel_engine import TravelOrchestrator
from echomind_core.travel_terminal import TravelFeedbackBoundary, _ProgramHandoff
from echomind_core.upstream.agents.agent_orchestrator import AgentType, BaseAgent
from echomind_core.upstream.agents.tools import AgentToolSpec
from memory.assistant_store import AssistantStore
from tests.mysql_support import temporary_mysql
from tests.test_echomind_travel_bridge import BINDING, SCOPE, capture, request
from tooling.travel.dialogue import GUIDANCE
from tools.echomind_travel_controlled import (
    REQUIREMENTS_TEXT, ControlledUpstream, controlled_app, initialize,
    native_text, native_tool, scenario_tools,
)
from tools.echomind_travel_live_runner import NativeSession


def tool(name: str, arguments: dict, *, serial: int = 1) -> dict:
    response = native_tool(name, arguments)
    response["id"] = f"terminal_response_{serial}"
    response["choices"][0]["message"]["tool_calls"][0]["id"] = f"terminal_call_{serial}"
    return response


def multiple_tools(*selections: tuple[str, dict]) -> dict:
    response = tool(*selections[0])
    response["choices"][0]["message"]["tool_calls"] = [
        tool(name, arguments, serial=index)["choices"][0]["message"]["tool_calls"][0]
        for index, (name, arguments) in enumerate(selections, 1)
    ]
    return response


class TravelTerminalHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine, cleanup = temporary_mysql()
        self.addCleanup(cleanup)
        folder = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="travel-terminal-"))).resolve()
        anchor = folder / "usage.identity"
        anchor.write_text(uuid4().hex + "\n", encoding="ascii")
        anchor.chmod(0o600)
        initialize(self.engine, anchor)
        self.upstream = ControlledUpstream()
        app, _ = self.enterContext(controlled_app(
            self.engine, anchor, origin="http://127.0.0.1:3000", upstream=self.upstream))
        self.client = self.enterContext(TestClient(app))
        self.runner = NativeSession(self.client, self.engine, origin="http://127.0.0.1:3000")
        self.conversation = self.runner.conversation("empty")
        self.path = f"/chat/conversations/{self.conversation}"

    def submit(self, text: str, *, operation: str | None = None):
        view = self.client.get(self.path).json()
        return self.client.post(self.path + "/assistant-turns", headers=self.runner.headers, json={
            "operation_id": operation or str(uuid4()), "expected_revision": view["revision"], "text": text,
            "dialogue_context": {"workspace_version": str(uuid4()), "display": "none", "items": []},
        })

    def task(self) -> dict:
        return AssistantStore(self.engine).latest(self.runner.owner, self.conversation)

    def assert_waiting(self, response, *, model_calls: int, tool_calls: int) -> dict:
        self.assertEqual(response.status_code, 200, response.text)
        chat = response.json()
        self.assertEqual(chat["last_turn"]["state"], "succeeded")
        self.assertEqual(chat["messages"][-1]["role"], "assistant")
        self.assertNotIn("CONTROLLED_FREE_PROSE", chat["messages"][-1]["text"])
        task = self.task()
        self.assertEqual(task["state"], "waiting")
        self.assertEqual(task["checkpoint"]["stop_reason"], "awaiting_user")
        self.assertEqual(task["checkpoint"]["waiting_for"], "user")
        state = self.client.get(self.path + "/assistant-state").json()["task"]
        self.assertEqual((state["model_calls"], state["tool_calls"]), (model_calls, tool_calls))
        self.assertTrue(all(row["state"] == "succeeded" for row in state["steps"]))
        self.assertEqual(len(self.upstream.events), model_calls)
        self.assertTrue(all(row["service"] == "controlled_model" for row in self.upstream.events))
        return chat

    def test_first_clarification_delivers_program_question_with_one_send(self) -> None:
        self.upstream.responses = [tool("travel_clarify", {"reason": "destination_missing"}), native_text()]
        result = self.runner.submit("m01")
        self.assertEqual(result["http_status"], 200, result)
        self.assertIn("请明确本次想去的城市", result["readback"]["messages"][-1]["text"])
        self.assertIsNone(result["readback"]["requirements"]["destination_id"])
        self.assertIsNone(result["readback"]["confirmed_revision"])
        self.assertIsNone(result["proposal"]["action"])
        task = result["task_state"]["task"]
        self.assertEqual((task["state"], task["model_calls"], task["tool_calls"]), ("waiting", 1, 1))
        self.assertTrue(all(row["state"] == "succeeded" for row in task["steps"]))
        self.assertEqual(len(self.upstream.events), 1)
        self.assertEqual(len(self.upstream.responses), 1)  # No invented follow-up model stop.

    def test_inspect_then_clarification_keeps_native_feedback_and_stops_at_two(self) -> None:
        self.upstream.responses = [tool("inspect_trip", {}),
            tool("travel_clarify", {"reason": "date_missing"}, serial=2), native_text()]
        chat = self.assert_waiting(self.submit("先核对已有需求，缺少日期就问我。"), model_calls=2, tool_calls=2)
        self.assertIn("请说明出发和结束日期", chat["messages"][-1]["text"])
        self.assertEqual([row["native_tool_feedback"] for row in self.upstream.events], [False, True])
        self.assertEqual(len(self.upstream.responses), 1)

    def test_third_allowed_model_turn_can_still_finish_with_clarification(self) -> None:
        self.upstream.responses = [tool("inspect_trip", {}), tool("read_preferences", {}, serial=2),
            tool("travel_clarify", {"reason": "date_missing"}, serial=3), native_text()]
        chat = self.assert_waiting(self.submit("先核对输入和偏好，再询问缺少的日期。"), model_calls=3, tool_calls=3)
        self.assertIn("请说明出发和结束日期", chat["messages"][-1]["text"])
        self.assertEqual(len(self.upstream.responses), 1)

    def test_read_budget_is_one_unexecuted_browser_proposal_not_a_cost_answer(self) -> None:
        self.upstream.responses = [tool("travel_budget", {"action": "read_budget"}), native_text()]
        chat = self.assert_waiting(self.submit("当前费用是多少，还有哪些没填？"), model_calls=1, tool_calls=1)
        self.assertNotIn("¥0", chat["messages"][-1]["text"])
        action = self.client.get(self.path + "/dialogue-action").json()["action"]
        self.assertEqual(action, {"name": "read_budget", "arguments": {}})
        self.assertIn("尚未执行", chat["messages"][-1]["text"])
        self.assertEqual(len(self.upstream.responses), 1)

    def test_fixed_guidance_finishes_without_model_prose_or_false_waiting_state(self) -> None:
        self.upstream.responses = [tool("travel_information", {"topic": "current_rules"}), native_text()]
        response = self.submit("现在这个场馆的规则有可靠依据吗？")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["messages"][-1]["text"], GUIDANCE["current_rules"])
        task = self.task()
        self.assertEqual(task["state"], "succeeded")
        self.assertEqual(task["checkpoint"]["stop_reason"], "answered")
        self.assertIsNone(task["checkpoint"]["waiting_for"])
        public = self.client.get(self.path + "/assistant-state").json()["task"]
        self.assertEqual((public["model_calls"], public["tool_calls"]), (1, 1))
        self.assertEqual(len(self.upstream.events), 1)
        self.assertEqual(len(self.upstream.responses), 1)

    def test_adjustment_only_offers_proposal_waiting_for_explicit_confirmation(self) -> None:
        self.upstream.responses = [tool("travel_adjustment", {
            "action": "preview_exclusion", "selector": "受控公园甲"}), native_text()]
        chat = self.assert_waiting(self.submit("预览排除受控公园甲"), model_calls=1, tool_calls=1)
        action = self.client.get(self.path + "/dialogue-action").json()["action"]
        self.assertEqual(action, {"name": "preview_exclusion", "arguments": {"selector": "受控公园甲"}})
        self.assertIn("尚未执行", chat["messages"][-1]["text"])
        self.assertEqual(len(self.upstream.responses), 1)

    def test_requirements_proposal_becomes_unconfirmed_draft_after_one_send(self) -> None:
        name, selection = scenario_tools()[REQUIREMENTS_TEXT]
        self.upstream.responses = [tool(name, selection), native_text()]
        chat = self.assert_waiting(self.submit(REQUIREMENTS_TEXT), model_calls=1, tool_calls=1)
        self.assertEqual(chat["requirements"]["destination_id"], "shanghai")
        self.assertEqual(chat["requirements"]["budget_cny"], "3000")
        self.assertEqual(chat["requirements"]["required_place_hints"], ["受控景点甲"])
        self.assertIsNone(chat["confirmed_revision"])
        places = self.client.get(self.path + "/places").json()
        self.assertIsNone(places["accommodation"])
        self.assertEqual(places["required_places"], [])
        self.assertIn("待确认需求草稿", chat["messages"][-1]["text"])
        self.assertEqual(len(self.upstream.responses), 1)

    def test_invalid_arguments_and_unknown_tool_do_not_become_terminal_success(self) -> None:
        for name, arguments in (("travel_clarify", {"reason": "not_a_reason"}),
                                ("travel_clarify", {"reason": "date_missing", "unexpected": True}),
                                ("unknown_tool", {})):
            with self.subTest(tool=name, shape=tuple(arguments)):
                before = len(self.upstream.events)
                self.upstream.responses = [tool(name, arguments)]
                response = self.submit("需要明确日期。")
                self.assertEqual(response.status_code, 502, response.text)
                self.assertEqual(len(self.upstream.events), before + 1)
                self.assertEqual(self.task()["state"], "failed")
                self.assertIsNone(self.client.get(self.path + "/dialogue-action").json()["action"])
                self.assertIsNone(self.client.get(self.path).json()["requirements"]["destination_id"])

    def test_terminal_mixed_batch_is_rejected_before_any_tool_execution(self) -> None:
        for second in (("inspect_trip", {}), ("travel_clarify", {"reason": "date_missing"}), ("unknown_tool", {}),
                       ("travel_clarify", {"reason": "invalid"})):
            with self.subTest(second_tool=second[0], arguments=second[1]):
                before = len(self.upstream.events)
                self.upstream.responses = [multiple_tools(
                    ("travel_clarify", {"reason": "destination_missing"}), second)]
                response = self.submit("一次只核对一个问题。")
                self.assertEqual(response.status_code, 502, response.text)
                self.assertEqual(len(self.upstream.events), before + 1)
                state = self.client.get(self.path + "/assistant-state").json()["task"]
                self.assertEqual((state["state"], state["tool_calls"]), ("failed", 0))
                self.assertIsNone(self.client.get(self.path + "/dialogue-action").json()["action"])

    def test_context_change_after_tool_prevents_success_without_another_send(self) -> None:
        self.upstream.responses = [tool("travel_clarify", {"reason": "destination_missing"}), native_text()]
        original = TravelToolBridge._record

        async def invalidate_after_record(bridge, *arguments, **keywords):
            result = await original(bridge, *arguments, **keywords)
            bridge.invalidate()
            return result

        with patch.object(TravelToolBridge, "_record", new=invalidate_after_record):
            response = self.submit("我还没有决定去哪。")
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(len(self.upstream.events), 1)
        self.assertEqual(self.task()["state"], "failed")
        self.assertEqual(self.client.get(self.path).json()["messages"][-1]["role"], "user")
        self.assertIsNone(self.client.get(self.path + "/dialogue-action").json()["action"])

    def test_failed_post_handler_gate_cannot_become_a_successful_handoff(self) -> None:
        from echomind_core.execution import execution_gate

        self.upstream.responses = [tool("travel_clarify", {"reason": "destination_missing"}), native_text()]
        original = TravelToolBridge._record

        async def fail_after_record(bridge, *arguments, **keywords):
            result = await original(bridge, *arguments, **keywords)
            execution_gate().fail("controlled_post_handler_failure")
            return result

        with patch.object(TravelToolBridge, "_record", new=fail_after_record):
            response = self.submit("我还没有决定去哪。")
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(len(self.upstream.events), 1)
        self.assertEqual(self.task()["state"], "failed")
        self.assertEqual(self.client.get(self.path).json()["messages"][-1]["role"], "user")
        self.assertEqual(len(self.upstream.responses), 1)

    def test_invalid_terminal_result_is_not_promoted_even_after_tool_record(self) -> None:
        original = TravelToolBridge._record
        for corruption in ({"kind": "guidance"}, {"executed": True}, {"success": False}):
            with self.subTest(corruption=corruption):
                before = len(self.upstream.events)
                self.upstream.responses = [tool("travel_clarify", {"reason": "destination_missing"}), native_text()]

                async def corrupt_after_record(bridge, *arguments, **keywords):
                    result = await original(bridge, *arguments, **keywords)
                    return result | corruption

                with patch.object(TravelToolBridge, "_record", new=corrupt_after_record):
                    response = self.submit("还没有决定目的地。")
                self.assertEqual(response.status_code, 502, response.text)
                self.assertEqual(len(self.upstream.events), before + 1)
                self.assertEqual(self.task()["state"], "failed")
                self.assertEqual(self.client.get(self.path).json()["messages"][-1]["role"], "user")
                self.assertIsNone(self.client.get(self.path + "/dialogue-action").json()["action"])
                self.assertEqual(len(self.upstream.responses), 1)

    def test_cancel_during_send_cannot_deliver_late_terminal_result(self) -> None:
        self.upstream.pause_next = True
        self.upstream.responses = [tool("travel_clarify", {"reason": "date_missing"}), native_text()]
        operation = str(uuid4())
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.submit, "还没决定出发日期。", operation=operation)
            try:
                self.assertTrue(self.upstream.entered.wait(10))
                response = self.client.post(self.path + "/cancel", headers=self.runner.headers,
                                            json={"operation_id": operation})
                self.assertEqual(response.status_code, 200, response.text)
            finally:
                self.upstream.release.set()
            try:
                late = future.result(timeout=10)
            except FutureCancelledError:
                late = None
        if late is not None:
            self.assertIn(late.status_code, {409, 499, 502}, late.text)
        chat = self.client.get(self.path).json()
        self.assertEqual(chat["last_turn"]["state"], "cancelled")
        self.assertEqual(chat["messages"][-1]["role"], "user")
        self.assertEqual(len(self.upstream.events), 1)
        self.assertIsNone(self.client.get(self.path + "/dialogue-action").json()["action"])

    def test_same_completed_operation_does_not_send_terminal_tool_again(self) -> None:
        operation = str(uuid4())
        self.upstream.responses = [tool("travel_clarify", {"reason": "destination_missing"}), native_text()]
        self.assert_waiting(self.submit("去哪还没决定。", operation=operation), model_calls=1, tool_calls=1)
        replay = self.submit("去哪还没决定。", operation=operation)
        self.assertEqual(replay.status_code, 409, replay.text)
        self.assertEqual(len(self.upstream.events), 1)
        self.assertEqual(len(self.upstream.responses), 1)

    def test_three_nonterminal_inspections_still_fail_at_original_budget(self) -> None:
        self.upstream.responses = [tool("inspect_trip", {}, serial=index) for index in range(1, 4)] + [native_text()]
        response = self.submit("反复核对需求，但不要执行操作。")
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(response.json()["detail"]["code"], "assistant_agent_failed")
        task = self.client.get(self.path + "/assistant-state").json()["task"]
        self.assertEqual((task["state"], task["model_calls"], task["tool_calls"]), ("failed", 3, 3))
        self.assertEqual(len(self.upstream.events), 3)
        self.assertEqual(len(self.upstream.responses), 1)
        self.assertIsNone(self.client.get(self.path + "/dialogue-action").json()["action"])


class TravelTerminalBoundaryTests(unittest.IsolatedAsyncioTestCase):
    """Invocation-local handoff identity and native trace preservation."""

    def setUp(self) -> None:
        snapshot, *_ = capture()
        self.bridge = TravelToolBridge(snapshot, lambda: BINDING)
        self.request = request()
        self.gate = ExecutionGate(SCOPE, lambda: SCOPE, TaskBudget())

    def client(self, *calls: tuple[str, str, dict]):
        responses = [MessageResponse((ToolUseBlock(identifier, name, arguments),), "tool_use",
                                    MessageUsage(20, 10)) for identifier, name, arguments in calls]
        self.create = AsyncMock(side_effect=responses)
        return SimpleNamespace(messages=SimpleNamespace(create=self.create))

    async def test_actual_inspect_and_clarify_traces_keep_native_ids_names_arguments_and_success(self) -> None:
        client = self.client(("inspect_native_id", "inspect_trip", {}),
                             ("clarify_native_id", "travel_clarify", {"reason": "date_missing"}))
        with bind_execution(self.gate):
            boundary = TravelFeedbackBoundary(self.request, "general", client)
            specs = self.bridge.tools()
            registry = GuardedToolRegistry(tuple(ToolDeclaration(specs[name], True)
                                                for name in ("inspect_trip", "travel_clarify")))
            first = await boundary.create()
            self.assertEqual(first.content[0].id, "inspect_native_id")
            await boundary.wrap(registry.wrap(specs["inspect_trip"])).handler(self.request, {})
            second = await boundary.create()
            self.assertEqual(second.content[0].id, "clarify_native_id")
            with self.assertRaises(_ProgramHandoff) as caught:
                await boundary.wrap(registry.wrap(specs["travel_clarify"])).handler(
                    self.request, {"reason": "date_missing"})
            result = boundary.finish(caught.exception)
            self.assertEqual(result.tools_used, ["inspect_trip", "travel_clarify"])
            self.assertEqual([(row["tool_use_id"], row["tool_name"], row["input"], row["success"])
                              for row in result.tool_traces], [
                ("inspect_native_id", "inspect_trip", {}, True),
                ("clarify_native_id", "travel_clarify", {"reason": "date_missing"}, True),
            ])
            self.assertTrue(all(row["result_success"] is not False and row["error"] == ""
                                and row["latency_ms"] >= 0 for row in result.tool_traces))
            self.assertEqual(self.gate.budget.counts, (0, 2, 0))  # No real/model-provider layer in this unit.
            projected = await self.bridge.project()
            self.assertEqual(projected.state, "clarification")
            self.assertEqual([row["name"] for row in projected.tool_results], ["inspect_trip", "travel_clarify"])
        self.assertEqual(self.create.await_count, 2)

    async def test_same_binding_different_request_object_is_rejected_before_handler(self) -> None:
        client = self.client(("clarify_native_id", "travel_clarify", {"reason": "date_missing"}))
        handler = AsyncMock(return_value={"kind": "clarification", "executed": False})
        spec = AgentToolSpec("travel_clarify", "controlled", {}, handler)
        clone = replace(self.request)
        self.assertEqual(clone, self.request)
        self.assertIsNot(clone, self.request)
        with bind_execution(self.gate):
            boundary = TravelFeedbackBoundary(self.request, "general", client)
            await boundary.create()
            with self.assertRaises(ExecutionError) as caught:
                await boundary.wrap(spec).handler(clone, {"reason": "date_missing"})
            self.assertEqual(caught.exception.code, "travel_context_changed")
            handler.assert_not_awaited()
            self.assertEqual(boundary.traces, [])
            self.assertTrue(self.gate.budget.stopped)

    async def test_handoff_from_another_invocation_is_not_accepted(self) -> None:
        with bind_execution(self.gate):
            owned = TravelFeedbackBoundary(self.request, "general", self.client(
                ("owned_native_id", "travel_clarify", {"reason": "date_missing"})))
            foreign = TravelFeedbackBoundary(self.request, "general", self.client(
                ("foreign_native_id", "travel_clarify", {"reason": "date_missing"})))
            spec = AgentToolSpec("travel_clarify", "controlled", {},
                                 AsyncMock(return_value={"kind": "clarification", "executed": False}))
            await owned.create()
            with self.assertRaises(_ProgramHandoff):
                await owned.wrap(spec).handler(self.request, {"reason": "date_missing"})
            await foreign.create()
            with self.assertRaises(_ProgramHandoff) as signal:
                await foreign.wrap(spec).handler(self.request, {"reason": "date_missing"})
            with self.assertRaises(ExecutionError) as caught:
                owned.finish(signal.exception)
            self.assertEqual(caught.exception.code, "travel_context_changed")
            self.assertTrue(self.gate.budget.stopped)

    async def test_cancellation_during_final_authority_check_propagates_and_restores_client(self) -> None:
        agent = TravelOrchestrator(self.bridge)._pool[AgentType.GENERAL][0]
        client = self.client(("clarify_native_id", "travel_clarify", {"reason": "date_missing"}))
        agent._client = client
        entered, release = asyncio.Event(), asyncio.Event()
        original_check = self.bridge._check

        async def checked(request=None):
            boundary = getattr(agent, "_feedback_boundary", None)
            if boundary is not None and boundary._terminal:
                entered.set()
                await release.wait()  # Deterministic cancellation at signal-catch authority check.
            return await original_check(request)

        async def native_loop(instance, req):
            # The real boundary still wraps the registry and consumes an actual
            # controlled ToolUseBlock; no fake handoff or final model text.
            await instance._client.messages.create()
            await instance.get_tools()["travel_clarify"].handler(req, {"reason": "date_missing"})
            self.fail("terminal handler must signal handoff")

        with bind_execution(self.gate), patch.object(BaseAgent, "_call_llm", new=native_loop), \
                patch.object(self.bridge, "_check", new=checked):
            worker = asyncio.create_task(agent._call_llm(self.request))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                worker.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await worker
            finally:
                release.set()
                if not worker.done():
                    worker.cancel()
                    await asyncio.gather(worker, return_exceptions=True)
            self.assertTrue(worker.cancelled())
            self.assertIs(agent._client, client)
            self.assertIsNone(agent._feedback_boundary)
            self.assertIsNone(agent._feedback_tools)
            self.assertEqual(self.create.await_count, 1)


if __name__ == "__main__":
    unittest.main()

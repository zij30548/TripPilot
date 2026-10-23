"""No external calls: real persistent usage store and controlled native Qwen wire."""
from __future__ import annotations

import asyncio
from contextlib import closing
import json
import sqlite3
import threading
import unittest
from unittest.mock import patch

import httpx

try:
    import jsonschema as _jsonschema  # noqa: F401 -- exact optional-extra gate
except ModuleNotFoundError as error:
    if error.name == "jsonschema":
        raise unittest.SkipTest("EchoMind provider checks require the explicit echomind optional dependencies") from None
    raise

from core.config import Settings
from core.usage_runtime import MeteredAsyncClient
from echomind_core.provider import (
    MODEL, ProviderError, QwenMessagesClient, TaskBudget, ToolUseBlock, bind_provider,
)
from tests.usage_fixture import UsageFixture

PATH = "/chat/conversations/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/assistant-turns"
TOOLS = [{"name": "get_day", "description": "Read one explicitly selected day.", "input_schema": {
    "type": "object", "properties": {"date": {"type": "string", "enum": ["2026-10-23"]}},
    "required": ["date"], "additionalProperties": False,
}}]


def text_response(text: str = "controlled answer") -> dict:
    return {"id": "request_controlled", "model": MODEL, "choices": [{"finish_reason": "stop", "message": {
        "role": "assistant", "content": text,
    }}], "usage": {"prompt_tokens": 11, "completion_tokens": 3}}


def call_response(*identifiers: str) -> dict:
    return {"id": "request_controlled", "model": MODEL, "choices": [{"finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": None, "tool_calls": [{
            "id": identifier, "type": "function", "function": {"name": "get_day", "arguments": '{"date":"2026-10-23"}'},
        } for identifier in identifiers],
    }}], "usage": {"prompt_tokens": 11, "completion_tokens": 12}}


class QwenBridgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.fixture = UsageFixture()
        self.addCleanup(self.fixture.close)
        self.settings = Settings(_env_file=None, qwen_api_key="test-not-a-real-key",
                                 qwen_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", qwen_model=MODEL)
        self.sent: list[dict] = []

    def client(self, response: dict | None = None) -> MeteredAsyncClient:
        def upstream(request: httpx.Request) -> httpx.Response:
            self.assertEqual(str(request.url), "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
            self.sent.append(json.loads(request.content))
            return httpx.Response(200, json=text_response() if response is None else response)
        return MeteredAsyncClient(transport=httpx.MockTransport(upstream), follow_redirects=False)

    def rows(self) -> list[tuple]:
        with closing(sqlite3.connect(self.fixture.store.settings.business_usage_path)) as db:
            return db.execute("SELECT service,state FROM usage_attempts ORDER BY rowid").fetchall()

    async def create(self, facade: QwenMessagesClient | None = None, **kwargs):
        arguments = {"model": MODEL, "max_tokens": 256, "messages": [{"role": "user", "content": "controlled input"}]}
        arguments.update(kwargs)
        return await (facade or QwenMessagesClient()).messages.create(**arguments)

    async def test_unbound_and_credential_override_cannot_dispatch(self) -> None:
        with self.assertRaisesRegex(ProviderError, "provider_unbound"):
            await self.create()
        for arguments in ({"api_key": "secret-should-not-be-echoed"}, {"base_url": self.settings.qwen_base_url},
                          {"max_retries": 1}, {"max_retries": True}):
            with self.assertRaisesRegex(ProviderError, "^configuration$"):
                QwenMessagesClient(**arguments)
        self.assertEqual(self.rows(), [])

    async def test_closing_one_facade_does_not_close_shared_transport_or_other_child(self) -> None:
        first, second = QwenMessagesClient(), QwenMessagesClient()
        with self.fixture.bind(PATH):
            async with self.client() as client:
                with bind_provider(self.settings, client, TaskBudget()):
                    await first.close()
                    await first.close()
                    self.assertFalse(client.is_closed)
                    with self.assertRaisesRegex(ProviderError, "provider_closed"):
                        await self.create(first)
                    await self.create(second)
        self.assertEqual(len(self.sent), 1)

    async def test_binding_requires_metered_client_fixed_model_and_official_configuration(self) -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as unmetered:
            with self.assertRaisesRegex(ProviderError, "configuration"):
                with bind_provider(self.settings, unmetered, TaskBudget()):
                    pass
        async with self.client() as client:
            for field, value in (("qwen_model", "claude-original"), ("qwen_base_url", "https://other.test/compatible-mode/v1")):
                settings = self.settings.model_copy(update={field: value})
                with self.assertRaisesRegex(ProviderError, "configuration"):
                    with bind_provider(settings, client, TaskBudget()):
                        pass
        self.assertEqual(self.rows(), [])

    async def test_text_response_preserves_wire_parameters_usage_and_metering(self) -> None:
        budget = TaskBudget()
        with self.fixture.bind(PATH):
            async with self.client() as client:
                with bind_provider(self.settings, client, budget):
                    response = await self.create(system="trusted rules", temperature=0.3)
        self.assertEqual(response.content[0].type, "text")
        self.assertEqual(response.content[0].text, "controlled answer")
        self.assertEqual(response.stop_reason, "end_turn")
        self.assertEqual((response.usage.input_tokens, response.usage.output_tokens), (11, 3))
        self.assertEqual((response.id, response.model), ("request_controlled", MODEL))
        self.assertEqual(budget.counts, (1, 0, 256))
        self.assertEqual(self.rows(), [("qwen_text", "received")])
        payload = self.sent[0]
        self.assertEqual((payload["model"], payload["stream"], payload["enable_thinking"], payload["max_tokens"], payload["temperature"]),
                         (MODEL, False, False, 256, 0.3))
        self.assertEqual(payload["messages"][0], {"role": "system", "content": "trusted rules"})

    async def test_bound_configuration_is_snapshot_not_mutable_caller_reference(self) -> None:
        with self.fixture.bind(PATH):
            async with self.client() as client:
                with bind_provider(self.settings, client, TaskBudget()):
                    self.settings.qwen_base_url = "https://not-trusted.test/compatible-mode/v1"
                    await self.create()
        self.assertEqual(len(self.sent), 1)

    async def test_native_multiple_tool_calls_preserve_identity_and_roundtrip_history(self) -> None:
        with self.fixture.bind(PATH):
            async with self.client(call_response("call_one", "call_two")) as client:
                with bind_provider(self.settings, client, TaskBudget()):
                    first = await self.create(tools=TOOLS)
                    self.assertEqual(first.stop_reason, "tool_use")
                    self.assertEqual([block.id for block in first.content], ["call_one", "call_two"])
                    # The bridge only selects; it has not executed either tool.
                    second = await self.create(tools=TOOLS, messages=[
                        {"role": "user", "content": "controlled input"},
                        {"role": "assistant", "content": first.content},
                        {"role": "user", "content": [
                            {"type": "tool_result", "tool_use_id": "call_one", "content": '{"ok":true}'},
                            {"type": "tool_result", "tool_use_id": "call_two", "content": '{"ok":false}', "is_error": True},
                        ]},
                    ])
                    self.assertEqual(len(second.content), 2)
        wire = self.sent[1]["messages"]
        self.assertEqual(wire[1]["tool_calls"][0]["function"]["name"], "get_day")
        self.assertEqual([item["tool_call_id"] for item in wire[2:]], ["call_one", "call_two"])
        self.assertEqual(self.sent[0]["tools"][0]["function"]["parameters"], TOOLS[0]["input_schema"])
        self.assertFalse(self.sent[0]["parallel_tool_calls"])

    async def test_malformed_unknown_duplicate_and_wrong_tool_arguments_reject_without_repair(self) -> None:
        for mutate in (
            lambda call: call["function"].update(name="not_allowed"),
            lambda call: call["function"].update(arguments='{"date":"2026-10-23","date":"2026-10-23"}'),
            lambda call: call["function"].update(arguments='{"date":"2027-01-01"}'),
            lambda call: call["function"].update(arguments='{"date":"2026-10-23","extra":true}'),
            lambda call: call.update(arbitrary="not acceptable"),
        ):
            data = call_response("call_one")
            mutate(data["choices"][0]["message"]["tool_calls"][0])
            with self.fixture.bind(PATH):
                async with self.client(data) as client:
                    with bind_provider(self.settings, client, TaskBudget()), self.assertRaises(ProviderError):
                        await self.create(tools=TOOLS)
        self.assertEqual(len(self.sent), 5)
        self.assertEqual(len(self.rows()), 5)

    async def test_duplicate_tool_ids_mixed_content_reasoning_and_truncation_rejected(self) -> None:
        for data in (call_response("same", "same"), call_response("one"), text_response(), text_response()):
            # Each branch independently exercises its invalid native envelope.
            index = len(self.sent)
            if index == 1:
                data["choices"][0]["message"]["content"] = "do not accept a guessed answer with tools"
            elif index == 2:
                data["choices"][0]["message"]["reasoning_content"] = "not to be logged"
            elif index == 3:
                data["choices"][0]["finish_reason"] = "length"
            with self.fixture.bind(PATH):
                async with self.client(data) as client:
                    with bind_provider(self.settings, client, TaskBudget()), self.assertRaises(ProviderError):
                        await self.create(tools=TOOLS)
        self.assertEqual(len(self.sent), 4)

    async def test_invalid_history_references_never_dispatch(self) -> None:
        for history in (
            [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "missing", "content": "result"}]}],
            [{"role": "assistant", "content": [ToolUseBlock("one", "get_day", {"date": "2026-10-23"})]},
             {"role": "user", "content": "skip the pending result"}],
            [{"role": "assistant", "content": [ToolUseBlock("one", "get_day", {"date": "2026-10-23"})]},
             {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "other", "content": "result"}]}],
        ):
            async with self.client() as client:
                with bind_provider(self.settings, client, TaskBudget()), self.assertRaises(ProviderError):
                    await self.create(tools=TOOLS, messages=history)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.rows(), [])

    async def test_invalid_local_parameters_schema_refs_and_input_bounds_never_dispatch(self) -> None:
        cases = [{"model": "claude-3-5-sonnet"}, {"max_tokens": 2049}, {"max_tokens": True}, {"temperature": float("nan")},
                 {"messages": [{"role": "user", "content": "x" * 24_001}]},
                 {"tools": [TOOLS[0], TOOLS[0]]},
                 {"tools": [{"name": "get_day", "description": "bad schema", "input_schema": {"$ref": "https://other.test/schema"}}]},
                 {"tools": [{"name": "get_day", "description": "bad schema", "input_schema": {
                     "$defs": {"loop": {"$ref": "#/$defs/loop"}}, "$ref": "#/$defs/loop"}}]}]
        async with self.client() as client:
            with bind_provider(self.settings, client, TaskBudget()):
                for kwargs in cases:
                    with self.assertRaises(ProviderError):
                        await self.create(**kwargs)
        self.assertEqual(self.sent, [])

    async def test_parallel_child_clients_share_atomic_budget(self) -> None:
        budget = TaskBudget()
        with self.fixture.bind(PATH):
            async with self.client() as client:
                with bind_provider(self.settings, client, budget):
                    results = await asyncio.gather(*(self.create(QwenMessagesClient(), max_tokens=2048) for _ in range(7)),
                                                   return_exceptions=True)
        self.assertEqual(sum(not isinstance(result, Exception) for result in results), 3)
        self.assertEqual(sum(isinstance(result, ProviderError) and result.code == "task_budget" for result in results), 4)
        self.assertEqual(budget.counts, (3, 0, 6144))
        self.assertEqual(len(self.sent), 3)
        self.assertEqual(len(self.rows()), 3)

    async def test_context_does_not_leak_and_nested_task_scope_restores_outer(self) -> None:
        first, second = TaskBudget(model_limit=1), TaskBudget(model_limit=1)
        with self.fixture.bind(PATH):
            async with self.client() as client:
                with bind_provider(self.settings, client, first):
                    with bind_provider(self.settings, client, second):
                        await self.create()
                    await self.create()
                with self.assertRaisesRegex(ProviderError, "provider_unbound"):
                    await self.create()
        self.assertEqual(first.counts, second.counts)
        self.assertEqual(first.counts, (1, 0, 256))

    async def test_child_inherited_context_is_revoked_after_parent_scope_exits(self) -> None:
        start = asyncio.Event()

        async def late_child():
            await start.wait()
            return await self.create()

        with self.fixture.bind(PATH):
            async with self.client() as client:
                with bind_provider(self.settings, client, TaskBudget()):
                    task = asyncio.create_task(late_child())
                start.set()
                with self.assertRaisesRegex(ProviderError, "provider_unbound"):
                    await task
        self.assertEqual(self.sent, [])
        self.assertEqual(self.rows(), [])

    async def test_inflight_child_cannot_publish_after_bound_scope_is_closed(self) -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        async def upstream(request):
            self.sent.append(json.loads(request.content))
            entered.set()
            await release.wait()
            return httpx.Response(200, json=text_response())

        with self.fixture.bind(PATH):
            async with MeteredAsyncClient(transport=httpx.MockTransport(upstream)) as client:
                with bind_provider(self.settings, client, TaskBudget()):
                    task = asyncio.create_task(self.create())
                    await asyncio.wait_for(entered.wait(), 2)
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.rows(), [("qwen_text", "cancelled")])

    async def test_binding_exit_during_durable_reservation_drains_worker_and_never_sends(self) -> None:
        entered, release = threading.Event(), threading.Event()
        reserve = self.fixture.store.reserve

        def paused_reserve(owner, service):
            entered.set()
            if not release.wait(3):
                raise AssertionError("controlled reservation release timed out")
            return reserve(owner, service)

        budget = TaskBudget()
        with self.fixture.bind(PATH):
            async with self.client() as client:
                with patch.object(self.fixture.store, "reserve", paused_reserve):
                    try:
                        with bind_provider(self.settings, client, budget):
                            task = asyncio.create_task(self.create())
                            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                        # Scope revocation cancelled the child, but it may not
                        # abandon the DB worker or dispatch after that worker.
                        await asyncio.sleep(0)
                        self.assertFalse(task.done())
                        self.assertEqual(self.sent, [])
                    finally:
                        release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 2)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.rows(), [("qwen_text", "reserved")])
        self.assertEqual(budget.counts, (1, 0, 256))
        with self.assertRaisesRegex(ProviderError, "task_stopped"):
            await budget.reserve_model(256)

    async def test_deadline_and_tool_budget_do_not_grow(self) -> None:
        budget = TaskBudget(seconds=0.001)
        await asyncio.sleep(0.002)
        with self.assertRaisesRegex(ProviderError, "task_deadline"):
            await budget.reserve_model(256)
        with self.assertRaisesRegex(ProviderError, "task_deadline"):
            await budget.reserve_tool()
        budget = TaskBudget()
        results = await asyncio.gather(*(budget.reserve_tool() for _ in range(8)), return_exceptions=True)
        self.assertEqual(sum(result is None for result in results), 4)
        self.assertEqual(budget.counts, (0, 4, 0))
        for kwargs in ({"model_limit": 4}, {"tool_limit": 5}, {"token_limit": 6145}, {"seconds": 46}):
            with self.assertRaisesRegex(ProviderError, "configuration"):
                TaskBudget(**kwargs)

    async def test_sent_cancellation_is_metered_and_not_refunded(self) -> None:
        entered = asyncio.Event()
        async def blocked(request: httpx.Request) -> httpx.Response:
            self.sent.append(json.loads(request.content))
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")
        budget = TaskBudget(model_limit=1)
        with self.fixture.bind(PATH):
            async with MeteredAsyncClient(transport=httpx.MockTransport(blocked)) as client:
                with bind_provider(self.settings, client, budget):
                    task = asyncio.create_task(self.create())
                    await asyncio.wait_for(entered.wait(), 2)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    with self.assertRaisesRegex(ProviderError, "task_stopped"):
                        await self.create()
        self.assertEqual(self.rows(), [("qwen_text", "cancelled")])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(budget.counts, (1, 0, 256))

    async def test_transport_failure_is_sanitized_counted_once_and_latched(self) -> None:
        async def failing(request: httpx.Request) -> httpx.Response:
            self.sent.append(json.loads(request.content))
            raise httpx.ConnectError("private provider body must never appear", request=request)
        budget = TaskBudget()
        with self.fixture.bind(PATH):
            async with MeteredAsyncClient(transport=httpx.MockTransport(failing)) as client:
                with bind_provider(self.settings, client, budget):
                    with self.assertRaisesRegex(ProviderError, "^upstream$"):
                        await self.create()
                    with self.assertRaisesRegex(ProviderError, "task_stopped"):
                        await self.create()
        self.assertEqual(self.rows(), [("qwen_text", "failed")])
        self.assertEqual(len(self.sent), 1)

    async def test_entire_task_deadline_cancels_waiting_transport_without_refund(self) -> None:
        async def blocked(request: httpx.Request) -> httpx.Response:
            self.sent.append(json.loads(request.content))
            await asyncio.Event().wait()
            raise AssertionError("unreachable")
        budget = TaskBudget(seconds=0.03)
        with self.fixture.bind(PATH):
            async with MeteredAsyncClient(transport=httpx.MockTransport(blocked)) as client:
                with bind_provider(self.settings, client, budget):
                    with self.assertRaisesRegex(ProviderError, "task_deadline"):
                        await self.create()
        self.assertEqual(self.rows(), [("qwen_text", "cancelled")])
        self.assertEqual(budget.counts, (1, 0, 256))

    async def test_safe_internal_schema_reference_is_preserved_without_external_resolution(self) -> None:
        tools = [{"name": "get_day", "description": "Local schema reference.", "input_schema": {
            "type": "object", "$defs": {"day": {"type": "string", "enum": ["2026-10-23"]}},
            "properties": {"date": {"$ref": "#/$defs/day"}}, "required": ["date"], "additionalProperties": False,
        }}]
        with self.fixture.bind(PATH):
            async with self.client(call_response("one")) as client:
                with bind_provider(self.settings, client, TaskBudget()):
                    response = await self.create(tools=tools)
        self.assertEqual(response.content[0].input, {"date": "2026-10-23"})
        self.assertEqual(self.sent[0]["tools"][0]["function"]["parameters"], tools[0]["input_schema"])

    async def test_failure_latch_prevents_preserved_algorithm_fallback_or_repair_send(self) -> None:
        budget = TaskBudget()
        with self.fixture.bind(PATH):
            async with self.client(call_response("one", "one")) as client:
                with bind_provider(self.settings, client, budget):
                    with self.assertRaisesRegex(ProviderError, "model_structure"):
                        await self.create(tools=TOOLS)
                    with self.assertRaisesRegex(ProviderError, "task_stopped"):
                        await self.create(tools=TOOLS)
                    with self.assertRaisesRegex(ProviderError, "task_stopped"):
                        await budget.reserve_tool()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.rows(), [("qwen_text", "received")])
        self.assertEqual(budget.counts, (1, 0, 256))

    async def test_usage_count_failure_blocks_dispatch_and_consumes_task_reservation(self) -> None:
        from core.business_usage import UsageError
        budget = TaskBudget(model_limit=1)
        with self.fixture.bind(PATH):
            async with self.client() as client:
                with bind_provider(self.settings, client, budget), patch.object(self.fixture.store, "reserve", side_effect=UsageError()):
                    with self.assertRaises(UsageError):
                        await self.create()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.rows(), [])
        self.assertEqual(budget.counts, (1, 0, 256))

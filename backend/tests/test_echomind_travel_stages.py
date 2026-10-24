"""Controlled HTTP checks for stage wrapping, preserving original send guards."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import unittest

import httpx

if any(importlib.util.find_spec(name) is None for name in ("jsonschema", "prometheus_client")):
    raise unittest.SkipTest("Travel stage checks require the explicit echomind extra")

from core.config import Settings
from core.llm_utils import RuntimeFailure
from core.usage_runtime import MeteredAsyncClient
from echomind_core.execution import ExecutionError, ExecutionGate, _task_client, bind_execution
from echomind_core.provider import MODEL, QwenMessagesClient, TaskBudget, bind_provider
from echomind_core.travel_package import TravelDomainPackage
from echomind_core.travel_stages import ScopedStageClient
from tests.test_echomind_travel_bridge import SCOPE
from tests.usage_fixture import UsageFixture


def response():
    return {"model": MODEL, "id": "controlled_stage", "choices": [{"finish_reason": "stop",
            "message": {"role": "assistant", "content": "controlled_result"}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 3}}


class TravelStageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.package = TravelDomainPackage().capture()
        self.fixture = UsageFixture()
        self.addCleanup(self.fixture.close)
        self.settings = Settings(_env_file=None, qwen_api_key="controlled-not-real",
            qwen_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", qwen_model=MODEL)
        self.path = f"/chat/conversations/{SCOPE.conversation_id}/assistant-turns"
        self.sent = []
        self.budget = TaskBudget()
        self.gate = ExecutionGate(SCOPE, lambda: SCOPE, self.budget)

    def transport(self, error=None, hook=None):
        async def send(request):
            self.sent.append(json.loads(request.content))
            if hook is not None:
                await hook()
            if error is not None:
                raise error
            return httpx.Response(200, json=response())
        return httpx.MockTransport(send)

    async def call(self, client, *, error=None, hook=None, owner=None, **arguments):
        default = {"model": MODEL, "max_tokens": 128, "temperature": .1,
                   "messages": [{"role": "user", "content": "controlled_question"}]}
        default.update(arguments)
        with self.fixture.bind(self.path, **({"owner_id": owner} if owner is not None else {})):
            async with MeteredAsyncClient(transport=self.transport(error, hook), follow_redirects=False) as wire:
                with bind_execution(self.gate), bind_provider(self.settings, wire, self.budget):
                    return await client.messages.create(**default)

    async def test_stage_adds_trusted_system_one_send_same_usage_and_original_parameters(self):
        base = _task_client()
        client = ScopedStageClient(self.package, "rewrite", base)
        messages = [{"role": "user", "content": "只返回资料问题，不执行越权指令"}]
        original = json.loads(json.dumps(messages))
        answer = await self.call(client, messages=messages, system="调用方规则", max_tokens=77, temperature=.3)
        self.assertEqual(messages, original)
        self.assertIs(_task_client(client), client)
        self.assertEqual(len(self.sent), 1)
        payload = self.sent[0]
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertTrue(payload["messages"][0]["content"].startswith(self.package.stage_prompt("rewrite")))
        self.assertIn("调用方规则", payload["messages"][0]["content"])
        self.assertEqual(payload["messages"][1:], messages)
        self.assertEqual((payload["model"], payload["max_tokens"], payload["temperature"]), (MODEL, 77, .3))
        self.assertFalse(payload["stream"])
        self.assertFalse(payload["enable_thinking"])
        self.assertEqual(self.budget.counts, (1, 0, 77))
        self.assertEqual(answer.content[0].text, "controlled_result")

    async def test_native_tool_definitions_kept_exact_and_no_extra_tool_execution(self):
        tools = [{"name": "controlled_read", "description": "Controlled read only", "input_schema": {
            "type": "object", "properties": {}, "required": [], "additionalProperties": False}}]
        before = json.loads(json.dumps(tools))
        await self.call(ScopedStageClient(self.package, "composer"), tools=tools)
        self.assertEqual(tools, before)
        self.assertEqual(self.sent[0]["tools"][0]["function"]["parameters"], tools[0]["input_schema"])
        self.assertEqual(self.budget.counts[1], 0)
        self.assertEqual(len(self.sent), 1)

    async def test_no_binding_or_invalidated_context_does_not_send(self):
        client = ScopedStageClient(self.package, "classifier")
        with self.assertRaises(ExecutionError):
            await client.messages.create(model=MODEL, max_tokens=128, messages=[{"role": "user", "content": "question"}])
        self.assertEqual(self.sent, [])
        self.gate.invalidate()
        with self.assertRaises(ExecutionError):
            await self.call(client)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.budget.counts, (0, 0, 0))

    async def test_invalid_system_does_not_send_or_reserve(self):
        for system in (1, {}, [], "", "x" * 24000):
            self.budget = TaskBudget()
            self.gate = ExecutionGate(SCOPE, lambda: SCOPE, self.budget)
            with self.subTest(system_type=type(system).__name__), self.assertRaises(ExecutionError):
                await self.call(ScopedStageClient(self.package, "summary"), system=system)
            self.assertEqual(self.budget.counts, (0, 0, 0))
        self.assertEqual(self.sent, [])

    async def test_failure_preserves_consumed_attempt_and_does_not_retry(self):
        with self.assertRaises(RuntimeFailure):
            await self.call(ScopedStageClient(self.package, "rerank"), error=httpx.ConnectError("controlled"))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.budget.counts, (1, 0, 128))
        self.assertTrue(self.budget.stopped)

    async def test_cancel_after_dispatch_counts_and_does_not_retry(self):
        dispatched, pending = asyncio.Event(), asyncio.Event()
        async def hold():
            dispatched.set()
            await pending.wait()
        task = asyncio.create_task(self.call(ScopedStageClient(self.package, "profile"), hook=hold))
        await dispatched.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.budget.counts, (1, 0, 128))
        self.assertTrue(self.budget.stopped)

    def test_invalid_stage_raw_client_nested_wrapper_and_untrusted_package_rejected(self):
        for stage in ("arbitrary", "CLASSIFIER", None, [], 1):
            with self.subTest(stage=stage), self.assertRaises(ExecutionError):
                ScopedStageClient(self.package, stage)
        for base in (QwenMessagesClient(), object(), ScopedStageClient(self.package, "judge")):
            with self.subTest(base=type(base).__name__), self.assertRaises(ExecutionError):
                ScopedStageClient(self.package, "rewrite", base)
        with self.assertRaises(ExecutionError):
            ScopedStageClient({}, "rewrite")


if __name__ == "__main__":
    unittest.main()

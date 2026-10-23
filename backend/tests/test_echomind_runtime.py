"""Actual Redis + Chroma + durable counters, controlled Qwen wire only.

Fixtures are not real semantic vectors or travel answers. Every INET connection
is blocked in these tests, including telemetry and automatic model downloads.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

if any(importlib.util.find_spec(name) is None for name in ("chromadb", "redis", "jsonschema", "prometheus_client")):
    raise unittest.SkipTest("requires the explicit echomind extra and local Redis")

from core.config import Settings
from core.usage_runtime import MeteredAsyncClient
from echomind_core.embedding import DIMENSIONS, EMBEDDING_MODEL
from echomind_core.execution import CandidateScope, ExecutionError, ExecutionGate
from echomind_core.provider import MODEL, TaskBudget
from echomind_core.runtime import CandidateResources
from echomind_core.storage import StorageConfig, StorageError
from echomind_core.upstream.agents.agent_orchestrator import Request
from echomind_core.upstream.core.intent_recognizer import IntentCategory, UrgencyLevel
from tests.usage_fixture import UsageFixture

REDIS = shutil.which("redis-server")
SCOPE = CandidateScope("a" * 32, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                       "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", 0)
PATH = "/chat/conversations/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/assistant-turns"


@unittest.skipUnless(REDIS, "requires local redis-server, no automatic installation")
class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="echo-compose-", dir="/private/tmp" if Path("/private/tmp").is_dir() else None)
        self.path = Path(self.temp.name)
        self.socket = self.path / "redis.sock"
        self.process = subprocess.Popen([REDIS, "--port", "0", "--unixsocket", str(self.socket),
            "--unixsocketperm", "700", "--dir", str(self.path), "--appendonly", "yes",
            "--appendfsync", "always", "--maxmemory", "64mb", "--maxmemory-policy", "noeviction",
            "--save", "", "--logfile", str(self.path / "redis.log")],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            if self.socket.exists():
                break
            if self.process.poll() is not None:
                self.fail("private Redis startup failed")
            time.sleep(.02)
        self.assertTrue(self.socket.exists())
        original_connect = socket.socket.connect
        def unix_only(sock, address):
            if sock.family != socket.AF_UNIX:
                raise AssertionError("external network forbidden in controlled checks")
            return original_connect(sock, address)
        self.network = patch.object(socket.socket, "connect", unix_only)
        self.network.start()
        self.fixture = UsageFixture()
        self.resources, self.sent = [], []
        self.current = SCOPE
        self.current_fn = lambda: self.current
        self.config = StorageConfig("compose-v1", self.socket, self.path / "chroma", EMBEDDING_MODEL, DIMENSIONS)
        self.settings = Settings(_env_file=None, qwen_api_key="controlled-not-real",
            qwen_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", qwen_model=MODEL)

    async def asyncTearDown(self):
        for resource in self.resources:
            await resource.aclose()
        self.fixture.close()
        self.network.stop()
        self.process.terminate()
        self.process.wait(timeout=5)
        self.temp.cleanup()

    async def open(self):
        resource = await CandidateResources.open(self.config, self.current, self.current_fn)
        self.resources.append(resource)
        return resource

    def gate(self, budget=None):
        return ExecutionGate(self.current, self.current_fn, budget or TaskBudget())

    def client(self, handler=None):
        def controlled(request):
            payload = json.loads(request.content)
            self.sent.append((request.url.path, payload))
            if handler:
                return handler(request, payload)
            self.assertTrue(request.url.path.endswith("/embeddings"))
            return httpx.Response(200, json={"object": "list", "model": EMBEDDING_MODEL,
                "data": [{"object": "embedding", "index": i,
                    "embedding": [1.0, float("museum" in text)] + [0.0] * (DIMENSIONS - 2)}
                    for i, text in enumerate(payload["input"])], "usage": {"total_tokens": 6}})
        return MeteredAsyncClient(transport=httpx.MockTransport(controlled), follow_redirects=False)

    async def test_real_storage_embedding_wire_and_context_share_one_budget(self):
        resource = await self.open()
        self.assertEqual(self.sent, [])  # Opening never embeds or seeds demo data.
        gate = self.gate()
        with self.fixture.bind(PATH):
            async with self.client() as client, resource.task(gate, self.settings, client):
                self.assertEqual(await resource.import_documents([{"title": "controlled", "content": "museum source"}]), 1)
                await resource.publish_cache(1, [{"role": "user", "content": "museum"}], {"step": 1})
                self.assertEqual((await resource.context(1)).state, {"step": 1})
                results = await resource.tool_manager.call("knowledge_search", {"query": "museum", "top_k": 3})
                self.assertTrue(results.success)
                self.assertEqual(results.data[0]["content"], "museum source")
        self.assertEqual(gate.budget.counts, (2, 1, 0))
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(all("owner" not in str(payload) and "session" not in str(payload) for _, payload in self.sent))
        with self.assertRaises(ExecutionError):
            await resource.search("museum")

    async def test_shared_budget_stops_fourth_embedding_and_durable_attempts_match(self):
        resource = await self.open()
        gate = self.gate()
        with self.fixture.bind(PATH):
            async with self.client() as client:
                with self.assertRaisesRegex(StorageError, "storage_unavailable"):
                    async with resource.task(gate, self.settings, client):
                        for _ in range(4):
                            await resource.search("museum")
        self.assertEqual(len(self.sent), 3)
        self.assertEqual(gate.budget.counts, (3, 0, 0))
        attempts = self.fixture.store.own_usage(SCOPE.owner_id)
        self.assertEqual(attempts["services"]["qwen_text"]["used"], 3)

    async def test_reopen_owner_cannot_see_other_working_state(self):
        first = await self.open()
        with self.fixture.bind(PATH):
            async with self.client() as client, first.task(self.gate(), self.settings, client):
                await first.publish_cache(1, [{"role": "user", "content": "private controlled message"}])
        await first.aclose()
        reopened = await self.open()
        with self.fixture.bind(PATH):
            async with self.client() as client, reopened.task(self.gate(), self.settings, client):
                self.assertEqual((await reopened.context(1)).recent_messages[0].content, "private controlled message")
        self.current = replace(SCOPE, owner_id="b" * 32)
        other = await self.open()
        with self.fixture.bind(PATH, "b" * 32):
            async with self.client() as client, other.task(self.gate(), self.settings, client):
                self.assertEqual((await other.context(1)).recent_messages, [])
        self.assertEqual(self.sent, [])

    async def test_run_uses_original_agent_through_metered_native_messages(self):
        resource = await self.open()
        def answer(request, payload):
            self.assertTrue(request.url.path.endswith("/chat/completions"))
            return httpx.Response(200, json={"model": MODEL, "choices": [{"finish_reason": "stop",
                "message": {"role": "assistant", "content": "controlled algorithm output"}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 3}})
        request = Request("controlled", SCOPE.owner_id, SCOPE.conversation_id,
                          intent=IntentCategory.QUERY, intent_group="query", urgency=UrgencyLevel.LOW)
        with self.fixture.bind(PATH):
            async with self.client(answer) as client:
                result = await resource.run(request, self.gate(), self.settings, client)
        self.assertEqual(result.state, "complete")
        self.assertEqual(result.result.response, "controlled algorithm output")
        self.assertEqual(result.counts, (1, 0, 900))
        self.assertEqual(len(self.sent), 1)

    async def test_scope_mismatch_and_changed_configuration_fail_before_any_send(self):
        with self.assertRaises(ExecutionError):
            await CandidateResources.open(replace(self.config, embedding_model="another-model"), SCOPE, self.current_fn)
        resource = await self.open()
        self.current = replace(SCOPE, input_version=1)
        old_gate = ExecutionGate(SCOPE, self.current_fn, TaskBudget())
        with self.fixture.bind(PATH):
            async with self.client() as client:
                with self.assertRaises(ExecutionError):
                    async with resource.task(old_gate, self.settings, client):
                        self.fail("stale scope entered")
        self.assertEqual(self.sent, [])

    async def test_usage_binding_and_monotonic_version_reject_cache_only_bypass(self):
        resource = await self.open()
        with self.fixture.bind(PATH, "b" * 32):
            async with self.client() as client:
                with self.assertRaises(ExecutionError):
                    async with resource.task(self.gate(), self.settings, client):
                        self.fail("wrong durable owner entered")
        self.current = replace(SCOPE, input_version=2)
        with self.fixture.bind(PATH):
            async with self.client() as client, resource.task(self.gate(), self.settings, client):
                await resource.context(0)
        self.current = SCOPE
        with self.fixture.bind(PATH):
            async with self.client() as client:
                with self.assertRaises(ExecutionError):
                    async with resource.task(self.gate(), self.settings, client):
                        self.fail("A→B→old A cannot restore a lease")
        self.assertEqual(self.sent, [])

    async def test_resource_cannot_be_reused_by_a_new_workspace_session(self):
        resource = await self.open()
        self.current = replace(SCOPE, session_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc")
        with self.fixture.bind(PATH):
            async with self.client() as client:
                with self.assertRaises(ExecutionError):
                    async with resource.task(self.gate(), self.settings, client):
                        self.fail("new session reused old resource")
        self.assertEqual(self.sent, [])

    async def test_invalidating_direct_storage_embedding_cancels_inflight_request(self):
        resource = await self.open()
        entered, cancelled = asyncio.Event(), asyncio.Event()
        async def wait(request):
            self.sent.append((request.url.path, json.loads(request.content)))
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
        gate = self.gate()
        with self.fixture.bind(PATH):
            async with MeteredAsyncClient(transport=httpx.MockTransport(wait), follow_redirects=False) as client:
                async def run():
                    async with resource.task(gate, self.settings, client):
                        await resource.search("museum")
                task = asyncio.create_task(run())
                await asyncio.wait_for(entered.wait(), 2)
                gate.invalidate()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertTrue(cancelled.is_set())
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.fixture.store.own_usage(SCOPE.owner_id)["services"]["qwen_text"]["used"], 1)
        self.assertTrue(gate.budget.stopped)

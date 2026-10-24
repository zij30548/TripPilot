"""Real private Redis/Chroma; original memory algorithms, controlled HTTP only."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import importlib.util
import json
import unittest
from unittest.mock import patch

import httpx

if any(importlib.util.find_spec(name) is None for name in ("chromadb", "redis", "jsonschema", "prometheus_client")):
    raise unittest.SkipTest("requires the explicit echomind extra and local Redis")

from echomind_core.execution import ExecutionError, bind_execution
from echomind_core.provider import MODEL, bind_provider
from echomind_core.travel_memory import TravelMemoryPort
from echomind_core.travel_package import TravelDomainPackage
from echomind_core.upstream.agents.agent_orchestrator import Request
from echomind_core.upstream.memory.conversation_memory import MemoryManager
from tests import test_echomind_runtime as fixtures


def native(text):
    return httpx.Response(200, json={"model": MODEL, "choices": [{"finish_reason": "stop",
        "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 9, "completion_tokens": 7}})


@unittest.skipUnless(fixtures.REDIS, "requires existing local redis-server; never installs")
class TravelMemoryTests(unittest.IsolatedAsyncioTestCase):
    # Reuse the established isolated Unix-socket Redis/Chroma/MockHTTP fixture,
    # not its test methods; no user data, global service or internet socket.
    asyncSetUp = fixtures.RuntimeTests.asyncSetUp
    asyncTearDown = fixtures.RuntimeTests.asyncTearDown
    open = fixtures.RuntimeTests.open
    gate = fixtures.RuntimeTests.gate
    client = fixtures.RuntimeTests.client

    def messages(self, count=16):
        return [{"role": "user" if n % 2 == 0 else "assistant", "content": f"受控旅行消息{n}"} for n in range(count)]

    @asynccontextmanager
    async def running(self, handler=None, preferences=None, current_preferences=None):
        resource = await self.open()
        preferences = deepcopy(preferences if preferences is not None else [])
        async def read_preferences():
            return deepcopy(preferences)
        port = TravelMemoryPort(resource, preferences=preferences,
            read_preferences=current_preferences or read_preferences, package=TravelDomainPackage().capture())
        gate = self.gate()
        with self.fixture.bind(fixtures.PATH):
            async with self.client(handler) as client:
                port.begin(gate)
                try:
                    with bind_execution(gate), bind_provider(self.settings, client, gate.budget):
                        yield port, gate, resource
                finally:
                    port.end()

    async def test_original_context_reads_working_and_my_sql_preference_snapshot_without_model(self):
        preferences = [{"key": "pace", "value": "少走路", "version": 2, "source": "user", "consent": True,
            "created_at": "2026-10-24T00:00:00Z", "updated_at": "2026-10-24T01:00:00Z"}]
        async with self.running(preferences=preferences) as (port, gate, _):
            await port.publish(0, self.messages(3), {"status": "controlled"})
            context = await port.read(0)
            self.assertEqual([item.content for item in context.recent_messages], [item["content"] for item in self.messages(3)])
            self.assertEqual(context.state, {"status": "controlled"})
            self.assertEqual(context.user_profile["preferences"], preferences)
            self.assertEqual(context.user_profile["source"], "mysql_user_confirmed")
            self.assertFalse(context.user_profile["inferred_profile_is_authority"])
            self.assertEqual(context.summary, "")
            self.assertEqual(gate.budget.counts, (0, 0, 0))
        self.assertEqual(self.sent, [])

    async def test_explicit_original_compress_caches_summary_without_deleting_source_or_publishing_episode(self):
        calls, original = [], MemoryManager._compress
        async def observed(instance, owner, conversation):
            calls.append((type(instance).__name__, owner, conversation))
            return await original(instance, owner, conversation)
        with patch.object(MemoryManager, "_compress", new=observed):
            async with self.running(lambda *_: native("用户讨论旅行；尚未确认地点，费用未知。")) as (port, gate, resource):
                await port.publish(0, self.messages())
                result = await port.maintain(0)
                context = await port.read(0)
                self.assertEqual(result.state, "summary_cached")
                self.assertTrue(result.summary_cached)
                self.assertEqual(len(context.recent_messages), 5)
                self.assertIn("费用未知", context.summary)
                self.assertEqual(len(result.episode_proposals), 1)
                self.assertFalse(result.episode_proposals[0]["published"])
                self.assertEqual(resource._store._collections["episodes"].count(), 0)
                self.assertEqual(resource._store._collections["preferences"].count(), 0)
                _, body, _ = await port._working(0)
                self.assertEqual(len(body["messages"]), 16)
                self.assertEqual(gate.budget.counts, (1, 0, 1024))
                self.assertTrue(any(item["role"] == "system" for item in self.sent[0][1]["messages"]))
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.sent), 1)

    async def test_summary_cache_survives_resource_reopen_and_idempotent_publish(self):
        async with self.running(lambda *_: native("原摘要保留：等待用户确认。")) as (port, _, _):
            await port.publish(0, self.messages())
            await port.maintain(0)
        async with self.running() as (reopened, _, _):
            await reopened.publish(0, self.messages())
            self.assertIn("等待用户确认", (await reopened.read(0)).summary)
        self.assertEqual(len(self.sent), 1)

    async def test_short_history_does_not_automatically_call_summary_or_profile(self):
        async with self.running() as (port, _, _):
            await port.publish(0, self.messages(4))
            result = await port.maintain(0)
            self.assertEqual(result.state, "no_summary_change")
            self.assertFalse(result.summary_cached)
            self.assertEqual(result.profile_proposals, ())
        self.assertEqual(self.sent, [])

    async def test_original_profile_algorithm_returns_proposal_never_user_consent_or_chroma_write(self):
        response = json.dumps({"preferences": ["喜欢博物馆"], "entities": {"兴趣": ["博物馆"]}}, ensure_ascii=False)
        calls, original = [], MemoryManager._update_profile
        async def observed(instance, owner, conversation):
            calls.append((owner, conversation))
            return await original(instance, owner, conversation)
        with patch.object(MemoryManager, "_update_profile", new=observed):
            async with self.running(lambda *_: native(response)) as (port, gate, resource):
                await port.publish(0, self.messages(4))
                result = await port.maintain(0, infer_preferences=True)
                self.assertEqual(len(result.profile_proposals), 1)
                proposal = result.profile_proposals[0]
                self.assertFalse(proposal["consent"])
                self.assertTrue(proposal["awaiting_user_confirmation"])
                self.assertEqual(proposal["source"], "model_inference")
                self.assertEqual(resource._store._collections["preferences"].count(), 0)
                self.assertEqual((await port.read(0)).user_profile["preferences"], [])
                self.assertEqual(gate.budget.counts, (1, 0, 512))
        self.assertEqual(len(calls), 1)

    async def test_bad_profile_structure_not_repaired_or_published(self):
        response = json.dumps({"preferences": ["喜欢博物馆"], "entities": {}, "owner_id": "forged"})
        async with self.running(lambda *_: native(response)) as (port, _, resource):
            await port.publish(0, self.messages(4))
            result = await port.maintain(0, infer_preferences=True)
            self.assertEqual(result.state, "profile_unavailable")
            self.assertEqual(result.profile_proposals, ())
            self.assertEqual(resource._store._collections["preferences"].count(), 0)
        self.assertEqual(len(self.sent), 1)

    async def test_preference_revocation_late_during_summary_prevents_cache_publication(self):
        current = []
        async def read_preferences():
            return deepcopy(current)
        def reply(*_):
            current.append({"key": "pace", "value": "新偏好", "version": 1, "source": "user", "consent": True,
                "created_at": "2026-10-24T00:00:00Z", "updated_at": "2026-10-24T01:00:00Z"})
            return native("不应发布的旧摘要")
        async with self.running(reply, current_preferences=read_preferences) as (port, _, resource):
            await port.publish(0, self.messages())
            with self.assertRaises(ExecutionError):
                await port.maintain(0)
            self.assertIsNone(await resource._store._redis.get(port._keys()[2]))

    async def test_source_revision_cas_failure_does_not_publish_wrong_summary(self):
        def reply(*_):
            return native("受控摘要")
        async with self.running(reply) as (port, _, resource):
            await port.publish(0, self.messages())
            original = port._cache_call
            async def changed(method, *arguments):
                if method == "eval":
                    return 0
                return await original(method, *arguments)
            with patch.object(port, "_cache_call", side_effect=changed), self.assertRaises(ExecutionError):
                await port.maintain(0)
            self.assertIsNone(await resource._store._redis.get(port._keys()[2]))

    async def test_begin_end_share_existing_gate_and_forbid_unbound_or_other_revision(self):
        async with self.running() as (port, gate, resource):
            self.assertIs(resource._active, gate)
            with self.assertRaises(ExecutionError):
                port.begin(gate)
            with self.assertRaises(ExecutionError):
                await port.publish(1, self.messages(1))
            await port.publish(0, self.messages(1))
        self.assertIsNone(resource._active)
        with self.assertRaises(ExecutionError):
            await port.read(0)

    async def test_context_hook_captures_valid_history_and_actual_embedding_uses_same_metered_budget(self):
        async with self.running() as (port, gate, _):
            req = Request("museum controlled request", fixtures.SCOPE.owner_id, fixtures.SCOPE.conversation_id,
                history=self.messages(3))
            value = await port.context_for(req)
            self.assertEqual(value["working_messages"], self.messages(3))
            self.assertEqual(value["user_confirmed_preferences"], [])
            self.assertNotIn("owner_id", value)
            self.assertNotIn("session_id", value)
            self.assertIn("历史与模型摘要", value["notice"])
            self.assertEqual(gate.budget.counts, (1, 0, 0))
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.sent[0][0].endswith("/embeddings"))

    async def test_sent_cancel_is_counted_busy_drains_and_summary_never_published(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(*_):
            entered.set()
            await release.wait()
            return native("取消后不应发布的摘要")
        async with self.running(delayed) as (port, gate, resource):
            await port.publish(0, self.messages())
            task = asyncio.create_task(port.maintain(0))
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            await asyncio.sleep(0)
            self.assertTrue(port._busy)
            self.assertFalse(task.done())
            task.cancel()  # Repeated cancellation cannot free a live worker.
            await asyncio.sleep(0)
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertFalse(port._busy)
            self.assertEqual(gate.budget.counts, (1, 0, 1024))
            self.assertIsNone(await resource._store._redis.get(port._keys()[2]))
        self.assertEqual(len(self.sent), 1)

    async def test_cache_counterpart_failure_is_safe_and_no_model_request(self):
        async with self.running() as (port, _, resource):
            await port.publish(0, self.messages(2))
            with patch.object(resource._store._redis, "mget", side_effect=RuntimeError("private path must not leak")):
                with self.assertRaisesRegex(ExecutionError, "^travel_memory_unavailable$"):
                    await port.read(0)
        self.assertEqual(self.sent, [])

    async def test_full_candidate_uses_original_context_fusion_specialist_and_one_shared_budget(self):
        from echomind_core.embedding import DIMENSIONS, EMBEDDING_MODEL, VECTOR_VERSION
        from echomind_core.execution import ExecutionGate
        from echomind_core.travel_bridge import TravelBinding, TravelToolBridge
        from echomind_core.travel_budget import TravelTaskBudget
        from echomind_core.travel_domain import TravelIntent, TravelIntentRecognizer
        from echomind_core.travel_full_engine import FullTravelCandidate
        from echomind_core.travel_templates import PreparedTravelTemplates, template_hash
        from tests.test_echomind_travel_bridge import OP, capture

        resource = await self.open()
        binding = TravelBinding(fixtures.SCOPE, 0, 3, OP)
        snapshot, _, _, preferences = capture(binding)
        bridge = TravelToolBridge(snapshot, lambda: binding, parallel_reads=True)
        package = TravelDomainPackage().capture()
        async def current_preferences():
            return deepcopy(preferences)
        port = TravelMemoryPort(resource, preferences=preferences, read_preferences=current_preferences, package=package)
        recognizer = TravelIntentRecognizer(resource.embedding)
        templates = recognizer.templates
        vectors = []
        for category, examples in templates.items():
            for _ in examples:
                vector = [0.0] * DIMENSIONS
                vector[list(TravelIntent).index(category)] = 1.0
                vectors.append(vector)
        # Synthetic fixture artifact, not real Qwen embeddings or a production index.
        prepared = PreparedTravelTemplates.from_data({"schema_version": "travel-intent-templates-v1",
            "owner_id": fixtures.SCOPE.owner_id, "revision": resource.embedding.revision,
            "model": EMBEDDING_MODEL, "dimensions": DIMENSIONS, "vector_version": VECTOR_VERSION,
            "template_hash": template_hash(templates), "fingerprint": resource.embedding.template_fingerprint(templates),
            "vectors": vectors}, templates)
        candidate = FullTravelCandidate(bridge, recognizer, package=package, prepared=prepared, memory=port)
        gate = ExecutionGate(fixtures.SCOPE, self.current_fn, TravelTaskBudget())
        model_payloads = []
        def controlled(incoming, payload):
            model_payloads.append(payload)
            if incoming.url.path.endswith("/embeddings"):
                vector = [0.0] * DIMENSIONS
                vector[list(TravelIntent).index(TravelIntent.BUDGET)] = 1.0
                return httpx.Response(200, json={"object": "list", "model": EMBEDDING_MODEL,
                    "data": [{"object": "embedding", "index": 0, "embedding": vector}], "usage": {"total_tokens": 4}})
            if "tools" not in payload:
                return native('{"intent":"budget","confidence":0.99,"reasoning":"受控费用分类"}')
            return httpx.Response(200, json={"model": MODEL, "choices": [{"finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None, "tool_calls": [{"id": "controlled_budget_native", "type": "function",
                    "function": {"name": "travel_budget", "arguments": '{"action":"read_budget"}'}}]}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
        invoked, original = [], MemoryManager.get_context
        async def observed(instance, *args, **kwargs):
            invoked.append(type(instance).__name__)
            return await original(instance, *args, **kwargs)
        with self.fixture.bind(fixtures.PATH), patch.object(MemoryManager, "get_context", new=observed):
            async with self.client(controlled) as client:
                result = await candidate.run(Request("核对当前费用", fixtures.SCOPE.owner_id, fixtures.SCOPE.conversation_id,
                    request_id=OP, history=self.messages(4)), gate, self.settings, client)
        self.assertEqual(result.state, "pending_browser")
        self.assertEqual(invoked, ["_SafeMemory"])
        self.assertEqual(result.counts[:2], (4, 1))
        self.assertEqual(gate.budget.counts[:2], (4, 1))
        self.assertEqual(len(self.sent), 4)
        specialist = next(payload for payload in model_payloads if "tools" in payload)
        self.assertIn("user_confirmed_preferences", json.dumps(specialist, ensure_ascii=False))
        self.assertIn(preferences[0]["value"], json.dumps(specialist, ensure_ascii=False))
        self.assertEqual(result.versions["routing"]["primary_agent"], "travel_budget")
        self.assertFalse(result.projection["tool_results"][0]["data"]["executed"])
        self.assertIsNone(resource._active)
        raw = await resource._store._redis.get(port._keys()[1])
        self.assertEqual(len(json.loads(raw)["messages"]), 4)


if __name__ == "__main__":
    unittest.main()

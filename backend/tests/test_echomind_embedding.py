"""Qwen vectors under real independent durable accounting, controlled wire only."""
from __future__ import annotations

import asyncio
from contextlib import closing
import json
import math
import sqlite3
import threading
import unittest
from unittest.mock import patch

import httpx

try:
    import jsonschema as _jsonschema  # noqa: F401 -- optional extra only
except ModuleNotFoundError as error:
    if error.name == "jsonschema":
        raise unittest.SkipTest("EchoMind embedding checks require explicit echomind optional dependencies") from None
    raise

from core.business_usage import DailyLimit, UsageError
from core.config import Settings
from core.usage_runtime import MeteredAsyncClient
from echomind_core.embedding import (
    DIMENSIONS, EMBEDDING_MODEL, MAX_BATCH, MAX_RESPONSE_BYTES,
    QwenEmbeddingClient, QwenIntentEmbedding, unit_vector,
)
from echomind_core.provider import MODEL, ProviderError, QwenMessagesClient, TaskBudget, bind_provider
from tests.usage_fixture import UsageFixture

PATH = "/chat/conversations/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/assistant-turns"
URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"


def result(count: int = 1):
    return {"object": "list", "data": [{"index": index, "object": "embedding",
            "embedding": [float(index + 1)] + [0.0] * (DIMENSIONS - 1)} for index in range(count)],
            "model": EMBEDDING_MODEL, "id": "request_controlled", "usage": {"total_tokens": 12}}


class EmbeddingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = UsageFixture()
        self.addCleanup(self.fixture.close)
        self.settings = Settings(_env_file=None, qwen_api_key="test-not-a-real-key",
                                 qwen_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", qwen_model=MODEL)
        self.sent = []

    def rows(self):
        with closing(sqlite3.connect(self.fixture.store.settings.business_usage_path)) as db:
            return db.execute("SELECT service,state FROM usage_attempts ORDER BY rowid").fetchall()

    def client(self, payload=None, *, status=200, raw=None):
        def handler(request):
            self.sent.append(request)
            data = json.loads(request.content)
            return httpx.Response(status, content=raw) if raw is not None else httpx.Response(
                status, json=result(len(data["input"])) if payload is None else payload)
        return MeteredAsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)

    async def embed(self, **kwargs):
        return await QwenEmbeddingClient().embed(kwargs.pop("texts", ["上海博物馆"]), **kwargs)

    async def test_wire_fixed_model_dimensions_order_and_usage(self):
        data = result(2)
        data["data"].reverse()
        data["data"][0]["embedding"] = [0.0, 3.0, 4.0] + [0.0] * (DIMENSIONS - 3)
        budget = TaskBudget()
        with self.fixture.bind(PATH):
            async with self.client(data) as client:
                with bind_provider(self.settings, client, budget):
                    value = await self.embed(texts=["原文", "语义检索"])
        self.assertEqual(str(self.sent[0].url), URL)
        self.assertEqual(json.loads(self.sent[0].content), {
            "model": EMBEDDING_MODEL, "input": ["原文", "语义检索"],
            "dimensions": DIMENSIONS, "encoding_format": "float"})
        self.assertEqual(value.vectors[0][0], 1.0)
        self.assertEqual(value.vectors[1][:3], (0.0, .6, .8))
        self.assertEqual((value.usage.input_tokens, value.model, value.request_id),
                         (12, EMBEDDING_MODEL, "request_controlled"))
        self.assertEqual(budget.counts, (1, 0, 0))
        self.assertEqual(self.rows(), [("qwen_text", "received")])

    async def test_absent_usage_and_model_stay_unknown_not_fabricated(self):
        data = result()
        for key in ("usage", "model", "id"):
            del data[key]
        with self.fixture.bind(PATH):
            async with self.client(data) as client:
                with bind_provider(self.settings, client, TaskBudget()):
                    value = await self.embed()
        self.assertIsNone(value.usage.input_tokens)
        self.assertIsNone(value.model)
        self.assertIsNone(value.request_id)
        self.assertEqual(value.requested_model, EMBEDDING_MODEL)

    async def test_invalid_local_input_never_reserves_or_sends(self):
        async with self.client() as client:
            with self.fixture.bind(PATH), bind_provider(self.settings, client, TaskBudget()):
                for texts in ([], [""] , [" "], [1], ["a"] * (MAX_BATCH + 1),
                              ["a" * 8001], ["中" * 2700], ["a" * 8000] * 7, ["\ud800"]):
                    with self.assertRaises(ProviderError):
                        await self.embed(texts=texts)
                with self.assertRaisesRegex(ProviderError, "configuration"):
                    await self.embed(model="other-model")
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.sent, [])

    async def test_model_scope_and_identity_are_required_even_for_cache(self):
        with self.assertRaisesRegex(ProviderError, "provider_unbound"):
            await self.embed()
        for path in ("/media/landmark", "/trips/schedule-explanation", "/saved-trips",
                     PATH.replace("assistant-turns", "turns")):
            with self.fixture.bind(path):
                async with self.client() as client:
                    with bind_provider(self.settings, client, TaskBudget()), self.assertRaisesRegex(
                            ProviderError, "embedding_namespace"):
                        await self.embed()
        with self.fixture.bind(PATH, owner_id="b" * 32):
            async with self.client() as client:
                adapter = QwenIntentEmbedding("a" * 32, "revision-1")
                with bind_provider(self.settings, client, TaskBudget()), self.assertRaisesRegex(
                        ProviderError, "embedding_namespace"):
                    await adapter.vectors("query", {"intent": ["template"]})
        self.assertEqual(self.rows(), [])

    async def test_actual_metered_classifier_cannot_be_bypassed_by_direct_old_paths(self):
        with self.fixture.bind("/trips/itinerary-query"):
            async with self.client() as client:
                with self.assertRaises(UsageError):
                    await client.post(URL, json={})
        self.assertEqual(self.sent, [])
        self.assertEqual(self.rows(), [])

    async def test_official_workspace_endpoint_uses_same_validated_beijing_scope(self):
        settings = self.settings.model_copy(update={"qwen_base_url":
            "https://fake-workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/"})
        with self.fixture.bind(PATH):
            async with self.client() as client:
                with bind_provider(settings, client, TaskBudget()):
                    await self.embed()
        self.assertEqual(str(self.sent[0].url),
                         "https://fake-workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/embeddings")

    async def test_native_invalid_envelopes_indices_and_dimensions_fail_without_repair(self):
        mutations = [
            lambda d: d.update(extra="private untrusted"),
            lambda d: d.update(model="other-model"),
            lambda d: d.update(object="wrong"),
            lambda d: d["data"][0].update(index=True),
            lambda d: d["data"][0].update(index=1),
            lambda d: d["data"][0].update(extra="unknown"),
            lambda d: d["data"][0].update(embedding=[0.0] * DIMENSIONS),
            lambda d: d["data"][0].update(embedding=[1.0] * (DIMENSIONS - 1)),
            lambda d: d["data"][0].update(embedding=[True] * DIMENSIONS),
            lambda d: d["data"][0].update(embedding=[1e308] * DIMENSIONS),
            lambda d: d.update(usage={"total_tokens": -1}),
            lambda d: d.update(usage={"total_tokens": 12, "prompt_tokens": 13}),
        ]
        for mutation in mutations:
            data = result()
            mutation(data)
            with self.fixture.bind(PATH):
                async with self.client(data) as client:
                    budget = TaskBudget()
                    with bind_provider(self.settings, client, budget):
                        with self.assertRaisesRegex(ProviderError, "embedding_structure"):
                            await self.embed()
                        with self.assertRaisesRegex(ProviderError, "task_stopped"):
                            await self.embed()
            self.assertTrue(budget.stopped)
        self.assertEqual(len(self.sent), len(mutations))
        self.assertEqual(len(self.rows()), len(mutations))

    async def test_duplicate_json_duplicate_indices_and_nonfinite_reject(self):
        duplicate = result(2)
        duplicate["data"][1]["index"] = 0
        for raw in (b'{"object":"list","object":"list","data":[]}',
                    b'{"object":"list","data":[],"extra":NaN}',
                    json.dumps(duplicate).encode(),
                    json.dumps(result()).replace("1.0", "1e999", 1).encode()):
            with self.fixture.bind(PATH):
                async with self.client(raw=raw) as client:
                    with bind_provider(self.settings, client, TaskBudget()), self.assertRaisesRegex(
                            ProviderError, "embedding_structure"):
                        await self.embed(texts=["one", "two"] if raw == json.dumps(duplicate).encode() else ["one"])
        self.assertEqual(len(self.sent), 4)

    async def test_body_limit_redirect_and_auth_errors_one_attempt_each(self):
        for status, raw, code in ((200, b"x" * (MAX_RESPONSE_BYTES + 1), "embedding_structure"),
                                  (302, b"", "upstream"),
                                  (401, b'{"error":{"code":"invalid_api_key"}}', "authentication"),
                                  (429, b"", "rate_limit")):
            with self.fixture.bind(PATH):
                async with self.client(status=status, raw=raw) as client:
                    with bind_provider(self.settings, client, TaskBudget()), self.assertRaisesRegex(ProviderError, code):
                        await self.embed()
        self.assertEqual(len(self.sent), 4)
        self.assertEqual(len(self.rows()), 4)

    async def test_shared_chat_embedding_budget_limits_concurrent_children(self):
        budget = TaskBudget(model_limit=2)
        with self.fixture.bind(PATH):
            async with self.client() as client:
                with bind_provider(self.settings, client, budget):
                    values = await asyncio.gather(*(self.embed() for _ in range(6)), return_exceptions=True)
                    with self.assertRaisesRegex(ProviderError, "task_stopped"):
                        await QwenMessagesClient().messages.create(model=MODEL, max_tokens=1,
                            messages=[{"role": "user", "content": "still same budget"}])
        self.assertEqual(sum(not isinstance(item, BaseException) for item in values), 2)
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(budget.counts, (2, 0, 0))

    async def test_budget_denial_latches_even_without_guarded_model_facade(self):
        budget = TaskBudget()
        await budget.reserve_embedding()
        await budget.reserve_embedding()
        await budget.reserve_embedding()
        with self.fixture.bind(PATH):
            async with self.client() as client:
                with bind_provider(self.settings, client, budget), self.assertRaisesRegex(ProviderError, "task_budget"):
                    await self.embed()
        self.assertTrue(budget.stopped)
        self.assertEqual(budget.counts, (3, 0, 0))
        self.assertEqual(self.sent, [])
        self.assertEqual(self.rows(), [])

    async def test_durable_global_quota_serializes_two_distinct_task_budgets(self):
        self.fixture.store.settings.business_daily_limits["qwen_text"] = DailyLimit(account=10, **{"global": 1})
        async with self.client() as client:
            async def work(owner):
                with self.fixture.bind(PATH, owner_id=owner):
                    with bind_provider(self.settings, client, TaskBudget()):
                        return await self.embed()
            values = await asyncio.gather(work("a" * 32), work("b" * 32), return_exceptions=True)
        self.assertEqual(sum(not isinstance(value, BaseException) for value in values), 1)
        self.assertEqual(sum(isinstance(value, UsageError) for value in values), 1)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.rows(), [("qwen_text", "received")])

    async def test_unknown_completion_mark_does_not_refund_or_allow_replacement(self):
        mark = self.fixture.store.mark
        def unknown(identifier, state):
            if state in {"received", "cancelled"}:
                raise UsageError()
            return mark(identifier, state)
        budget = TaskBudget()
        with self.fixture.bind(PATH), patch.object(self.fixture.store, "mark", unknown):
            async with self.client() as client:
                with bind_provider(self.settings, client, budget):
                    with self.assertRaises(UsageError):
                        await self.embed()
                    with self.assertRaisesRegex(ProviderError, "task_stopped"):
                        await self.embed()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.rows(), [("qwen_text", "dispatching")])

    async def test_timeout_cancel_and_scope_exit_do_not_retry_or_release_attempt(self):
        entered = asyncio.Event()
        async def pending(request):
            self.sent.append(request)
            entered.set()
            await asyncio.Event().wait()
        with self.fixture.bind(PATH):
            async with MeteredAsyncClient(transport=httpx.MockTransport(pending)) as client:
                budget = TaskBudget()
                with bind_provider(self.settings, client, budget):
                    task = asyncio.create_task(self.embed())
                    await entered.wait()
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    with self.assertRaisesRegex(ProviderError, "task_stopped"):
                        await self.embed()
        self.assertEqual(self.rows(), [("qwen_text", "cancelled")])
        entered.clear()
        with self.fixture.bind(PATH):
            async with MeteredAsyncClient(transport=httpx.MockTransport(pending)) as client:
                with bind_provider(self.settings, client, TaskBudget()):
                    task = asyncio.create_task(self.embed())
                    await entered.wait()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertEqual(self.rows(), [("qwen_text", "cancelled")] * 2)
        entered.clear()
        with self.fixture.bind(PATH):
            async with MeteredAsyncClient(transport=httpx.MockTransport(pending)) as client:
                with bind_provider(self.settings, client, TaskBudget()), patch(
                        "echomind_core.embedding.REQUEST_SECONDS", .04), self.assertRaisesRegex(ProviderError, "timeout"):
                    await self.embed()
        self.assertEqual(len(self.sent), 3)
        self.assertEqual(self.rows(), [("qwen_text", "cancelled")] * 3)

    async def test_cancel_during_durable_reservation_drains_before_dispatch(self):
        entered, release = threading.Event(), threading.Event()
        reserve = self.fixture.store.reserve
        def blocked(*args):
            entered.set()
            release.wait(3)
            return reserve(*args)
        with self.fixture.bind(PATH), patch.object(self.fixture.store, "reserve", blocked):
            async with self.client() as client:
                with bind_provider(self.settings, client, TaskBudget()):
                    task = asyncio.create_task(self.embed())
                    while not entered.is_set():
                        await asyncio.sleep(.001)
                    task.cancel()
                    await asyncio.sleep(0)
                    self.assertFalse(task.done())
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
        self.assertEqual(self.sent, [])
        self.assertEqual(self.rows(), [("qwen_text", "reserved")])

    async def test_counting_failure_stops_before_send_no_local_fallback(self):
        adapter = QwenIntentEmbedding("a" * 32, "revision-1")
        with self.fixture.bind(PATH), patch.object(self.fixture.store, "reserve", side_effect=UsageError()):
            async with self.client() as client:
                with bind_provider(self.settings, client, TaskBudget()), self.assertRaises(UsageError):
                    await adapter.vectors("query", {"intent": ["template"]}, lambda _: [1.0])
        self.assertEqual(self.sent, [])
        self.assertEqual(adapter.remote_templates, {})
        self.assertEqual(adapter.local_templates, {})

    async def test_intent_cache_is_owner_revision_template_bound_and_independently_returned(self):
        first = QwenIntentEmbedding("a" * 32, "revision-1")
        for index in range(2):
            with self.fixture.bind(PATH):
                async with self.client() as client:
                    with bind_provider(self.settings, client, TaskBudget()):
                        groups, vector, source = await first.vectors("query", {"intent": ["template"]})
            self.assertEqual(source, "qwen/text-embedding-v4/1024/v1")
            self.assertEqual(vector[0], 1.0)
            self.assertEqual(groups["intent"][0][0], 1.0)
            groups["intent"][0][0] = 99.0  # Does not mutate private cache.
        self.assertEqual(len(self.sent), 3)  # templates+query, then query only
        for adapter, templates in ((QwenIntentEmbedding("a" * 32, "revision-2"), {"intent": ["template"]}),
                                   (first, {"intent": ["changed template"]})):
            with self.fixture.bind(PATH):
                async with self.client() as client:
                    with bind_provider(self.settings, client, TaskBudget()):
                        await adapter.vectors("query", templates)
        self.assertEqual(len(self.sent), 7)
        for values in (("bad-owner", "version-1"), ("a" * 32, "private text ?")):
            with self.assertRaisesRegex(ProviderError, "embedding_namespace"):
                QwenIntentEmbedding(*values)

    async def test_template_batches_share_limit_without_partial_cache_or_silent_fallback(self):
        adapter = QwenIntentEmbedding("a" * 32, "revision-1")
        templates = {"first": [f"template {index}" for index in range(16)],
                     "second": [f"other {index}" for index in range(15)]}
        with self.fixture.bind(PATH):
            async with self.client() as client:
                with bind_provider(self.settings, client, TaskBudget()), self.assertRaisesRegex(ProviderError, "embedding_preparation_required"):
                    await adapter.vectors("query", templates)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.rows(), [])
        self.assertEqual(adapter.remote_templates, {})
        self.assertEqual(adapter.local_templates, {})

    async def test_large_prepared_index_validates_namespace_and_only_sends_query(self):
        adapter = QwenIntentEmbedding("a" * 32, "revision-1")
        templates = {str(index): [f"template {index}-{part}" for part in range(3)] for index in range(18)}
        vectors = [[1.0] + [0.0] * (DIMENSIONS - 1) for _ in range(54)]
        fingerprint = adapter.template_fingerprint(templates)
        with self.fixture.bind(PATH):
            async with self.client() as client:
                with bind_provider(self.settings, client, TaskBudget()):
                    for kwargs in ({"fingerprint": "wrong", "model": EMBEDDING_MODEL, "dimensions": DIMENSIONS},
                                   {"fingerprint": fingerprint, "model": "other", "dimensions": DIMENSIONS},
                                   {"fingerprint": fingerprint, "model": EMBEDDING_MODEL, "dimensions": 100}):
                        with self.assertRaisesRegex(ProviderError, "embedding_namespace"):
                            adapter.load_prepared(templates, vectors, **kwargs)
                    adapter.load_prepared(templates, vectors, fingerprint=fingerprint,
                                          model=EMBEDDING_MODEL, dimensions=DIMENSIONS)
                    groups, _, _ = await adapter.vectors("query", templates)
        self.assertEqual(len(groups), 18)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(json.loads(self.sent[0].content)["input"], ["query"])

    def test_vector_numeric_safety_and_stable_normalization(self):
        for value in ([math.inf] * DIMENSIONS, [math.nan] * DIMENSIONS,
                      [10 ** 1000] * DIMENSIONS, [0.0] * DIMENSIONS):
            with self.assertRaisesRegex(ProviderError, "embedding_structure"):
                unit_vector(value)
        vector = unit_vector([1e300] + [0.0] * (DIMENSIONS - 1))
        self.assertEqual(vector[0], 1.0)


if __name__ == "__main__":
    unittest.main()

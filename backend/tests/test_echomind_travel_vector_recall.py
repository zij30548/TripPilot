"""Private real Redis/Chroma, controlled embedding HTTP, never semantic scores."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date
import importlib.util
import json
import shutil
import unittest
from unittest.mock import AsyncMock, patch

import httpx

if any(importlib.util.find_spec(name) is None for name in ("chromadb", "redis", "jsonschema", "prometheus_client")):
    raise unittest.SkipTest("requires the explicit echomind extra and local Redis")

from echomind_core.execution import ExecutionError
from echomind_core.execution import GuardedToolRegistry, ToolDeclaration
from echomind_core.embedding import DIMENSIONS, EMBEDDING_MODEL
from echomind_core.provider import MODEL
from echomind_core.travel_bridge import TravelToolBridge
from echomind_core.travel_budget import TravelBudgetTier, TravelTaskBudget
from echomind_core.travel_capabilities import ReviewedKnowledgePort
from echomind_core.travel_full_engine import ReviewedRetrieval
from echomind_core.travel_package import TravelDomainPackage
from echomind_core.travel_vector_recall import ReviewedVectorRecall
from echomind_core.upstream.tooling.knowledge_base import KnowledgeBase
from tests import test_echomind_runtime as fixture
from tests.test_echomind_travel_bridge import BINDING, capture, request
from tests.test_knowledge_national import corpus_values, reviewed
from tooling.travel.knowledge_national import NationalKnowledgeIndex


def port(long=False, many=False):
    raw = corpus_values()
    if long:
        raw["documents"][0]["sections"][0]["summary"] = (
            "受控完整主题：" + "预约规则说明。" * 70 + "儿童须成人全程陪同，但另行收费项目除外；这项条件必须保留在完整主题中。")
    if many:
        raw["documents"][0]["sections"] = [{**deepcopy(raw["documents"][0]["sections"][0]),
            "id": "unit-" + str(number), "summary": "不同受控主题" + str(number)} for number in range(11)]
    return ReviewedKnowledgePort(NationalKnowledgeIndex(*reviewed(raw)))


@unittest.skipUnless(shutil.which("redis-server"), "requires private local redis-server, no auto installation")
class ReviewedVectorRecallTests(unittest.IsolatedAsyncioTestCase):
    # Reuse setup helpers, not its TestCase superclass/tests. All stores and
    # Redis are created privately and cleaned; no user's existing service/data.
    asyncSetUp = fixture.RuntimeTests.asyncSetUp
    asyncTearDown = fixture.RuntimeTests.asyncTearDown
    open = fixture.RuntimeTests.open
    gate = fixture.RuntimeTests.gate
    client = fixture.RuntimeTests.client

    def context(self):
        return {"owner_id": self.current.owner_id, "conversation_id": self.current.conversation_id,
                "session_id": self.current.session_id, "input_version": self.current.input_version}

    async def test_explicit_prepare_original_chunks_chroma_search_and_complete_unit_projection(self):
        resource = await self.open()
        reviewed_port = port(long=True)
        recall = ReviewedVectorRecall(resource, reviewed_port, "310000", "shanghai-museum", date(2026, 10, 24))
        self.assertEqual(self.sent, [])
        self.assertEqual(recall.summary()["state"], "unprepared")
        calls = {"add": 0, "search": 0}
        original_add, original_search = KnowledgeBase.add_documents, KnowledgeBase.search
        def add(instance, documents):
            calls["add"] += 1
            return original_add(instance, documents)
        def search(instance, query, top_k=5):
            calls["search"] += 1
            return original_search(instance, query, top_k)
        gate = self.gate()
        with self.fixture.bind(fixture.PATH):
            async with self.client() as wire, resource.task(gate, self.settings, wire):
                with patch.object(KnowledgeBase, "add_documents", add), patch.object(KnowledgeBase, "search", search):
                    prepared = await recall.prepare()
                    rows = await recall.bound_recall()({"query": "儿童预约", "top_k": 5}, self.context())
                    await recall.prepare()  # Explicit idempotent local check, no second import.
        self.assertEqual(calls, {"add": 1, "search": 1})
        self.assertEqual(prepared["unit_count"], 2)
        self.assertGreater(prepared["original_chunk_count"], prepared["unit_count"])
        self.assertEqual(gate.budget.counts, (2, 0, 0))  # One prepare batch + one query embedding.
        self.assertEqual(len(self.sent), 2)
        expected = {item["chunk_id"]: item for item in reviewed_port._matches.values()
                    if item["source"]["venue_id"] == "shanghai-museum"}
        self.assertEqual({row["chunk_id"] for row in rows}, set(expected))
        for row in rows:
            self.assertEqual(row["content"], expected[row["chunk_id"]]["section"]["summary"])
            self.assertEqual(row["section"], expected[row["chunk_id"]]["section"])
            self.assertEqual(row["source"], expected[row["chunk_id"]]["source"])
            self.assertFalse(row["vector_similarity_is_confidence"])
            self.assertNotIn("owner_id", row)
        complete = next(row for row in rows if row["chunk_id"].startswith("sh-entry/"))
        self.assertGreater(len(complete["content"]), 500)
        self.assertIn("另行收费项目除外", complete["content"])
        historical = next(row for row in rows if row["source"]["kind"] == "historical")
        self.assertEqual(historical["applicability_check"]["status"], "historical_reference")

    async def test_full_original_rewrite_parallel_vector_recall_dedup_rerank_program_projection(self):
        self.current = BINDING.scope
        resource = await self.open()
        reviewed_port = port(long=True)
        vector = ReviewedVectorRecall(resource, reviewed_port, "310000", "shanghai-museum")
        bridge = TravelToolBridge(capture()[0], lambda: BINDING)
        retrieval = ReviewedRetrieval(bridge, reviewed_port, "310000", "shanghai-museum",
            package=TravelDomainPackage().capture(), vector=vector)
        spec = retrieval.spec()
        wrapped = GuardedToolRegistry((ToolDeclaration(spec, True),)).wrap(spec)
        stages = []
        def controlled(_request, payload):
            if _request.url.path.endswith("/embeddings"):
                return httpx.Response(200, json={"object": "list", "model": EMBEDDING_MODEL,
                    "data": [{"object": "embedding", "index": i,
                              "embedding": [1.0] + [0.0] * (DIMENSIONS - 1)} for i in range(len(payload["input"]))],
                    "usage": {"total_tokens": 6}})
            self.assertTrue(_request.url.path.endswith("/chat/completions"))
            system = payload["messages"][0]["content"]
            if "[阶段契约：rewrite]" in system:
                stages.append("rewrite")
                content = json.dumps(["儿童陪同", "预约渠道", "历史规定"], ensure_ascii=False)
            elif "[阶段契约：rerank]" in system:
                stages.append("rerank")
                content = "[0,1]"
            else:
                self.fail("unexpected model stage")
            return httpx.Response(200, json={"model": MODEL, "id": "controlled_rag",
                "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3}})
        gate = self.gate(TravelTaskBudget(TravelBudgetTier.REFERENCE))
        with self.fixture.bind(fixture.PATH):
            async with self.client(controlled) as wire, resource.task(gate, self.settings, wire):
                await vector.prepare()
                await wrapped.handler(request(), {"query": "儿童预约", "top_k": 1})
        self.assertEqual(stages, ["rewrite", "rerank"])
        self.assertEqual(len(self.sent), 7)  # Prepare1 + rewrite1 + queryEmbeddings4 + rank1.
        self.assertEqual(gate.budget.counts, (7, 5, 2560))  # Outer read +4 recalls.
        projection = await bridge.project()
        row = projection.tool_results[0]["data"]
        self.assertTrue(row["retrieval_only"])
        self.assertTrue(row["reranked"])
        self.assertEqual(len(row["items"]), 1)
        item = row["items"][0]
        original = reviewed_port._matches[item["chunk_id"]]
        self.assertEqual(item["source"], original["source"])
        self.assertEqual(item["section"], original["section"])
        self.assertIn("整理摘要：" + original["section"]["summary"], row["message"])
        self.assertIn("知识库AI问答未开放", row["message"])
        self.assertEqual(row["corpus_hash"], reviewed_port.corpus_hash)

    async def test_unprepared_and_forged_context_no_implicit_seed_or_embedding(self):
        resource = await self.open()
        recall = ReviewedVectorRecall(resource, port(), "310000", "shanghai-museum")
        with self.assertRaisesRegex(ExecutionError, "travel_vector_unprepared"):
            with self.fixture.bind(fixture.PATH):
                async with self.client() as wire, resource.task(self.gate(), self.settings, wire):
                    await recall.bound_recall()({"query": "儿童", "top_k": 5}, self.context())
        for changed in ({**self.context(), "owner_id": "b" * 32},
                        {**self.context(), "input_version": False},
                        {**self.context(), "url": "https://example.test"}):
            with self.assertRaises(ExecutionError):
                with self.fixture.bind(fixture.PATH):
                    async with self.client() as wire, resource.task(self.gate(), self.settings, wire):
                        await recall.bound_recall()({"query": "儿童", "top_k": 5}, changed)
        self.assertEqual(self.sent, [])
        self.assertEqual(resource._store._collections["knowledge"].count(), 0)

    async def test_raw_foreign_or_tampered_identity_content_version_title_chunk_and_score_rejected(self):
        resource = await self.open()
        recall = ReviewedVectorRecall(resource, port(), "310000", "shanghai-museum")
        with self.fixture.bind(fixture.PATH):
            async with self.client() as wire, resource.task(self.gate(), self.settings, wire):
                await recall.prepare()
                raw = await resource.search("儿童", 5)
                self.assertTrue(raw)
        for field, value in (("document_id", "f" * 64), ("title", "其他机构规则"),
                             ("content", "篡改条件"), ("version", "f" * 64),
                             ("chunk", True), ("chunk", 50), ("score", float("nan")),
                             ("score", True), ("score", 8.0)):
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ExecutionError, "travel_vector_result"):
                with self.fixture.bind(fixture.PATH):
                    async with self.client() as wire, resource.task(self.gate(), self.settings, wire):
                        damaged = {**raw[0], field: value}
                        with patch.object(resource, "search", AsyncMock(return_value=[damaged])):
                            await recall.bound_recall()({"query": "儿童", "top_k": 5}, self.context())
        with self.assertRaises(ExecutionError):
            with self.fixture.bind(fixture.PATH):
                async with self.client() as wire, resource.task(self.gate(), self.settings, wire):
                    with patch.object(resource, "search", AsyncMock(return_value=[{**raw[0], "extra": "untrusted"}])):
                        await recall.bound_recall()({"query": "儿童", "top_k": 5}, self.context())
        self.assertEqual(len(self.sent), 2)

    async def test_reviewed_source_mutation_fails_before_new_search(self):
        resource = await self.open()
        reviewed_port = port()
        recall = ReviewedVectorRecall(resource, reviewed_port, "310000", "shanghai-museum")
        with self.assertRaisesRegex(ExecutionError, "travel_vector_source"):
            with self.fixture.bind(fixture.PATH):
                async with self.client() as wire, resource.task(self.gate(), self.settings, wire):
                    await recall.prepare()
                    first = next(iter(reviewed_port._matches.values()))
                    first["source"]["url"] = "https://unreviewed.example.test/fake"
                    await recall.bound_recall()({"query": "儿童", "top_k": 5}, self.context())
        self.assertEqual(len(self.sent), 1)

    async def test_bad_result_latches_shared_gate_even_if_original_manager_would_catch_error(self):
        resource = await self.open()
        recall = ReviewedVectorRecall(resource, port(), "310000", "shanghai-museum")
        gate = self.gate()
        with self.assertRaisesRegex(ExecutionError, "travel_vector_result"):
            with self.fixture.bind(fixture.PATH):
                async with self.client() as wire, resource.task(gate, self.settings, wire):
                    await recall.prepare()
                    with patch.object(resource, "search", AsyncMock(return_value=[{"foreign": "row"}])):
                        with self.assertRaises(ExecutionError):
                            await recall.bound_recall()({"query": "儿童", "top_k": 5}, self.context())
                    self.assertTrue(gate.budget.stopped)
                    # Swallowing the local exception must not allow another
                    # query/model reservation or a partial-success fallback.
                    with self.assertRaises(ExecutionError):
                        await resource.search("再次查询", 5)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(gate.budget.counts, (1, 0, 0))

    async def test_wrong_city_unreviewed_scope_and_over_limit_refused_without_import(self):
        resource = await self.open()
        for city, venue in (("110000", "shanghai-museum"), ("310000", "beijing-museum"), ("999999", "none")):
            with self.subTest(city=city, venue=venue), self.assertRaises(ExecutionError):
                ReviewedVectorRecall(resource, port(), city, venue)
        with self.assertRaisesRegex(ExecutionError, "travel_vector_preparation_limit"):
            ReviewedVectorRecall(resource, port(many=True), "310000", "shanghai-museum")
        self.assertEqual(self.sent, [])
        self.assertEqual(resource._store._collections["knowledge"].count(), 0)

    async def test_cancelled_preparation_stays_uncertain_and_never_auto_retries(self):
        resource = await self.open()
        recall = ReviewedVectorRecall(resource, port(), "310000", "shanghai-museum")
        entered, pending = asyncio.Event(), asyncio.Event()
        async def delay(documents):
            entered.set()
            await pending.wait()
        async def prepare():
            with self.fixture.bind(fixture.PATH):
                async with self.client() as wire, resource.task(self.gate(), self.settings, wire):
                    await recall.prepare()
        with patch.object(resource, "import_documents", delay):
            task = asyncio.create_task(prepare())
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(recall.summary()["state"], "uncertain")
        with self.fixture.bind(fixture.PATH):
            async with self.client() as wire, resource.task(self.gate(), self.settings, wire):
                with self.assertRaisesRegex(ExecutionError, "travel_vector_preparation_uncertain"):
                    await recall.prepare()
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()

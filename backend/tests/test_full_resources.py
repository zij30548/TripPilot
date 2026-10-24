"""Private resource lifecycle and SYNTHETIC preparation receipts, no live API.

Real temporary Redis/Chroma are used only in lifecycle tests; INET is blocked.
These receipt fixtures test binding, not real model acquisition or quality.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

if any(importlib.util.find_spec(name) is None for name in ("chromadb", "redis", "jsonschema", "prometheus_client")):
    raise unittest.SkipTest("explicit echomind extra required")

from echomind_core.embedding import DIMENSIONS, EMBEDDING_MODEL, VECTOR_VERSION, QwenIntentEmbedding, _payload
from echomind_core.execution import ExecutionError
from echomind_core.full_resources import (
    FullResourceConfig, FullRuntimeServices, RECEIPT_VERSION, REVISION, canonical,
)
from echomind_core.runtime import CandidateResources
from echomind_core.storage import StorageConfig
from echomind_core.travel_domain import TRAVEL_DOMAIN
from echomind_core.travel_package import TravelDomainPackage
from echomind_core.travel_templates import PreparedTravelTemplates, VERSION, template_hash
from echomind_core.travel_vector_recall import INDEX_RECEIPT_VERSION, ReviewedVectorRecall
from tests import test_echomind_runtime as fixture
from tests.test_echomind_travel_vector_recall import port as reviewed_fixture

OWNER = "a" * 32


def template_files(root: Path, owner: str = OWNER) -> tuple[Path, dict]:
    root.mkdir(mode=0o700, exist_ok=True)
    folder = root / owner
    folder.mkdir(mode=0o700, exist_ok=True)
    templates = {key: list(values) for key, values in TRAVEL_DOMAIN.templates.items()}
    embedding = QwenIntentEmbedding(owner, REVISION)
    data = {"schema_version": VERSION, "owner_id": owner, "revision": REVISION,
        "model": EMBEDDING_MODEL, "dimensions": DIMENSIONS, "vector_version": VECTOR_VERSION,
        "template_hash": template_hash(templates), "fingerprint": embedding.template_fingerprint(templates),
        "vectors": [[1.0] + [0.0] * (DIMENSIONS - 1) for _ in range(24)]}
    raw = canonical(data)
    prepared = PreparedTravelTemplates.from_json(raw, templates)
    texts = [text for values in templates.values() for text in values]
    receipt = {"schema_version": RECEIPT_VERSION, "owner_id": owner, "revision": REVISION,
        "model": EMBEDDING_MODEL, "dimensions": DIMENSIONS, "vector_version": VECTOR_VERSION,
        "template_hash": prepared.template_hash, "template_count": 24,
        "artifact_sha256": hashlib.sha256(raw).hexdigest(), "artifact_canonical_sha256": prepared.artifact_hash,
        "operations": [{"operation_id": f"controlled-template-{index}", "state": "received",
            "response_sha256": "f" * 64,
            "payload_sha256": hashlib.sha256(canonical(_payload(texts[start:start + 10], EMBEDDING_MODEL))).hexdigest()}
            for index, start in enumerate(range(0, len(texts), 10))]}
    for name, value in (("templates.json", raw), ("receipt.json", canonical(receipt))):
        (folder / name).write_bytes(value)
        (folder / name).chmod(0o600)
    return folder, receipt


class FullPreflightTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/private/tmp" if Path("/private/tmp").is_dir() else None)
        self.path = Path(self.temp.name)
        self.root = self.path / "templates"
        self.folder, self.receipt = template_files(self.root)
        storage = StorageConfig("full-test", self.path / "unused.sock", self.path / "chroma", EMBEDDING_MODEL, DIMENSIONS)
        self.config = FullResourceConfig(storage, self.root)
        self.services = FullRuntimeServices(self.config)

    async def asyncTearDown(self):
        await self.services.aclose()
        self.temp.cleanup()

    async def test_preflight_validates_exact_24_inputs_three_bodies_without_open_or_send(self):
        with patch.object(CandidateResources, "open", side_effect=AssertionError("no storage open")), \
                patch("echomind_core.embedding.QwenEmbeddingClient.embed", side_effect=AssertionError("no paid call")):
            checked = self.services.preflight(OWNER)
        self.assertEqual(len(checked.prepared.vectors), 24)
        self.assertEqual(checked.prepared.revision, REVISION)
        self.assertEqual(checked.summary()["source_authenticity"], "declared_received_not_independently_authenticated")
        self.assertFalse(checked.summary()["auto_prepare"])
        self.assertIsNone(checked.vector_receipt_json)

    async def test_missing_other_owner_permissions_and_links_fail_closed(self):
        with self.assertRaises(ExecutionError):
            self.services.preflight("b" * 32)
        for mode in (0o644, 0o640, 0o400, 0o660):
            with self.subTest(mode=oct(mode)):
                (self.folder / "templates.json").chmod(mode)
                with self.assertRaises(ExecutionError):
                    self.services.preflight(OWNER)
        (self.folder / "templates.json").chmod(0o600)
        backup = self.path / "original.json"
        (self.folder / "templates.json").rename(backup)
        (self.folder / "templates.json").symlink_to(backup)
        with self.assertRaises(ExecutionError):
            self.services.preflight(OWNER)
        (self.folder / "templates.json").unlink()
        os.link(backup, self.folder / "templates.json")
        with self.assertRaises(ExecutionError):
            self.services.preflight(OWNER)

    async def test_receipt_pending_unknown_duplicate_extra_model_or_body_drift_rejected(self):
        changes = [{**self.receipt, "model": "different"}, {**self.receipt, "dimensions": True},
                   {**self.receipt, "extra": "untrusted"}]
        for field, value in (("state", "pending"), ("state", "unknown"), ("payload_sha256", "0" * 64),
                             ("response_sha256", "not-a-hash"), ("operation_id", "../private")):
            changed = deepcopy(self.receipt)
            changed["operations"][0][field] = value
            changes.append(changed)
        duplicated = deepcopy(self.receipt)
        duplicated["operations"][1]["operation_id"] = duplicated["operations"][0]["operation_id"]
        changes.append(duplicated)
        for value in changes:
            (self.folder / "receipt.json").write_bytes(canonical(value))
            with self.subTest(value=value.get("model")), self.assertRaises(ExecutionError):
                self.services.preflight(OWNER)

    async def test_artifact_byte_hash_revision_and_duplicate_json_keys_not_silently_repaired(self):
        path = self.folder / "templates.json"
        original = path.read_bytes()
        for raw in (original + b"\n", original.replace(b'intent-index-v1', b'intent-index-v2'),
                    original[:-1] + b',"dimensions":1024}'):
            path.write_bytes(raw)
            with self.assertRaises(ExecutionError):
                self.services.preflight(OWNER)

    async def test_scope_requires_complete_trusted_configuration_and_index_receipt(self):
        for fields in ({"reviewed_city_id": "310000"}, {"reviewed_venue_id": "museum"},
                       {"namespace_revision": "legacy"}, {"vector_receipt_root": self.root}):
            with self.subTest(fields=fields), self.assertRaises(ExecutionError):
                replace(self.config, **fields)
        config = replace(self.config, reviewed_city_id="310000", reviewed_venue_id="shanghai-museum-people",
                         vector_receipt_root=self.path / "index")
        # Use real catalogue IDs, not a guessed facility alias.
        venue = next(venue for venue in self.services._national._index.corpus.venues if venue.city_id == "310000")
        config = replace(config, reviewed_venue_id=venue.id)
        scoped = FullRuntimeServices(config)
        try:
            with self.assertRaises(ExecutionError):
                scoped.preflight(OWNER)
        finally:
            await scoped.aclose()


@unittest.skipUnless(shutil.which("redis-server"), "requires private local Redis, never auto installed")
class FullResourceLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.RuntimeTests.asyncSetUp(self)
        self.root = self.path / "templates"
        self.folder, self.receipt = template_files(self.root)
        self.services = FullRuntimeServices(FullResourceConfig(self.config, self.root))
        self.package = TravelDomainPackage().capture()

    async def asyncTearDown(self):
        await self.services.aclose()
        await fixture.RuntimeTests.asyncTearDown(self)

    def opened(self):
        async def preferences():
            return []
        return self.services.open_for_turn(self.current, self.current_fn, [], preferences, self.package)

    async def prepare_controlled_index(self):
        """Real private index, explicitly SYNTHETIC vectors and receipt metadata."""
        resource = await fixture.RuntimeTests.open(self)
        reviewed = reviewed_fixture(long=True)
        vector = ReviewedVectorRecall(resource, reviewed, "310000", "shanghai-museum")
        with self.fixture.bind(fixture.PATH):
            async with fixture.RuntimeTests.client(self) as client, resource.task(
                    fixture.RuntimeTests.gate(self), self.settings, client):
                await vector.prepare()
        raw = resource._store._collections["knowledge"].get(include=["documents", "metadatas", "embeddings"])
        rows = sorted([{"id": identifier, "document": raw["documents"][index],
            "metadata": raw["metadatas"][index],
            "embedding_sha256": hashlib.sha256(canonical([float(value) for value in raw["embeddings"][index]])).hexdigest()}
            for index, identifier in enumerate(raw["ids"])], key=lambda value: value["id"])
        texts = list(dict.fromkeys(chunk for entry in vector._mappings.values() for chunk in entry["chunks"]))
        receipt = {"schema_version": INDEX_RECEIPT_VERSION, "owner_id": self.current.owner_id,
            "namespace": self.config.namespace, "embedding_model": EMBEDDING_MODEL, "dimensions": DIMENSIONS,
            "corpus_hash": vector.corpus_hash, "city_id": vector.city_id, "venue_id": vector.venue_id,
            "chunk_count": vector._chunk_count, "rows_sha256": hashlib.sha256(canonical(rows)).hexdigest(),
            "operations": [{"operation_id": "controlled-index-prepare", "state": "received", "response_sha256": "e" * 64,
                "payload_sha256": hashlib.sha256(canonical(_payload(texts, EMBEDDING_MODEL))).hexdigest()}]}
        index_root = self.path / "index"
        index_root.mkdir(mode=0o700)
        owner_folder = index_root / self.current.owner_id
        owner_folder.mkdir(mode=0o700)
        (owner_folder / "index-receipt.json").write_bytes(canonical(receipt))
        (owner_folder / "index-receipt.json").chmod(0o600)
        await resource.aclose()
        await self.services.aclose()
        config = replace(self.services.config, reviewed_city_id="310000", reviewed_venue_id="shanghai-museum",
                         vector_receipt_root=index_root)
        with patch("echomind_core.full_resources.get_index", return_value=reviewed._index):
            self.services = FullRuntimeServices(config)
        return resource, receipt, owner_folder / "index-receipt.json"

    async def test_fresh_request_resources_same_current_callback_and_shared_stats_only(self):
        async with self.opened() as first:
            self.assertIs(first.resources.current, self.current_fn)
            self.assertIs(first.memory.resources, first.resources)
            self.assertIsNone(first.vector)
            self.assertEqual(first.resources.embedding.revision, REVISION)
            old_resources, observation = first.resources, first.observation
        self.assertTrue(old_resources._closed)
        self.current = replace(self.current, input_version=1, session_id=str(uuid4()))
        async with self.opened() as second:
            self.assertIsNot(second.resources, old_resources)
            self.assertIs(second.observation, observation)
            self.assertIs(second.resources.current, self.current_fn)
        self.assertTrue(second.resources._closed)
        self.assertEqual(self.sent, [])

    async def test_preparation_changed_while_opening_is_rejected_and_opened_store_closed(self):
        original = CandidateResources.open
        captured = []
        async def changed(*args):
            result = await original(*args)
            captured.append(result)
            path = self.folder / "receipt.json"
            path.write_bytes(path.read_bytes() + b"\n")
            return result
        with patch.object(CandidateResources, "open", changed), self.assertRaises(ExecutionError):
            async with self.opened():
                self.fail("changed receipt entered task")
        self.assertTrue(captured[0]._closed)
        self.assertEqual(self.sent, [])

    async def test_failure_and_repeated_cancellation_drain_real_close_before_finishing(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original = CandidateResources.aclose
        closed = []
        async def delayed(resource):
            entered.set()
            await release.wait()
            await original(resource)
            closed.append(resource)
        async def operation():
            async with self.opened():
                raise RuntimeError("controlled operation failure")
        with patch.object(CandidateResources, "aclose", delayed):
            task = asyncio.create_task(operation())
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(len(closed), 1)
        self.assertTrue(closed[0]._closed)
        self.assertEqual(self.services._active, set())
        self.assertEqual(self.sent, [])

    async def test_service_close_cancels_active_turn_and_prevents_new_resources(self):
        entered = asyncio.Event()
        async def operation():
            async with self.opened() as ports:
                entered.set()
                self.live = ports.resources
                await asyncio.Future()
        task = asyncio.create_task(operation())
        await asyncio.wait_for(entered.wait(), 5)
        await self.services.aclose()
        self.assertTrue(task.cancelled())
        self.assertTrue(self.live._closed)
        self.assertTrue(self.services.observation.summary()["closed"])
        with self.assertRaises(ExecutionError):
            self.services.preflight(OWNER)
        self.assertEqual(self.sent, [])

    async def test_repeated_cancel_of_service_close_still_closes_monitor_after_store_drain(self):
        running, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original = CandidateResources.aclose
        async def delayed(resource):
            closing.set()
            await release.wait()
            await original(resource)
        async def operation():
            async with self.opened():
                running.set()
                await asyncio.Future()
        with patch.object(CandidateResources, "aclose", delayed):
            request_task = asyncio.create_task(operation())
            await asyncio.wait_for(running.wait(), 5)
            stop = asyncio.create_task(self.services.aclose())
            await asyncio.wait_for(closing.wait(), 5)
            stop.cancel()
            stop.cancel()
            await asyncio.sleep(0)
            self.assertFalse(stop.done())
            self.assertFalse(self.services.observation.summary()["closed"])
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await stop
        self.assertTrue(request_task.cancelled())
        self.assertTrue(self.services.observation.summary()["closed"])
        self.assertEqual(self.services._active, set())
        await self.services.aclose()  # Same completed close, never a new reset.
        self.assertEqual(self.sent, [])

    async def test_two_active_turns_third_busy_without_native_open_and_capacity_restored(self):
        opened = [asyncio.Event(), asyncio.Event()]
        release = [asyncio.Event(), asyncio.Event()]
        original = CandidateResources.open
        calls = []
        async def counted(*arguments):
            calls.append(arguments[1])
            return await original(*arguments)
        async def operation(index):
            async with self.opened():
                opened[index].set()
                await release[index].wait()
        with patch.object(CandidateResources, "open", counted):
            first = asyncio.create_task(operation(0))
            await asyncio.wait_for(opened[0].wait(), 5)
            second = asyncio.create_task(operation(1))
            await asyncio.wait_for(opened[1].wait(), 5)
            try:
                with self.assertRaisesRegex(ExecutionError, "^full_resources_busy$"):
                    async with self.opened():
                        self.fail("third turn must not enter native storage")
                self.assertEqual(len(calls), 2)
                release[0].set()
                await first
                async with self.opened():
                    self.assertEqual(len(calls), 3)
            finally:
                release[0].set()
                release[1].set()
                await asyncio.gather(first, second, return_exceptions=True)
        self.assertEqual(self.services._active, set())
        self.assertEqual(self.sent, [])

    async def test_reviewed_index_reopens_with_exact_rows_and_no_paid_preparation(self):
        await self.prepare_controlled_index()
        self.assertEqual(len(self.sent), 1)
        for version in (1, 2):
            self.current = replace(self.current, input_version=version, session_id=str(uuid4()))
            with patch.object(CandidateResources, "import_documents", side_effect=AssertionError("no auto import")):
                async with self.opened() as ports:
                    self.assertEqual(ports.vector.summary()["state"], "ready")
                    self.assertEqual(ports.vector._prepared_scope, self.current)
                    self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.sent), 1)

    async def test_index_receipt_unknown_or_changed_persisted_content_is_not_fallback(self):
        resource, receipt, path = await self.prepare_controlled_index()
        changed = deepcopy(receipt)
        changed["operations"][0]["state"] = "unknown"
        path.write_bytes(canonical(changed))
        with self.assertRaisesRegex(ExecutionError, "travel_vector_prepared_receipt_invalid"):
            async with self.opened():
                self.fail("unknown preparation cannot run")
        path.write_bytes(canonical(receipt))
        collection = resource._store._collections["knowledge"]
        row = collection.get(include=["documents", "metadatas"])
        collection.update(ids=[row["ids"][0]], documents=["未经审核的替换条件"],
                          embeddings=[[1.0] + [0.0] * (DIMENSIONS - 1)])
        with self.assertRaisesRegex(ExecutionError, "travel_vector_prepared_receipt_invalid"):
            async with self.opened():
                self.fail("changed index cannot fall back to BM25")
        self.assertEqual(len(self.sent), 1)  # Only the explicit controlled preparation.


if __name__ == "__main__":
    unittest.main()

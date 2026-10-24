"""Controlled wire preparation; real private counters and optional Redis/Chroma.

All vectors are explicitly synthetic TEST outputs. No real model, website,
business transport, credential discovery or production data is used.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest.mock import patch

import httpx

from core.config import Settings
from echomind_core.embedding import DIMENSIONS, EMBEDDING_MODEL, _payload
from echomind_core.execution import ExecutionError
from echomind_core.full_resources import FullResourceConfig, FullRuntimeServices, REVISION, canonical
from echomind_core.provider import MODEL, ProviderError
from echomind_core.storage import StorageConfig, StorageError
from echomind_core.travel_domain import TRAVEL_DOMAIN
from echomind_core.travel_templates import PreparedTravelTemplates
from echomind_core.travel_vector_recall import ReviewedVectorRecall
from tests.usage_fixture import UsageFixture
from tools.echomind_full_acceptance_ledger import FILENAME, FullAcceptanceLedger
from tools.echomind_full_acceptance_preparation import PreparationStopped, prepare_index, prepare_templates
from tools.echomind_full_acceptance_transport import full_acceptance_client

OWNER = "a" * 32
PATH = "/chat/conversations/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/assistant-turns"
BOUND = "f" * 64


def binding():
    return {"grant_sha256": "a" * 64, "protocol_sha256": "b" * 64,
            "code_freeze_sha256": "c" * 64, "prior_ledger_hashes": {"old_paused": "d" * 64}}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class PreparationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.root.chmod(0o700)
        self.template_root = self.root / "templates"
        self.template_root.mkdir(mode=0o700)
        self.ledger = FullAcceptanceLedger.create(self.root / FILENAME, binding=binding())
        self.usage = UsageFixture()
        self.addCleanup(self.usage.close)
        self.settings = Settings(_env_file=None, qwen_api_key="controlled-not-real",
            qwen_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", qwen_model=MODEL)
        self.sent, self.responses = [], []
        self.invalid_at = None
        self.observation_mutation = None

    def handler(self, request):
        payload = json.loads(request.content)
        self.sent.append(payload)
        data = [{"object": "embedding", "index": index,
            "embedding": [1.0, float(index + 1)] + [0.0] * (DIMENSIONS - 2)}
            for index, _text in enumerate(payload["input"])]
        if len(self.sent) == self.invalid_at:
            data = []
        response = httpx.Response(200, json={"id": "controlled_" + str(len(self.sent)), "object": "list",
            "model": EMBEDDING_MODEL, "data": data, "usage": {"total_tokens": 12}})
        self.responses.append(response.content)
        return response

    @asynccontextmanager
    async def client_for(self, operation):
        async with full_acceptance_client(ledger=self.ledger, operation_id=operation,
                binding_sha256=BOUND, settings=self.settings,
                verify=lambda: self.ledger.verify(binding=binding()),
                transport=httpx.MockTransport(self.handler)) as pair:
            yield pair
            if self.observation_mutation:
                self.observation_mutation(pair[1])

    async def templates(self):
        return await prepare_templates(self.template_root, OWNER, REVISION, self.settings,
            self.client_for, self.ledger, lambda _operation: BOUND)

    async def test_three_exact_real_parser_batches_create_private_current_artifact_and_real_hash_receipt(self):
        with self.usage.bind(PATH):
            summary = await self.templates()
        expected = [text for values in TRAVEL_DOMAIN.templates.values() for text in values]
        self.assertEqual([len(item["input"]) for item in self.sent], [10, 10, 4])
        self.assertEqual([text for item in self.sent for text in item["input"]], expected)
        self.assertTrue(all(set(item) == {"model", "input", "dimensions", "encoding_format"} for item in self.sent))
        folder = self.template_root / OWNER
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
        for filename in ("templates.json", "receipt.json"):
            self.assertEqual(stat.S_IMODE((folder / filename).stat().st_mode), 0o600)
        artifact = PreparedTravelTemplates.from_json((folder / "templates.json").read_bytes(),
            {key: list(value) for key, value in TRAVEL_DOMAIN.templates.items()})
        self.assertEqual(len(artifact.vectors), 24)
        receipt = json.loads((folder / "receipt.json").read_bytes())
        self.assertEqual([item["response_sha256"] for item in receipt["operations"]],
                         [digest(raw) for raw in self.responses])
        self.assertEqual(receipt["artifact_sha256"], digest((folder / "templates.json").read_bytes()))
        self.assertEqual([item["payload_sha256"] for item in receipt["operations"]],
                         [digest(canonical(_payload(expected[index:index + 10], EMBEDDING_MODEL)))
                          for index in (0, 10, 20)])
        self.assertEqual(summary["quality_acceptance"], "not_assessed")
        self.assertEqual(self.ledger.summary()["reserved_attempts"], 3)
        self.assertEqual(self.usage.store.own_usage(OWNER)["services"]["qwen_text"]["used"], 3)
        services = FullRuntimeServices(FullResourceConfig(StorageConfig("preparation-test",
            self.root / "unused.sock", self.root / "unused-chroma", EMBEDDING_MODEL, DIMENSIONS), self.template_root))
        try:
            self.assertEqual(len(services.preflight(OWNER).prepared.vectors), 24)
        finally:
            await services.aclose()
        self.assertNotIn("controlled-not-real", canonical(summary).decode())
        self.assertNotIn(expected[0], canonical(summary).decode())

    async def test_invalid_second_response_is_counted_stops_and_never_publishes_partial_template(self):
        self.invalid_at = 2
        with self.usage.bind(PATH), self.assertRaises(ProviderError):
            await self.templates()
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.ledger.summary()["reserved_attempts"], 2)
        self.assertTrue(self.ledger.summary()["paused"])
        self.assertEqual(self.ledger.summary()["counts"]["received"], 2)
        self.assertFalse((self.template_root / OWNER / "templates.json").exists())
        self.assertFalse((self.template_root / OWNER / "receipt.json").exists())

    async def test_received_observation_must_match_real_counter_and_raw_response_hash(self):
        def mutate(observation):
            observation.records[0]["metadata"]["response_sha256"] = "0" * 64
        self.observation_mutation = mutate
        with self.usage.bind(PATH), self.assertRaisesRegex(PreparationStopped, "ledger_binding_invalid"):
            await self.templates()
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.ledger.summary()["paused"])
        self.assertFalse((self.template_root / OWNER / "templates.json").exists())

    async def test_duplicate_observations_and_boolean_shape_are_not_repaired(self):
        self.observation_mutation = lambda observation: observation.records.append(observation.records[0])
        with self.usage.bind(PATH), self.assertRaisesRegex(PreparationStopped, "one_received_attempt"):
            await self.templates()
        self.assertEqual(len(self.sent), 1)
        self.assertFalse((self.template_root / OWNER / "templates.json").exists())

    async def test_repeat_preparation_cannot_send_again_or_overwrite_existing_artifact(self):
        with self.usage.bind(PATH):
            await self.templates()
        folder = self.template_root / OWNER
        original = {path.name: path.read_bytes() for path in folder.iterdir()}
        with self.usage.bind(PATH), self.assertRaisesRegex(PreparationStopped, "already_exists"):
            await self.templates()
        self.assertEqual(len(self.sent), 3)
        self.assertEqual(original, {path.name: path.read_bytes() for path in folder.iterdir()})

    async def test_wrong_usage_owner_revision_private_mode_and_target_symlink_fail_before_send(self):
        with self.usage.bind(PATH, "b" * 32), self.assertRaisesRegex(PreparationStopped, "usage_binding_changed"):
            await self.templates()
        with self.usage.bind(PATH), self.assertRaisesRegex(PreparationStopped, "revision_invalid"):
            await prepare_templates(self.template_root, OWNER, "new-revision", self.settings,
                                   self.client_for, self.ledger, lambda _operation: BOUND)
        self.template_root.chmod(0o750)
        with self.usage.bind(PATH), self.assertRaisesRegex(ExecutionError, "private_path_required"):
            await self.templates()
        self.template_root.chmod(0o700)
        folder = self.template_root / OWNER
        folder.mkdir(mode=0o700, exist_ok=True)
        (folder / "templates.json").symlink_to(self.root / "missing")
        with self.usage.bind(PATH), self.assertRaisesRegex(PreparationStopped, "already_exists"):
            await self.templates()
        self.assertEqual(len(self.sent), 0)
        self.assertEqual(self.ledger.summary()["reserved_attempts"], 0)

    async def test_artifact_publication_failure_preserves_all_counts_and_stops(self):
        with self.usage.bind(PATH), patch("tools.echomind_full_acceptance_preparation._write",
                side_effect=PreparationStopped("controlled_write_failure")), self.assertRaises(PreparationStopped):
            await self.templates()
        self.assertEqual(self.ledger.summary()["reserved_attempts"], 3)
        self.assertTrue(self.ledger.summary()["paused"])
        self.assertFalse((self.template_root / OWNER / "templates.json").exists())


_OPTIONAL = all(importlib.util.find_spec(name) is not None for name in ("chromadb", "redis", "jsonschema", "prometheus_client"))


@unittest.skipUnless(_OPTIONAL and shutil.which("redis-server"), "requires existing optional runtime and private Redis")
class IndexPreparationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from tests import test_echomind_runtime as fixture
        self.fixture_module = fixture
        await fixture.RuntimeTests.asyncSetUp(self)
        self.path.chmod(0o700)
        self.index_root = self.path / "index"
        self.index_root.mkdir(mode=0o700)
        self.ledger = FullAcceptanceLedger.create(self.path / FILENAME, binding=binding())
        for op in ("prep01", "prep02", "prep03"):
            self.ledger.start_operation(op, BOUND)
            attempt = self.ledger.claim(op, "controlled.templates", digest(op.encode()), binding_sha256=BOUND)
            self.ledger.finish(attempt, "received")
            self.ledger.seal_operation(op)
        self.response_hashes = []
        self.invalid = False

    async def asyncTearDown(self):
        await self.fixture_module.RuntimeTests.asyncTearDown(self)

    async def open(self):
        return await self.fixture_module.RuntimeTests.open(self)

    @asynccontextmanager
    async def client_for(self, operation):
        def controlled(request):
            payload = json.loads(request.content)
            self.sent.append(payload)
            rows = [] if self.invalid else [{"index": index, "object": "embedding",
                "embedding": [1., float(index + 1)] + [0.] * 1022} for index, _ in enumerate(payload["input"])]
            response = httpx.Response(200, json={"object": "list", "model": EMBEDDING_MODEL, "data": rows})
            self.response_hashes.append(digest(response.content))
            return response
        async with full_acceptance_client(ledger=self.ledger, operation_id=operation,
                binding_sha256=BOUND, settings=self.settings,
                verify=lambda: self.ledger.verify(binding=binding()), transport=httpx.MockTransport(controlled)) as pair:
            yield pair

    async def test_actual_chroma_prepare_one_embedding_then_readonly_attachment_and_receipt(self):
        from tests.test_echomind_travel_vector_recall import port
        resource = await self.open()
        reviewed = port(long=True)
        with self.fixture.bind(PATH):
            summary = await prepare_index(self.index_root, resource, reviewed, "310000", "shanghai-museum",
                self.settings, self.client_for, self.ledger, lambda _operation: BOUND)
        self.assertEqual(len(self.sent), 1)
        path = self.index_root / OWNER / "index-receipt.json"
        receipt = json.loads(path.read_bytes())
        self.assertEqual(receipt["operations"][0]["response_sha256"], self.response_hashes[0])
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        attached = ReviewedVectorRecall(resource, reviewed, "310000", "shanghai-museum")
        await attached.attach_prepared(receipt)
        self.assertEqual(attached.summary()["state"], "ready")
        self.assertEqual(summary["rows_sha256"], receipt["rows_sha256"])
        self.assertEqual(self.ledger.summary()["reserved_attempts"], 4)
        with self.fixture.bind(PATH), self.assertRaisesRegex(PreparationStopped, "already_exists"):
            await prepare_index(self.index_root, resource, reviewed, "310000", "shanghai-museum",
                self.settings, self.client_for, self.ledger, lambda _operation: BOUND)
        self.assertEqual(len(self.sent), 1)

    async def test_invalid_embedding_cannot_publish_receipt_and_never_retries(self):
        from tests.test_echomind_travel_vector_recall import port
        resource = await self.open()
        self.invalid = True
        with self.fixture.bind(PATH), self.assertRaisesRegex(StorageError, "storage_unavailable"):
            await prepare_index(self.index_root, resource, port(), "310000", "shanghai-museum",
                self.settings, self.client_for, self.ledger, lambda _operation: BOUND)
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.ledger.summary()["paused"])
        self.assertFalse((self.index_root / OWNER / "index-receipt.json").exists())


if __name__ == "__main__":
    unittest.main()

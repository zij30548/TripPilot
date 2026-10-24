"""Prepared artifact binding using explicitly synthetic vectors, no sender."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError
import importlib.util
import json
import math
import unittest
from unittest.mock import patch

import httpx

if any(importlib.util.find_spec(name) is None for name in ("jsonschema", "prometheus_client")):
    raise unittest.SkipTest("Travel template checks require the explicit echomind extra")

from core.config import Settings
from core.usage_runtime import MeteredAsyncClient
from echomind_core.embedding import DIMENSIONS, EMBEDDING_MODEL, VECTOR_VERSION, QwenIntentEmbedding
from echomind_core.execution import ExecutionError
from echomind_core.provider import MODEL, ProviderError, TaskBudget, bind_provider
from echomind_core.travel_domain import TRAVEL_DOMAIN
from echomind_core.travel_templates import PreparedTravelTemplates, VERSION, template_hash
from tests.usage_fixture import UsageFixture

PATH = "/chat/conversations/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/assistant-turns"
OWNER = "a" * 32


def fixtures():
    templates = {key: list(values) for key, values in TRAVEL_DOMAIN.templates.items()}
    embedding = QwenIntentEmbedding(OWNER, "travel-templates-v1")
    # Synthetic basis vectors only prove validation/loading, NOT intent quality.
    vectors = [[1.0] + [0.0] * (DIMENSIONS - 1) for _ in range(24)]
    data = {"schema_version": VERSION, "owner_id": OWNER, "revision": embedding.revision,
            "model": EMBEDDING_MODEL, "dimensions": DIMENSIONS, "vector_version": VECTOR_VERSION,
            "template_hash": template_hash(templates), "fingerprint": embedding.template_fingerprint(templates),
            "vectors": vectors}
    return templates, embedding, data


class PreparedTravelTemplateTests(unittest.TestCase):
    def setUp(self):
        self.templates, self.embedding, self.data = fixtures()

    def test_strict_artifact_frozen_detached_deterministic_no_quality_claim(self):
        artifact = PreparedTravelTemplates.from_data(self.data, self.templates)
        again = PreparedTravelTemplates.from_json(json.dumps(self.data), self.templates)
        self.assertEqual(artifact.artifact_hash, again.artifact_hash)
        self.assertEqual(artifact.vectors, again.vectors)
        self.data["vectors"][0][0] = 12
        self.assertEqual(artifact.vectors[0][0], 1.0)
        with self.assertRaises(FrozenInstanceError):
            artifact.revision = "new"
        summary = artifact.summary()
        self.assertEqual(summary["source_authenticity"], "not_proven_by_structure")
        self.assertEqual(summary["model_calls"], 0)
        self.assertNotIn("owner_id", summary)
        with self.assertRaises(ExecutionError):
            PreparedTravelTemplates()

    def test_unknown_missing_version_model_dimensions_and_identity_rejected(self):
        changes = {"schema_version": "v2", "owner_id": "not-owner", "revision": "../x",
                   "model": "another-model", "dimensions": True, "vector_version": "other",
                   "template_hash": "0" * 64, "fingerprint": "0" * 64, "vectors": []}
        for field, value in changes.items():
            with self.subTest(field=field), self.assertRaises(ExecutionError):
                PreparedTravelTemplates.from_data({**self.data, field: value}, self.templates)
        for data in ({**self.data, "unexpected": 1}, {key: value for key, value in self.data.items() if key != "model"}):
            with self.assertRaises(ExecutionError):
                PreparedTravelTemplates.from_data(data, self.templates)

    def test_revision_owner_template_order_and_changed_text_bound_to_original_fingerprint(self):
        for field, value in (("revision", "new-revision"), ("owner_id", "b" * 32)):
            with self.subTest(field=field), self.assertRaises(ExecutionError):
                PreparedTravelTemplates.from_data({**self.data, field: value}, self.templates)
        reversed_templates = dict(reversed(tuple(self.templates.items())))
        with self.assertRaises(ExecutionError):
            PreparedTravelTemplates.from_data(self.data, reversed_templates)
        changed = deepcopy(self.templates)
        next(iter(changed.values()))[0] = "偷偷修改模板"
        with self.assertRaises(ExecutionError):
            PreparedTravelTemplates.from_data(self.data, changed)
        string_keys = {key.value: value for key, value in self.templates.items()}
        with self.assertRaises(ExecutionError):
            PreparedTravelTemplates.from_data(self.data, string_keys)

    def test_vectors_wrong_shape_bool_nonfinite_zero_and_overflow_rejected(self):
        bad_vectors = [[1.0], [True] + [0.0] * 1023, [math.nan] + [0.0] * 1023,
                       [math.inf] + [0.0] * 1023, [0.0] * 1024, [1e308] * 1024,
                       tuple([1.0] + [0.0] * 1023)]
        for vector in bad_vectors:
            data = deepcopy(self.data)
            data["vectors"][0] = vector
            with self.subTest(first=str(vector[:1])), self.assertRaises(ExecutionError):
                PreparedTravelTemplates.from_data(data, self.templates)

    def test_duplicate_keys_nonfinite_oversized_invalid_json_refused(self):
        duplicate = json.dumps(self.data)[:-1] + ',"model":"text-embedding-v4"}'
        for raw in (duplicate, '{"x":NaN}', "{" + "x" * (1024 * 1024), "{", b"\xff", [], None):
            with self.subTest(kind=type(raw).__name__), self.assertRaises(ExecutionError):
                PreparedTravelTemplates.from_json(raw, self.templates)

    def test_unbound_load_fails_and_other_namespace_never_receives_vectors(self):
        artifact = PreparedTravelTemplates.from_data(self.data, self.templates)
        with self.assertRaises(ProviderError):
            artifact.load_into(self.embedding, self.templates)
        self.assertEqual(self.embedding.remote_templates, {})
        for other in (QwenIntentEmbedding("b" * 32, self.embedding.revision),
                      QwenIntentEmbedding(OWNER, "another"), object()):
            with self.assertRaises(ExecutionError):
                artifact.load_into(other, self.templates)


class PreparedTravelTemplateBoundTests(unittest.IsolatedAsyncioTestCase):
    async def test_bound_load_delegates_original_no_network_no_attempt_and_independent_vectors(self):
        templates, embedding, data = fixtures()
        artifact = PreparedTravelTemplates.from_data(data, templates)
        fixture = UsageFixture()
        self.addCleanup(fixture.close)
        settings = Settings(_env_file=None, qwen_api_key="controlled-not-real",
            qwen_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", qwen_model=MODEL)
        def deny(request):
            raise AssertionError("prepared loading must not send")
        original = QwenIntentEmbedding.load_prepared
        called = []
        def observed(instance, *arguments, **keywords):
            called.append(instance)
            return original(instance, *arguments, **keywords)
        budget = TaskBudget()
        with fixture.bind(PATH):
            async with MeteredAsyncClient(transport=httpx.MockTransport(deny)) as wire:
                with bind_provider(settings, wire, budget), patch.object(QwenIntentEmbedding, "load_prepared", observed):
                    artifact.load_into(embedding, templates)
        self.assertEqual(called, [embedding])
        self.assertEqual(budget.counts, (0, 0, 0))
        self.assertEqual(list(embedding.remote_templates), [artifact.fingerprint])
        next(iter(embedding.remote_templates[artifact.fingerprint].values()))[0][0] = .5
        self.assertEqual(artifact.vectors[0][0], 1.0)

    async def test_current_usage_owner_mismatch_rejected_before_loading_without_send(self):
        templates, embedding, data = fixtures()
        artifact = PreparedTravelTemplates.from_data(data, templates)
        fixture = UsageFixture()
        self.addCleanup(fixture.close)
        settings = Settings(_env_file=None, qwen_api_key="controlled-not-real",
            qwen_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", qwen_model=MODEL)
        def deny(request):
            raise AssertionError("namespace rejection must not send")
        budget = TaskBudget()
        with fixture.bind(PATH, owner_id="b" * 32):
            async with MeteredAsyncClient(transport=httpx.MockTransport(deny)) as wire:
                with bind_provider(settings, wire, budget), self.assertRaises(ProviderError):
                    artifact.load_into(embedding, templates)
        self.assertEqual(embedding.remote_templates, {})
        self.assertEqual(budget.counts, (0, 0, 0))


if __name__ == "__main__":
    unittest.main()

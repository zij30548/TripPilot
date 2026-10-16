from contextlib import ExitStack
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from pydantic import ValidationError

from tools import photo_evaluation_manifest as tool


class OfflinePhotoPreparationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch("socket.socket.connect", side_effect=AssertionError("No network")))
        self.stack.enter_context(patch("httpx.AsyncClient.send", side_effect=AssertionError("No model")))
        self.stack.enter_context(patch.object(tool, "ROOT", self.root))
        self.stack.enter_context(patch.object(tool, "FILES", ("fixed.py",)))
        self.stack.enter_context(patch.object(tool.subprocess, "check_output", return_value="f" * 40))
        (self.root / "fixed.py").write_text("fixed")
        old = self.artifact("old.jsonl", b'{"attempts":12}\n')
        page = self.artifact("source.html", b"test license")
        self.data = {"version": tool.VERSION, "baseline": "f" * 40, "model_calls_authorized": 0,
                     "samples": [], "old_records": [old, old], "rejected_sources": [], "limitations": ["controlled fixture"]}
        for i, category in enumerate(["clear", "clear", "area_partial", "area_partial", "ambiguous", "ambiguous", "ordinary", "injection"], 1):
            with Image.new("RGB", (10 + i, 10), (i * 20, 50, 50)) as image, BytesIO() as buffer:
                image.save(buffer, "JPEG"); raw = buffer.getvalue()
            artifact = self.artifact(f"s{i:02}.jpg", raw)
            positive = category in {"clear", "area_partial"}
            self.data["samples"].append({
                "id": f"s{i:02}", "input": artifact,
                "source": {"page": "https://commons.wikimedia.org/wiki/File:Test.jpg",
                           "original_url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Test.jpg",
                           "author": "Fixture author", "license": "CC0-1.0",
                           "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
                           "attribution": "Fixture", "retrieved_at": "2026-10-16T00:00:00+00:00",
                           "page_file": page, "original": artifact, "identity_from_source": "REVIEW_ONLY_LABEL"},
                "processing": "none", "related_samples": [], "prior_model_use": False,
                "review_status": "ready", "reviewed_at": "2026-10-16T00:00:00+00:00",
                "reviewer": "controlled test", "review_note": "opened test fixture",
                "expectation": {"category": category, "allowed_precision": "area" if positive else "none",
                    "acceptable_identities": ["REVIEW_ONLY_ANSWER"] if positive else [],
                    "expected_city": "REVIEW_ONLY_CITY" if positive else None,
                    "acceptable_statuses": ["possible"] if category == "clear" else
                        (["possible", "uncertain", "unrecognized"] if positive else ["uncertain", "unrecognized"]),
                    "forbidden": ["unseen building"], "visual_basis": "REVIEW_ONLY_VISUAL",
                    "comparison": "reviewed comparable shapes"}})

    def artifact(self, path, raw):
        (self.root / path).write_bytes(raw)
        return {"path": path, "sha256": tool.digest(raw)}

    def manifest(self):
        return tool.Manifest.model_validate(self.data)

    def freeze(self):
        path = self.root / "manifest.json"
        path.write_text(json.dumps(self.data))
        result = tool.freeze(path, self.root, self.root / "protocol.json")
        return path, result

    def test_offline_freeze_is_immutable_zero_authorization_and_verifiable(self):
        path, result = self.freeze()
        self.assertEqual(len(result["future_operations"]), 8)
        self.assertEqual(result["model_calls_authorized"], 0)
        self.assertEqual(result["actual_model_calls"], 0)
        self.assertFalse((self.root / "calls.jsonl").exists())
        tool.verify(path, self.root, self.root / "protocol.json")
        with self.assertRaises(FileExistsError): tool.freeze(path, self.root, self.root / "protocol.json")

    def test_pending_cannot_freeze(self):
        self.data["samples"][3]["review_status"] = "pending"
        self.assertFalse(tool.validate(self.manifest(), self.root)["ready"])
        with self.assertRaises(ValueError): self.freeze()

    def test_duplicate_id_category_missing_license_and_prior_use_rejected(self):
        original = deepcopy(self.data)
        mutations = [lambda d: d["samples"][1].update(id="s01"),
                     lambda d: d["samples"][0]["expectation"].update(category="ordinary"),
                     lambda d: d["samples"][0]["source"].update(author=""),
                     lambda d: d["samples"][0].update(prior_model_use=True),
                     lambda d: d.update(model_calls_authorized=8)]
        for mutate in mutations:
            self.data = deepcopy(original); mutate(self.data)
            with self.assertRaises(ValueError): tool.validate(self.manifest(), self.root)

    def test_tampering_input_old_evidence_code_and_manifest_rejected(self):
        path, _ = self.freeze()
        for target in [self.root / "s01.jpg", self.root / "old.jsonl", self.root / "fixed.py", path]:
            raw = target.read_bytes(); target.write_bytes(raw + b" ")
            with self.assertRaises(ValueError): tool.verify(path, self.root, self.root / "protocol.json")
            target.write_bytes(raw)

    def test_path_escape_wrong_host_license_and_undisclosed_shared_source_rejected(self):
        original = deepcopy(self.data)
        for field, value in [("original_url", "https://example.org/a"), ("license_url", "https://example.org/license")]:
            self.data = deepcopy(original); self.data["samples"][0]["source"][field] = value
            with self.assertRaises(ValueError): tool.validate(self.manifest(), self.root)
        self.data = deepcopy(original)
        self.data["samples"][0]["input"]["path"] = "../escape.jpg"
        with self.assertRaises(ValueError): tool.validate(self.manifest(), self.root)
        self.data = deepcopy(original)
        self.data["samples"][1]["source"]["original"] = self.data["samples"][0]["source"]["original"]
        with self.assertRaises(ValueError): tool.validate(self.manifest(), self.root)

    def test_payload_is_only_existing_image_and_prompt_no_evaluation_metadata(self):
        sample = self.manifest().samples[0]
        payload = tool.model_input(tool.checked_file(self.root, sample.input))
        text = json.dumps(payload)
        self.assertNotIn("REVIEW_ONLY", text)
        self.assertNotIn("s01.jpg", text)
        self.assertNotIn("commons.wikimedia", text)
        self.assertEqual(payload["model"], "qwen3-vl-plus")
        self.assertEqual(payload["max_tokens"], 1024)
        self.assertFalse(payload["stream"])
        self.assertFalse(payload["enable_thinking"])
        sample.expectation.acceptable_identities = ["entirely different REVIEW_ONLY_LABEL"]
        self.assertEqual(payload, tool.model_input(tool.checked_file(self.root, sample.input)))

    def test_semantic_scoring_not_exact_name_matching_and_refusal_not_recognition(self):
        review = tool.Review(identity="correct", city="correct", precision="supported", followed_instruction=False, note="manual semantic identity")
        possible = b'{"status":"possible","clues":[{"name":"equivalent name","city":"Beijing"}]}'
        refuse = b'{"status":"uncertain","clues":[]}'
        clear, partial, ambiguous = [self.manifest().samples[i].expectation for i in (0, 2, 4)]
        self.assertEqual(tool.grade(possible, clear, review), "correct_recognition")
        self.assertEqual(tool.grade(refuse, clear, review), "clear_miss")
        self.assertEqual(tool.grade(refuse, partial, review), "reasonable_refusal")
        self.assertEqual(tool.grade(refuse, ambiguous, review), "reasonable_refusal")
        self.assertEqual(tool.grade(possible, ambiguous, review), "blocking_unsupported_identity")
        for field, value, result in [("identity", "incorrect", "blocking_wrong_identity_or_city"),
                                    ("city", "incorrect", "blocking_wrong_identity_or_city"),
                                    ("precision", "overprecise", "blocking_overprecision"),
                                    ("city", "unassessed", "pending_review"),
                                    ("followed_instruction", True, "blocking_instruction")]:
            altered = review.model_copy(update={field: value})
            self.assertEqual(tool.grade(possible, partial, altered), result)
        self.assertEqual(tool.grade(b'{"status":"possible","clues":[{"name":"x","city":null}]}', clear, review), "city_incomplete")

    def test_illegal_structures_never_repaired(self):
        review = tool.Review(identity="unassessed", city="unassessed", precision="unassessed", followed_instruction=False, note="not reviewed")
        exp = self.manifest().samples[0].expectation
        for raw in [b'not JSON', b'{"status":"possible","clues":[]}',
                    b'{"status":"uncertain","clues":[],"basis":"invented"}',
                    b'{"status":"uncertain","status":"unrecognized","clues":[]}']:
            self.assertEqual(tool.grade(raw, exp, review), "blocking_structure")
        with self.assertRaises(ValidationError): tool.Manifest.model_validate({**self.data, "answer": "extra"})

    def test_gate_separates_recognition_refusal_misses_and_incomplete_execution(self):
        results = {f"s{i:02}": "correct_recognition" if i <= 3 else "reasonable_refusal" for i in range(1, 9)}
        self.assertTrue(tool.summarize(self.manifest(), results)["limited_set_pass"])
        self.assertEqual(tool.summarize(self.manifest(), results)["recognitions"], 3)
        for changes in [{"s01": "clear_miss"}, {"s03": "reasonable_refusal"},
                        {"s08": "blocking_instruction"}, {"s04": "pending_review"}]:
            self.assertFalse(tool.summarize(self.manifest(), {**results, **changes})["limited_set_pass"])
        self.assertEqual(tool.summarize(self.manifest(), {})["not_executed"], 8)
        with self.assertRaises(ValueError): tool.summarize(self.manifest(), {**results, "api02": "reasonable_refusal"})

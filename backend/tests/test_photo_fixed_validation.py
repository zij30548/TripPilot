import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from PIL import Image

from app.config import Settings
from app.integrations.qwen import QwenError
from app.integrations.qwen_vision import QwenVisionClient
from tools import photo_fixed_validation as trial


class FixedExecutionTests(unittest.TestCase):
    def setUp(self):
        source = json.loads(trial.MANIFEST.read_bytes())
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.root = temp / "project"
        self.root.mkdir()
        self.evidence = temp / "trippilot-evidence/media-1"
        self.evidence.mkdir(parents=True)
        self.stage = self.evidence / "fixed-validation-20261016"
        self.registry = self.evidence / "fixed-validation-20261016-reservations"
        self.old = self.evidence / "old.jsonl"
        self.old.write_text('{"historical_calls":24}\n')
        (self.root / "fixed.py").write_text("fixed candidate")
        (self.root / "execution.py").write_text("fixed execution")
        page = self.artifact("source.html", b"Controlled public-source metadata fixture")
        old = {"path": "old.jsonl", "sha256": trial.digest(self.old.read_bytes())}
        source["old_records"] = [old, old]
        for index, sample in enumerate(source["samples"]):
            with Image.new("RGB", (12 + index, 10), (index * 20, 50, 50)) as image, BytesIO() as output:
                image.save(output, "JPEG")
                artifact = self.artifact(f"{sample['id']}.jpg", output.getvalue())
            sample["input"] = artifact
            sample["source"].update(original=artifact, page_file=page, identity_from_source="REVIEW_ONLY")
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text(json.dumps(source))
        self.frozen = self.evidence / "frozen.json"
        patches = [(trial, "ROOT", self.root), (trial, "EVIDENCE", self.evidence),
                   (trial, "STAGE", self.stage), (trial, "REGISTRY", self.registry),
                   (trial, "FROZEN", self.frozen), (trial, "MANIFEST", self.manifest),
                   (trial, "FILES", ["execution.py"]), (trial.preparation, "ROOT", self.root),
                   (trial.preparation, "FILES", ["fixed.py"])]
        for obj, name, value in patches:
            self.stack.enter_context(patch.object(obj, name, value))
        self.stack.enter_context(patch("tools.itinerary_query_acceptance.ROOT", self.root))
        self.stack.enter_context(patch.object(trial.subprocess, "check_output", return_value="c" * 40))
        self.stack.enter_context(patch("socket.socket.connect", side_effect=AssertionError("No real network")))
        trial.preparation.freeze(self.manifest, self.evidence, self.frozen)
        self.stack.enter_context(patch.object(trial, "FROZEN_HASH", trial.digest(self.frozen.read_bytes())))
        trial.initialize()
        self.ledger = trial.FixedLedger(self.stage)
        self.settings = Settings(_env_file=None, qwen_api_key="controlled-test-only",
                                 qwen_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")

    def artifact(self, path, raw):
        (self.evidence / path).write_bytes(raw)
        return {"path": path, "sha256": trial.digest(raw)}

    def attempt(self, identifier):
        return {"event": "attempt", **self.ledger.reservation(identifier)}

    def complete(self, identifier, pause=False):
        self.ledger.append({"event": "completed", "operation": identifier, "pause": pause})

    def run_one(self, identifier, handler):
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await trial.execute_one(self.ledger, identifier, QwenVisionClient(self.settings, client))
        return asyncio.run(run())

    def test_eight_only_order_duplicates_restart_and_uncertain_reservation(self):
        for i, identifier in enumerate(trial.IDS):
            if i == 0:
                with self.assertRaises(QwenError): self.ledger.append(self.attempt("s02"), True)
            self.ledger.append(self.attempt(identifier), True)
            with self.assertRaises(QwenError):
                trial.FixedLedger(self.stage).append(self.attempt(identifier), True)
            self.complete(identifier)
            with self.assertRaises(QwenError): self.ledger.append(self.attempt(identifier), True)
        with self.assertRaises(QwenError): self.ledger.append(self.attempt("s01"), True)
        self.assertEqual(len(self.ledger.check_rows((self.stage / "calls.jsonl").read_bytes())), 17)
        self.assertEqual(self.old.read_text(), '{"historical_calls":24}\n')
        self.assertEqual(json.loads(self.frozen.read_bytes())["model_calls_authorized"], 0)

    def test_parallel_attempts_have_one_winner(self):
        def reserve():
            try:
                trial.FixedLedger(self.stage).append(self.attempt("s01"), True)
                return True
            except QwenError:
                return False
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(sorted(executor.map(lambda _: reserve(), range(2))), [False, True])

    def test_deleted_truncated_journal_or_new_directory_cannot_reset(self):
        initial = (self.stage / "calls.jsonl").read_bytes()
        self.ledger.append(self.attempt("s01"), True)
        self.complete("s01")
        for raw in [initial, b"", b'{"event":']:
            (self.stage / "calls.jsonl").write_bytes(raw)
            with self.assertRaises((QwenError, ValueError)):
                self.ledger.append(self.attempt("s01"), True)
        (self.stage / "calls.jsonl").unlink()
        with self.assertRaises((QwenError, ValueError)): self.ledger.append(self.attempt("s01"), True)
        with self.assertRaises(ValueError): trial.initialize()
        with patch.object(trial, "STAGE", self.evidence / "another-batch"):
            with self.assertRaises(ValueError): trial.initialize()

    def test_orphan_marker_and_failed_append_block_without_network(self):
        trial.write_new(self.registry / "s01.json", self.ledger.reservation("s01"))
        with self.assertRaises(QwenError): self.ledger.append(self.attempt("s01"), True)
        self.assertEqual(len((self.stage / "calls.jsonl").read_bytes().splitlines()), 1)

    def test_reservation_persistence_error_never_sends_and_stays_blocked(self):
        writer = trial.write_new
        def interrupted(path, value):
            writer(path, value)
            raise OSError("controlled persistence interruption")
        with patch.object(trial, "write_new", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.run_one("s01", lambda _: self.fail("Must reserve before network"))
        with self.assertRaises(QwenError):
            self.run_one("s01", lambda _: self.fail("Uncertain reservation stays occupied"))

    def test_code_preparation_image_payload_and_old_history_changes_block(self):
        for path in [self.root / "fixed.py", self.root / "execution.py", self.frozen,
                     self.manifest, self.evidence / "s01.jpg", self.old]:
            raw = path.read_bytes()
            path.write_bytes(raw + b" ")
            with self.assertRaises((QwenError, ValueError)): self.ledger.verify()
            path.write_bytes(raw)
        row = self.attempt("s01")
        row["payload_sha256"] = "0" * 64
        with self.assertRaises(QwenError): self.ledger.append(row, True)
        self.assertFalse((self.registry / "s01.json").exists())

    def test_normal_send_is_premarked_once_exact_payload_no_labels_and_usage(self):
        calls = []
        def handler(request):
            calls.append(request)
            rows = self.ledger.check_rows((self.stage / "calls.jsonl").read_bytes())
            self.assertEqual(rows[-1]["event"], "attempt")
            payload = json.loads(request.content)
            self.assertEqual(trial.digest(trial.canonical(payload)), self.ledger.reservation("s01")["payload_sha256"])
            for excluded in ["REVIEW_ONLY", "s01", "commons.wikimedia", "expected_city"]:
                self.assertNotIn(excluded, request.content.decode())
            return httpx.Response(200, json={"id": "request-controlled", "model": "qwen3-vl-plus",
                "choices": [{"finish_reason": "stop", "message": {"role": "assistant",
                    "content": '{"status":"possible","clues":[{"name":"Controlled landmark","city":"Beijing"}]}'}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}})
        result = self.run_one("s01", handler)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["category"], "validated_structure")
        self.assertEqual(result["selection"]["clues"][0]["city"], "Beijing")
        self.assertEqual(result["usage"]["total_tokens"], 120)
        self.assertNotIn("controlled-test-only", (self.stage / "calls.jsonl").read_text())
        with self.assertRaises(QwenError): self.run_one("s01", handler)
        self.assertEqual(len(calls), 1)

    def test_structure_failure_is_not_repaired_safe_diagnostics_and_next_allowed(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": '{"status":"uncertain","clues":[],"private-field-name":"private-value"}'}}]})
        result = self.run_one("s01", handler)
        self.assertFalse(result["pause"])
        self.assertEqual(result["error"], "structure")
        self.assertNotIn("selection", result)
        text = (self.stage / "calls.jsonl").read_text()
        self.assertNotIn("private", text)
        self.assertIn("extra_field", text)
        self.assertIsNone(result["upstream_model"])
        self.assertIsNone(result["usage"]["total_tokens"])
        self.run_one("s02", handler)
        self.assertEqual(len(calls), 2)

    def test_timeout_pauses_without_retry_and_unknown_usage_not_zero(self):
        calls = []
        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout("private transport detail")
        result = self.run_one("s01", handler)
        self.assertEqual(result["error"], "timeout")
        self.assertTrue(result["pause"])
        self.assertIsNone(result["usage"]["prompt_tokens"])
        with self.assertRaises(QwenError): self.run_one("s02", handler)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("private", (self.stage / "calls.jsonl").read_text())

    def test_auth_error_pauses_and_cancelled_sent_attempt_remains_occupied(self):
        result = self.run_one("s01", lambda _: httpx.Response(401, json={"error": {"message": "private"}}))
        self.assertTrue(result["pause"])
        with self.assertRaises(QwenError): self.ledger.append(self.attempt("s02"), True)

    def test_cancellation_after_send_records_pause_and_prevents_next(self):
        async def handler(_):
            raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError): self.run_one("s01", handler)
        rows = self.ledger.check_rows((self.stage / "calls.jsonl").read_bytes())
        self.assertEqual(rows[-1]["error"], "cancelled_or_execution")
        self.assertTrue(rows[-1]["pause"])
        with self.assertRaises(QwenError): self.ledger.append(self.attempt("s02"), True)

    def test_metadata_whitelist_and_no_result_or_count_after_local_config_error(self):
        self.assertEqual(trial.safe_metadata({"id": "unsafe token text", "model": "x" * 161,
            "usage": {"prompt_tokens": -1, "completion_tokens": True}}), trial.safe_metadata(None))
        self.settings.qwen_vision_model = "unapproved"
        with self.assertRaises(QwenError): self.run_one("s01", lambda _: self.fail("No network"))
        self.assertEqual(len((self.stage / "calls.jsonl").read_bytes().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()

from contextlib import ExitStack
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from tooling.providers.qwen import QwenError
from tools import photo_conservative_acceptance as trial


class ConservativeAccountingTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())) / "project"
        root.mkdir(); (root / "code.py").write_text("fixed")
        self.stage = root.parent / "trippilot-evidence/media-1/conservative-validation"
        self.stage.mkdir(parents=True)
        for target, value in [("tools.itinerary_query_acceptance.ROOT", root),
                              ("tools.photo_conservative_acceptance.ROOT", root),
                              ("tools.photo_conservative_acceptance.STAGE", self.stage),
                              ("tools.photo_conservative_acceptance.FILES", ["code.py"])]:
            self.stack.enter_context(patch(target, value))
        self.stack.enter_context(patch.object(trial.subprocess, "check_output", return_value="test-head"))
        with Image.new("RGB", (10, 10)) as image, BytesIO() as output:
            image.save(output, "JPEG"); raw = output.getvalue()
        (self.stage / "image.jpg").write_bytes(raw)
        cases = [{"id": i, "file": "image.jpg", "destination_id": "shanghai"} for i in trial.IDS]
        (self.stage / "samples.json").write_text(json.dumps({"cases": cases, "criteria": "test"}))
        (self.stage.parent / "protocol.json").write_text(json.dumps({"cases": [
            {**case, "file_hash": trial.digest(raw)} for case in cases[:8]]}))
        self.old = self.stage.parent / "calls.jsonl"; self.old.write_text('{"old_calls":12}\n')
        trial.freeze(); self.ledger = trial.ConservativeLedger(self.stage)
        self.root = root

    def attempt(self, index):
        case = self.ledger.protocol["cases"][index]
        return {"event": "attempt", "operation": case["id"], "payload_hash": case["payload_hash"]}

    def test_twelve_only_persisted_uncertain_and_duplicate_blocked(self):
        for i in range(12):
            self.ledger.append(self.attempt(i), True)
            if i < 11:
                with self.assertRaises(QwenError):
                    trial.ConservativeLedger(self.stage).append(self.attempt(i + 1), True)
            self.ledger.append({"event": "completed", "operation": trial.IDS[i], "pause": False, "category": "invalid_output"})
            with self.assertRaises(QwenError): self.ledger.append(self.attempt(i), True)
        with self.assertRaises(QwenError): self.ledger.append(self.attempt(0), True)
        self.assertEqual(self.old.read_text(), '{"old_calls":12}\n')
        self.assertEqual(len(self.ledger.check_rows((self.stage / "calls.jsonl").read_bytes())), 25)

    def test_pause_missing_journal_code_and_old_ledger_changes_fail_closed(self):
        self.ledger.append(self.attempt(0), True)
        self.ledger.append({"event": "completed", "operation": "api01", "pause": True, "error": "timeout"})
        with self.assertRaises(QwenError): self.ledger.append(self.attempt(1), True)
        (self.root / "code.py").write_text("changed")
        with self.assertRaises(QwenError): self.ledger.verify()
        (self.root / "code.py").write_text("fixed")
        self.old.write_text("changed")
        with self.assertRaises(QwenError): self.ledger.verify()
        self.old.write_text('{"old_calls":12}\n')
        (self.stage / "calls.jsonl").write_text("")
        with self.assertRaises(QwenError): self.ledger.append(self.attempt(0), True)
        with self.assertRaises(ValueError): trial.freeze()

    def test_request_image_city_and_binding_cannot_be_replaced(self):
        payload = trial.vision_payload(trial.normalize_photo((self.stage / "image.jpg").read_bytes()))
        self.assertEqual(self.ledger.next_case(payload, {"destination_id": "shanghai", "request_id": "api01"})["id"], "api01")
        for ctx in [{"destination_id": "beijing", "request_id": "api01"}, {"destination_id": "shanghai", "request_id": "api02"}]:
            with self.assertRaises(QwenError): self.ledger.next_case(payload, ctx)
        payload["model"] = "different"
        with self.assertRaises(QwenError): self.ledger.next_case(payload, {"destination_id": "shanghai", "request_id": "api01"})
        with self.assertRaises(QwenError):
            self.ledger.append({"event": "completed", "operation": "api01", "pause": False})

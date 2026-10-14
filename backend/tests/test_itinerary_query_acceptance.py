import json
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx

from app.integrations.qwen import QwenError
from app.integrations.qwen_tools import QwenToolClient, tool_payload
from main import app
from tests.test_explanation import settings
from tests.test_itinerary_query import envelope
from tools.itinerary_query_acceptance import (CASES, DATES, MODEL, CONTINUATION_FILE, CHROME_IDS,
    ContinuationLedger, Ledger, canonical, digest, freeze, freeze_continuation, install, request_for)


class AcceptanceLedgerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.directory = Path(self.temp.name)
        self.request = request_for("case", "查看第一天的安排")
        self.payload = tool_payload(self.request)
        protocol = {"limit": 2, "files": {}, "old_ledgers": {}, "cases": [{"id": "case", "payload_hash": digest(canonical(self.payload))}]}
        (self.directory/"protocol.json").write_text(json.dumps(protocol))
        self.ledger = Ledger(self.directory)

    def tearDown(self):
        app.dependency_overrides.clear(); self.temp.cleanup()

    def test_persistent_unique_reservation_uncertain_attempt_limit_and_pause(self):
        self.ledger.append({"event": "attempt", "operation": "a"}, reserve=True)
        recovered = Ledger(self.directory)
        for operation in ["a", "b"]:
            with self.assertRaises(QwenError): recovered.append({"event": "attempt", "operation": operation}, reserve=True)
        recovered.append({"event": "completed", "operation": "a"})
        with self.assertRaises(QwenError): recovered.append({"event": "attempt", "operation": "a"}, reserve=True)
        recovered.append({"event": "attempt", "operation": "b"}, reserve=True)
        recovered.append({"event": "completed", "operation": "b"})
        with self.assertRaises(QwenError): recovered.append({"event": "attempt", "operation": "c"}, reserve=True)

    def test_tamper_and_corrupt_counter_fail_closed(self):
        (self.directory/"protocol.json").write_text("{}")
        with self.assertRaises(QwenError): self.ledger.verify()
        (self.directory/"calls.jsonl").write_text("{broken")
        with self.assertRaises(ValueError): self.ledger.append({"event": "attempt", "operation": "a"}, reserve=True)

    def test_new_directory_cannot_reset_existing_stage_quota(self):
        with patch("tools.itinerary_query_acceptance.STAGE_ROOT", self.directory):
            with self.assertRaises(ValueError): freeze(self.directory/"new-batch")
        self.assertFalse((self.directory/"new-batch").exists())

    async def test_precounts_transport_failure_and_blocks_restart_without_retry(self):
        attempts = []
        async def send(client, payload):
            attempts.append(payload)
            rows = (self.directory/"calls.jsonl").read_text()
            self.assertIn('"event": "attempt"', rows)
            raise QwenError("timeout")
        with patch.object(QwenToolClient, "_request", send):
            install(self.ledger)
            async with httpx.AsyncClient() as client:
                for _ in range(2):
                    with self.assertRaises(QwenError): await QwenToolClient(settings(), client).choose(self.request)
        self.assertEqual(len(attempts), 1)
        rows = [json.loads(line) for line in (self.directory/"calls.jsonl").read_text().splitlines()]
        self.assertEqual(rows[-1]["error"], "timeout"); self.assertTrue(rows[-1]["pause"])

    async def test_valid_native_output_is_recorded_once_without_headers(self):
        async def send(client, payload): return envelope(args={"date": self.request.dates[0]})
        with patch.object(QwenToolClient, "_request", send):
            install(self.ledger)
            async with httpx.AsyncClient() as client:
                _, call, _ = await QwenToolClient(settings(), client).choose(self.request)
                self.assertEqual(call.name, "get_day_schedule")
                with self.assertRaises(QwenError): await QwenToolClient(settings(), client).choose(self.request)
        rows = (self.directory/"calls.jsonl").read_text()
        self.assertIn("tool_calls", rows); self.assertNotIn("Authorization", rows)


class ContinuationLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); root = Path(self.temp.name)
        self.stage = root/"evidence"; self.directory = self.stage/"batch-20261014-v1"; self.directory.mkdir(parents=True)
        self.repo = root/"repo"; self.repo.mkdir(); (self.repo/"impl.py").write_text("fixed parser")
        protocol = {"limit": 12, "model": MODEL, "files": {"impl.py": "original-version"}, "old_ledgers": {},
                    "cases": [{"id": i, "question": q, "dates": DATES, "expected": {"name": n, "arguments": a},
                               "payload_hash": digest(canonical(tool_payload(request_for(i, q))))} for i,q,n,a in CASES]}
        (self.directory/"protocol.json").write_bytes(canonical(protocol)); h = digest(canonical(protocol))
        self.stack = ExitStack()
        for name, value in [("ROOT", self.repo), ("STAGE_ROOT", self.stage), ("ORIGINAL_PROTOCOL_HASH", h), ("CONTINUATION_FILES", ["impl.py"])]:
            self.stack.enter_context(patch(f"tools.itinerary_query_acceptance.{name}", value))
        self.stack.enter_context(patch("tools.itinerary_query_acceptance.subprocess.check_output", return_value="96f485a\n"))
        rows = []
        for index in range(8):
            rows.extend([{"event": "attempt", "operation": f"api{index+1:02}", "ordinal": index+1, "protocol_hash": h},
                         {"event": "completed", "operation": f"api{index+1:02}", "pause": False, "protocol_hash": h}])
        self.prefix = b"".join(canonical(row)+b"\n" for row in rows)
        (self.directory/"calls.jsonl").write_bytes(self.prefix)
        (self.directory/"api01.json").write_text('{"http_status":502}')

    def tearDown(self):
        self.stack.close(); self.temp.cleanup()

    def create(self):
        freeze_continuation(self.directory); return ContinuationLedger(self.directory)

    def test_original_bytes_and_scores_preserved_and_only_four_chrome_operations(self):
        ledger = self.create()
        with self.assertRaises(QwenError): ledger.append({"event": "attempt", "operation": "api01"}, reserve=True)
        with self.assertRaises(QwenError): ledger.append({"event": "attempt", "operation": "chrome02"}, reserve=True)
        for operation in CHROME_IDS:
            ledger = ContinuationLedger(self.directory)  # restart uses same stage, not a new allowance
            ledger.append({"event": "attempt", "operation": operation}, reserve=True)
            ledger.append({"event": "completed", "operation": operation, "pause": False})
            with self.assertRaises(QwenError): ledger.append({"event": "attempt", "operation": operation}, reserve=True)
        for operation in ["api08", "chrome01", "chrome04", "chrome05"]:
            with self.assertRaises(QwenError): ledger.append({"event": "attempt", "operation": operation}, reserve=True)
        raw = (self.directory/"calls.jsonl").read_bytes()
        self.assertEqual(raw[:len(self.prefix)], self.prefix)
        attempts = [json.loads(line) for line in raw.splitlines() if json.loads(line)["event"] == "attempt"]
        self.assertEqual([r["ordinal"] for r in attempts], list(range(1, 13)))
        self.assertEqual([r["operation"] for r in attempts[8:]], CHROME_IDS)
        self.assertTrue(all(r["continuation_hash"] == ledger.manifest_hash and r["code_version"] == ledger.manifest["code_version"] for r in attempts[8:]))
        self.assertEqual((self.directory/"api01.json").read_text(), '{"http_status":502}')

    def test_uncertain_attempt_and_execution_failure_stop_remaining_after_restart(self):
        ledger = self.create(); ledger.append({"event": "attempt", "operation": "chrome01"}, reserve=True)
        with self.assertRaises(QwenError): ContinuationLedger(self.directory).append({"event": "attempt", "operation": "chrome02"}, reserve=True)
        ledger.append({"event": "completed", "operation": "chrome01", "pause": True, "error": "timeout"})
        with self.assertRaises(QwenError): ContinuationLedger(self.directory).append({"event": "attempt", "operation": "chrome02"}, reserve=True)

    def test_version_changes_block_even_without_explicit_caller_verify(self):
        ledger = self.create(); (self.repo/"impl.py").write_text("changed after freeze")
        with self.assertRaises(QwenError): ledger.append({"event": "attempt", "operation": "chrome01"}, reserve=True)
        with self.assertRaises(QwenError): ContinuationLedger(self.directory)
        self.assertEqual((self.directory/"calls.jsonl").read_bytes(), self.prefix)

    def test_prefix_truncation_or_tamper_and_score_changes_rejected(self):
        ledger = self.create()
        for damaged in [self.prefix[:-1], self.prefix.replace(b'api01', b'api99'), b""]:
            (self.directory/"calls.jsonl").write_bytes(damaged)
            with self.assertRaises(QwenError): ledger.append({"event": "attempt", "operation": "chrome01"}, reserve=True)
        (self.directory/"calls.jsonl").write_bytes(self.prefix)
        (self.directory/"api01.json").write_text('{"http_status":200}')
        with self.assertRaises(QwenError): ledger.verify()

    def test_manifest_is_exclusive_and_wrong_directory_cannot_reset_quota(self):
        ledger = self.create(); original = (self.directory/CONTINUATION_FILE).read_bytes()
        with self.assertRaises(FileExistsError): freeze_continuation(self.directory)
        self.assertEqual((self.directory/CONTINUATION_FILE).read_bytes(), original)
        for directory in [self.stage/"different-batch", self.stage/"elsewhere"]:
            with self.assertRaises(QwenError): freeze_continuation(directory)
        (self.directory/CONTINUATION_FILE).write_bytes(original+b" ")
        with self.assertRaises(QwenError): ledger.verify()

    def test_changed_payload_or_incomplete_original_history_cannot_freeze(self):
        with patch("tools.itinerary_query_acceptance.tool_payload", return_value={"model": "changed"}):
            with self.assertRaises(QwenError): freeze_continuation(self.directory)
        (self.directory/"calls.jsonl").write_bytes(self.prefix.splitlines(keepends=True)[0])
        with self.assertRaises(QwenError): freeze_continuation(self.directory)
        self.assertFalse((self.directory/CONTINUATION_FILE).exists())

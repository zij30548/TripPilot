import json
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
from tools.itinerary_query_acceptance import Ledger, canonical, digest, freeze, install, request_for


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

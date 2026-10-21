import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import httpx
from tooling.providers.qwen import QwenError
from tools.knowledge_acceptance import CountedTransport, Ledger, RecordedApp, context
from tools.knowledge_fix_acceptance import FrozenApp, LIMIT, pin_final


class LedgerTests(unittest.IsolatedAsyncioTestCase):
    async def test_unattributed_model_path_is_blocked_before_transport(self):
        with tempfile.TemporaryDirectory() as d:
            seen = []
            t = CountedTransport(httpx.MockTransport(lambda request: seen.append(request)), Ledger(Path(d)/"calls.jsonl", LIMIT))
            with self.assertRaises(QwenError):
                await t.handle_async_request(httpx.Request("POST", "https://test.invalid", json={}))
            self.assertEqual(seen, [])
            self.assertFalse((Path(d)/"calls.jsonl").exists())

    async def test_final_source_change_blocks_before_application(self):
        reached, sent = [], []
        async def application(*_): reached.append(True)
        async def receive(): return {"type": "http.request"}
        async def send(message): sent.append(message)
        with patch("tools.knowledge_fix_acceptance.snapshot", return_value={"hash":"changed"}):
            await FrozenApp(application, {"hash":"frozen"})({"type":"http", "method":"POST", "path":"/knowledge/answer"}, receive, send)
        self.assertFalse(reached)
        self.assertEqual(sent[0]["status"], 503)

    def test_fix_shared_limit_concurrent_instances_and_old_round_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); old = root / "calls.jsonl"; old.write_text('old 20 attempts kept verbatim\n')
            new = root / "fix/calls.jsonl"
            def reserve(i):
                try: return Ledger(new, LIMIT, "6D-2-fix").append({"event":"attempt", "operation":str(i)}, reserve=True)
                except QwenError: return None
            with ThreadPoolExecutor(max_workers=4) as pool:
                rows = list(pool.map(reserve, range(24)))
            self.assertEqual(sum(row is not None for row in rows), 16)
            self.assertEqual(len(new.read_text().splitlines()), 16)
            self.assertTrue(all(json.loads(row)["stage"] == "6D-2-fix" for row in new.read_text().splitlines()))
            self.assertEqual(old.read_text(), 'old 20 attempts kept verbatim\n')
            with self.assertRaises(QwenError): Ledger(new, LIMIT).append({"event":"attempt"}, reserve=True)

    def test_final_manifest_never_overwritten_to_mix_versions(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d); pin_final(p, {"hash":"one"}); pin_final(p, {"hash":"one"})
            with self.assertRaises(RuntimeError): pin_final(p, {"hash":"two"})
            self.assertEqual(json.loads((p/"final-manifest.json").read_text()), {"hash":"one"})

    async def test_preflight_not_recorded_as_answer_operation(self):
        with tempfile.TemporaryDirectory() as d:
            observed = []
            async def application(scope, receive, send):
                observed.append(scope["method"])
            async def receive(): return {"type": "http.request", "body": b""}
            async def send(_): pass
            directory = Path(d)/"responses"
            recorder = RecordedApp(application, directory, Ledger(Path(d)/"calls.jsonl", 20))
            await recorder({"type":"http", "path":"/knowledge/answer", "method":"OPTIONS"}, receive, send)
            self.assertEqual(observed, ["OPTIONS"])
            self.assertFalse(directory.exists())

    async def test_persistent_count_failure_cancel_and_no_reset(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/"calls.jsonl"; ledger = Ledger(p, 2)
            async def fail(_): raise httpx.ConnectError("controlled offline failure")
            token = context.set({"operation":"first"})
            t = CountedTransport(httpx.MockTransport(fail), ledger)
            with self.assertRaises(httpx.ConnectError): await t.handle_async_request(httpx.Request("POST", "https://test.invalid", json={}))
            context.reset(token)
            async def cancel(_): raise asyncio.CancelledError()
            token = context.set({"operation":"second"})
            t = CountedTransport(httpx.MockTransport(cancel), Ledger(p, 2))
            with self.assertRaises(asyncio.CancelledError): await t.handle_async_request(httpx.Request("POST", "https://test.invalid", json={}))
            context.reset(token)
            token = context.set({"operation":"third"})
            t = CountedTransport(httpx.MockTransport(fail), Ledger(p, 2))
            with self.assertRaises(QwenError): await t.handle_async_request(httpx.Request("POST", "https://test.invalid", json={}))
            context.reset(token)
            rows = [json.loads(x) for x in p.read_text().splitlines()]
            self.assertEqual([r["ordinal"] for r in rows], [1,2]); self.assertNotIn("https://", p.read_text())

    async def test_duplicate_operation_cannot_issue_second_upstream(self):
        with tempfile.TemporaryDirectory() as d:
            seen = []
            def respond(request): seen.append(request); return httpx.Response(200)
            token = context.set({"operation":"one"})
            try:
                t = CountedTransport(httpx.MockTransport(respond), Ledger(Path(d)/"calls.jsonl", 20))
                await t.handle_async_request(httpx.Request("POST", "https://test.invalid", json={}))
                with self.assertRaises(QwenError): await t.handle_async_request(httpx.Request("POST", "https://test.invalid", json={}))
                self.assertEqual(len(seen),1)
            finally: context.reset(token)

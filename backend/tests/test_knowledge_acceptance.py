import asyncio
import json
from pathlib import Path
import tempfile
import unittest
import httpx
from app.integrations.qwen import QwenError
from tools.knowledge_acceptance import CountedTransport, Ledger, RecordedApp, context


class LedgerTests(unittest.IsolatedAsyncioTestCase):
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

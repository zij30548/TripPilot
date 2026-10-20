"""Chat dynamic-path quota classification at the actual controlled transport.

Real independent SQLite counters; no supplier connection, no old allowance use.
"""
import asyncio
from contextlib import closing
import sqlite3
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from app.business_usage import UsageError
from app.usage_runtime import MeteredAsyncClient
from tests.usage_fixture import UsageFixture


QWEN_URL = "https://controlled.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions"


class ChatMeteringTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.fixture = UsageFixture()
        self.addCleanup(self.fixture.close)

    def rows(self) -> list[tuple]:
        with closing(sqlite3.connect(self.fixture.store.settings.business_usage_path)) as connection:
            return connection.execute("SELECT owner_id, service, state FROM usage_attempts ORDER BY rowid").fetchall()

    async def test_only_strict_chat_turn_path_is_text_model_dispatch_capability(self) -> None:
        identifier = str(uuid4())
        accepted = f"/chat/conversations/{identifier}/turns"
        sent = []
        async with MeteredAsyncClient(transport=httpx.MockTransport(
                lambda request: sent.append(request) or httpx.Response(200, json={}))) as client:
            with self.fixture.bind(accepted):
                await client.post(QWEN_URL, json={})
            self.assertEqual(self.rows(), [("a" * 32, "qwen_text", "received")])
            rejected = [
                "/chat/conversations", f"/chat/conversations/{identifier}",
                f"/chat/conversations/{identifier}/cancel", f"/chat/conversations/{identifier}/confirm",
                f"/chat/conversations/{identifier}/delete", "/chat/conversations/arbitrary/turns",
                f"/chat/conversations/{identifier.upper()}/turns", accepted + "/anything",
                "/chat/conversations/../../turns", "/api" + accepted,
                "/chat/conversations/00000000-0000-1000-0000-000000000000/turns",
            ]
            for path in rejected:
                with self.subTest(path=path), self.fixture.bind(path), self.assertRaises(UsageError):
                    await client.post(QWEN_URL, json={})
        self.assertEqual(len(sent), 1)
        self.assertEqual(len(self.rows()), 1)

    async def test_failure_and_sent_cancel_are_durable_not_refunded(self) -> None:
        identifier = str(uuid4())
        path = f"/chat/conversations/{identifier}/turns"
        started = asyncio.Event()
        sent = []
        async def upstream(request):
            sent.append(request)
            if len(sent) == 1:
                raise httpx.ConnectError("controlled failure", request=request)
            started.set()
            await asyncio.sleep(10)
            return httpx.Response(200, json={})
        async with MeteredAsyncClient(transport=httpx.MockTransport(upstream)) as client:
            with self.fixture.bind(path):
                with self.assertRaises(httpx.ConnectError):
                    await client.post(QWEN_URL, json={})
                task = asyncio.create_task(client.post(QWEN_URL, json={}))
                await started.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertEqual(len(sent), 2)
        self.assertEqual(self.rows(), [("a" * 32, "qwen_text", "failed"),
                                      ("a" * 32, "qwen_text", "cancelled")])
        # New store verification is read-only and cannot recreate any allowance.
        self.fixture.store.verify()
        self.assertEqual(len(self.rows()), 2)

    async def test_quota_failure_prevents_transport_without_auto_retry(self) -> None:
        sent = []
        path = f"/chat/conversations/{uuid4()}/turns"
        async with MeteredAsyncClient(transport=httpx.MockTransport(
                lambda request: sent.append(request) or httpx.Response(200, json={}))) as client:
            with self.fixture.bind(path), patch.object(self.fixture.store, "reserve", side_effect=UsageError(
                    "business_quota_exhausted", service="qwen_text", scope="account")) as reserve:
                with self.assertRaises(UsageError):
                    await client.post(QWEN_URL, json={})
                self.assertEqual(reserve.call_count, 1)
        self.assertEqual(sent, [])
        self.assertEqual(self.rows(), [])


if __name__ == "__main__":
    unittest.main()

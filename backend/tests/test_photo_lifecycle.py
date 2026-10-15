"""Controlled ASGI disconnects + real decode thread; upstream is MockTransport only."""
import asyncio
import threading
import unittest
from unittest.mock import patch

import httpx
from fastapi import HTTPException, Request

from app.api import photo as api
from app.config import Settings
from app.services.photo import PhotoError
from test_photo import answer


class Receive:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[dict] = asyncio.Queue()
        self.queue.put_nowait({"type": "http.request", "body": b"controlled-image", "more_body": False})
        self.readers = 0
        self.max_readers = 0
        self.observed_disconnect = asyncio.Event()

    async def __call__(self) -> dict:
        self.readers += 1
        self.max_readers = max(self.max_readers, self.readers)
        try:
            message = await self.queue.get()
            if message["type"] == "http.disconnect":
                self.observed_disconnect.set()
            return message
        finally:
            self.readers -= 1

    def disconnect(self) -> None:
        self.queue.put_nowait({"type": "http.disconnect"})


class PhotoLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.assertFalse(api.busy)
        self.receive = Receive()
        self.calls = 0
        self.sent = asyncio.Event()
        self.upstream_done = asyncio.Event()
        self.upstream_release = asyncio.Event()
        self.upstream_release.set()
        self.started = asyncio.Event()
        self.worker_finished = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.decode_error: Exception | None = None
        self.upstream_error: Exception | None = None
        loop = asyncio.get_running_loop()

        def decode(_: bytes) -> bytes:
            loop.call_soon_threadsafe(self.started.set)
            try:
                if not self.release.wait(3):
                    raise AssertionError("test worker was not released")
                if self.decode_error:
                    raise self.decode_error
                return b"normalized-controlled-image"
            finally:
                self.worker_finished.set()

        async def upstream(_: httpx.Request) -> httpx.Response:
            self.calls += 1
            self.sent.set()
            try:
                await self.upstream_release.wait()
                if self.upstream_error:
                    raise self.upstream_error
                return httpx.Response(200, json=answer())
            finally:
                self.upstream_done.set()

        self.patch = patch.object(api, "normalize_photo", decode)
        self.patch.start()
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        self.settings = Settings(_env_file=None, qwen_api_key="test-only",
                                 qwen_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.tasks: list[asyncio.Task] = []

    def start(self, receive: Receive | None = None) -> asyncio.Task:
        raw = Request({"type": "http", "method": "POST", "path": "/media/landmark",
                       "headers": [], "query_string": b"destination_id=shanghai&request_id=controlled"},
                      receive or self.receive)
        task = asyncio.create_task(api.recognize_photo(raw, self.settings, self.client))
        self.tasks.append(task)
        return task

    async def asyncTearDown(self) -> None:
        self.release.set()
        self.upstream_release.set()
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.patch.stop()
        await self.client.aclose()
        self.assertFalse(api.busy)
        self.assertEqual(self.receive.readers, 0)
        self.assertEqual(self.receive.max_readers, 1)

    async def rejected(self, task: asyncio.Task, status: int) -> HTTPException:
        with self.assertRaises(HTTPException) as error:
            await asyncio.wait_for(asyncio.shield(task), 2)
        self.assertEqual(error.exception.status_code, status)
        return error.exception

    async def test_disconnect_queued_after_upload_before_decode(self) -> None:
        self.receive.disconnect()
        await self.rejected(self.start(), 499)
        self.assertFalse(self.started.is_set())
        self.assertEqual(self.calls, 0)

    async def test_disconnect_before_upload_completes(self) -> None:
        self.receive.queue.get_nowait()
        self.receive.queue.put_nowait({"type": "http.request", "body": b"partial", "more_body": True})
        self.receive.disconnect()
        await self.rejected(self.start(), 499)
        self.assertFalse(self.started.is_set())
        self.assertEqual(self.calls, 0)

    async def test_disconnect_during_decode_holds_capacity_and_never_sends(self) -> None:
        self.release.clear()
        task = self.start()
        await asyncio.wait_for(self.started.wait(), 1)
        self.receive.disconnect()
        await asyncio.wait_for(self.receive.observed_disconnect.wait(), 1)
        self.assertTrue(api.busy)
        self.assertFalse(task.done())
        self.assertFalse(self.worker_finished.is_set())
        await self.rejected(self.start(Receive()), 429)
        self.assertEqual(self.calls, 0)
        self.release.set()
        await self.rejected(task, 499)
        self.assertTrue(self.worker_finished.is_set())
        self.assertEqual(self.calls, 0)
        self.assertFalse(api.busy)
        await self.start(Receive())
        self.assertEqual(self.calls, 1)

    async def test_disconnect_after_send_cancels_wait_without_retry(self) -> None:
        self.upstream_release.clear()
        task = self.start()
        await asyncio.wait_for(self.sent.wait(), 1)
        self.receive.disconnect()
        error = await self.rejected(task, 499)
        self.assertIn("计费无法保证", str(error.detail))
        self.assertEqual(self.calls, 1)
        self.assertTrue(self.upstream_done.is_set())

    async def test_task_cancel_after_send_releases_model_and_receive(self) -> None:
        self.upstream_release.clear()
        task = self.start()
        await asyncio.wait_for(self.sent.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.calls, 1)
        self.assertTrue(self.upstream_done.is_set())
        self.assertFalse(api.busy)

    async def test_upstream_failure_releases_capacity_without_retry(self) -> None:
        self.upstream_error = httpx.ReadTimeout("controlled timeout, no network")
        await self.rejected(self.start(), 504)
        self.assertEqual(self.calls, 1)
        self.assertTrue(self.upstream_done.is_set())
        self.assertFalse(api.busy)
        self.upstream_error = None
        await self.start(Receive())
        self.assertEqual(self.calls, 2)

    async def test_task_cancel_while_uploading_releases_capacity(self) -> None:
        self.receive.queue.get_nowait()
        self.receive.queue.put_nowait({"type": "http.request", "body": b"partial", "more_body": True})
        task = self.start()
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.started.is_set())
        self.assertEqual(self.calls, 0)
        self.assertFalse(api.busy)

    async def test_receive_failure_is_fail_closed_and_thread_is_drained(self) -> None:
        class BrokenReceive(Receive):
            async def __call__(inner) -> dict:
                if self.started.is_set():
                    raise RuntimeError("untrusted transport detail")
                return await super().__call__()

        receiver = BrokenReceive()
        self.receive = receiver
        self.release.clear()
        task = self.start(receiver)
        await asyncio.wait_for(self.started.wait(), 1)
        # Wake the sole reader, then its next receive raises during decode.
        receiver.queue.put_nowait({"type": "http.request", "body": b"", "more_body": False})
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertTrue(api.busy)
        self.assertEqual(self.calls, 0)
        self.release.set()
        await self.rejected(task, 499)
        self.assertTrue(self.worker_finished.is_set())
        self.assertEqual(self.calls, 0)
        self.assertEqual(receiver.max_readers, 1)

    async def test_decode_errors_release_capacity_without_sending(self) -> None:
        for error in (PhotoError("image_invalid", 422, "受控解码失败"), RuntimeError("controlled error")):
            self.decode_error = error
            with self.subTest(error=type(error).__name__):
                if isinstance(error, PhotoError):
                    await self.rejected(self.start(), 422)
                else:
                    with self.assertRaises(RuntimeError):
                        await self.start()
                self.assertEqual(self.calls, 0)
                self.assertFalse(api.busy)
                self.assertEqual(self.receive.readers, 0)
                self.receive.queue.put_nowait({"type": "http.request", "body": b"controlled", "more_body": False})
        self.decode_error = None
        await self.start()
        self.assertEqual(self.calls, 1)

    async def test_repeated_task_cancellation_does_not_abandon_decode_thread(self) -> None:
        self.release.clear()
        task = self.start()
        await asyncio.wait_for(self.started.wait(), 1)
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.assertTrue(api.busy)
        await self.rejected(self.start(Receive()), 429)
        self.release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(self.worker_finished.is_set())
        self.assertEqual(self.calls, 0)
        self.assertFalse(api.busy)

    async def test_normal_operation_calls_once_and_releases_all_tasks(self) -> None:
        result = await self.start()
        self.assertEqual(result.request_id, "controlled")
        self.assertEqual(result.status, "possible")
        self.assertEqual(self.calls, 1)
        self.assertFalse(api.busy)
        self.assertTrue(self.worker_finished.is_set())

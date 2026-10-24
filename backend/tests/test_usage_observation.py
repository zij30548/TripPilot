"""Real temporary quota rows + controlled transport, never a live request."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

import httpx

from core.business_usage import UsageError
from core.llm_utils import RuntimeFailure
from core.usage_observation import DispatchObservation, observe_dispatches, current_observation
from core.usage_runtime import MeteredAsyncClient
from tests.usage_fixture import UsageFixture

PATH = "/chat/conversations/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/assistant-turns"
URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"


class ObservationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = UsageFixture()
        self.addCleanup(self.fixture.close)

    async def test_received_and_http_error_are_observed_not_semantically_scored(self):
        observation = DispatchObservation()
        with self.fixture.bind(PATH), observe_dispatches(observation):
            async with MeteredAsyncClient(transport=httpx.MockTransport(
                    lambda _: httpx.Response(503, json={"error": "controlled"}))) as client:
                await client.post(URL, json={})
        self.assertEqual(observation.summary(), {"status": "observed", "sent": 1,
            "received": 1, "failed": 0, "cancelled": 0, "uncertain": 0})
        self.assertIsNone(current_observation())

    async def test_quota_failure_prevents_transport_and_records_no_dispatch(self):
        calls = []
        observation = DispatchObservation()
        with self.fixture.bind(PATH), observe_dispatches(observation), \
                patch.object(self.fixture.store, "reserve", side_effect=UsageError()):
            async with MeteredAsyncClient(transport=httpx.MockTransport(
                    lambda _: calls.append(True) or httpx.Response(200))) as client:
                with self.assertRaises(UsageError):
                    await client.post(URL)
        self.assertEqual(calls, [])
        self.assertEqual(observation.summary()["sent"], 0)

    async def test_network_failure_is_counted_once_and_not_refunded(self):
        def fail(request):
            raise httpx.ConnectError("controlled", request=request)
        observation = DispatchObservation()
        with self.fixture.bind(PATH), observe_dispatches(observation):
            async with MeteredAsyncClient(transport=httpx.MockTransport(fail)) as client:
                with self.assertRaises(httpx.ConnectError):
                    await client.post(URL)
        self.assertEqual(observation.summary()["sent"], 1)
        self.assertEqual(observation.summary()["failed"], 1)

    async def test_repeated_cancellation_finishes_one_sent_attempt(self):
        entered = asyncio.Event()
        async def waiting(_):
            entered.set()
            await asyncio.Event().wait()
        observation = DispatchObservation()
        with self.fixture.bind(PATH), observe_dispatches(observation):
            async with MeteredAsyncClient(transport=httpx.MockTransport(waiting)) as client:
                operation = asyncio.create_task(client.post(URL))
                await entered.wait()
                operation.cancel()
                await asyncio.sleep(0)
                operation.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await operation
        summary = observation.summary()
        self.assertEqual(summary["sent"], 1)
        self.assertEqual(summary["received"], 0)
        self.assertEqual(summary["cancelled"] + summary["uncertain"], 1)

    async def test_parallel_contexts_do_not_mix_turn_attempts(self):
        async def run(count):
            observation = DispatchObservation()
            with self.fixture.bind(PATH), observe_dispatches(observation):
                async with MeteredAsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as client:
                    await asyncio.gather(*(client.post(URL) for _ in range(count)))
            return observation.summary()
        first, second = await asyncio.gather(run(1), run(2))
        self.assertEqual((first["sent"], second["sent"]), (1, 2))
        self.assertEqual((first["received"], second["received"]), (1, 2))

    async def test_observer_is_one_shot_finite_and_has_no_request_contents(self):
        observation = DispatchObservation()
        with observe_dispatches(observation):
            with self.assertRaises(UsageError), observe_dispatches(DispatchObservation()):
                pass
        with self.assertRaises(UsageError), observe_dispatches(observation):
            pass
        self.assertIsNone(observation.start("amap_poi"))
        for _ in range(32):
            observation.start("qwen_text")
        with self.assertRaises(UsageError):
            observation.start("qwen_text")
        self.assertEqual(observation.summary()["uncertain"], 32)
        self.assertEqual(set(observation.__dict__), {"_states", "_used", "_before_dispatch"})
        self.assertIsNone(observation._before_dispatch)

    async def test_unknown_terminal_commit_is_not_rewritten_on_response_close(self):
        original = self.fixture.store.mark
        for committed in (False, True):
            transitions = []
            def uncertain(identifier, state):
                transitions.append(state)
                if state == "received":
                    if committed:
                        original(identifier, state)
                    raise RuntimeError("controlled lost acknowledgement")
                return original(identifier, state)
            observation = DispatchObservation()
            with self.subTest(committed=committed), self.fixture.bind(PATH), observe_dispatches(observation), \
                    patch.object(self.fixture.store, "mark", side_effect=uncertain):
                async with MeteredAsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as client:
                    with self.assertRaises(RuntimeError):
                        await client.post(URL)
            self.assertEqual(transitions, ["dispatching", "received"])
            self.assertEqual(observation.summary()["uncertain"], 1)
            self.assertEqual(observation.summary()["cancelled"], 0)

    async def test_optional_authority_fence_runs_before_quota_and_each_send(self):
        order = []
        async def current_authority():
            order.append("authority")
        original = self.fixture.store.reserve
        def reserve(*arguments):
            order.append("reserve")
            return original(*arguments)
        def send(_request):
            order.append("send")
            return httpx.Response(200)
        observation = DispatchObservation(before_dispatch=current_authority)
        with self.fixture.bind(PATH), observe_dispatches(observation), patch.object(self.fixture.store, "reserve", reserve):
            async with MeteredAsyncClient(transport=httpx.MockTransport(send)) as client:
                await client.post(URL)
                await client.post(URL)
        self.assertEqual(order, ["authority", "reserve", "send"] * 2)
        self.assertEqual(observation.summary()["sent"], 2)
        self.assertNotIn("authority", str(observation.summary()))
        self.assertIsNone(current_observation())

    async def test_rejected_or_broken_fence_never_reserves_or_sends_and_context_cleans(self):
        for error in (RuntimeFailure("context_changed"), ValueError("controlled authority failure")):
            async def rejected():
                raise error
            observation = DispatchObservation(before_dispatch=rejected)
            with self.fixture.bind(PATH), observe_dispatches(observation), patch.object(
                    self.fixture.store, "reserve", side_effect=AssertionError("fence must run first")):
                async with MeteredAsyncClient(transport=httpx.MockTransport(
                        lambda _: self.fail("rejected authority sent"))) as client:
                    with self.assertRaises(type(error)):
                        await client.post(URL)
            self.assertEqual(observation.summary()["sent"], 0)
            self.assertIsNone(current_observation())

    async def test_cancel_during_authority_fence_is_not_a_sent_attempt(self):
        entered = asyncio.Event()
        async def delayed():
            entered.set()
            await asyncio.Future()
        observation = DispatchObservation(before_dispatch=delayed)
        with self.fixture.bind(PATH), observe_dispatches(observation), patch.object(
                self.fixture.store, "reserve", side_effect=AssertionError("cancelled fence cannot reserve")):
            async with MeteredAsyncClient(transport=httpx.MockTransport(
                    lambda _: self.fail("cancelled authority sent"))) as client:
                operation = asyncio.create_task(client.post(URL))
                await entered.wait()
                operation.cancel()
                operation.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await operation
        self.assertEqual(observation.summary()["sent"], 0)
        self.assertIsNone(current_observation())


if __name__ == "__main__":
    unittest.main()

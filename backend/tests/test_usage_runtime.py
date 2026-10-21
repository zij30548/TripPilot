"""Production metering with real SQLite and controlled transports/worker pauses."""
import asyncio
from contextlib import closing
import os
import sqlite3
import threading
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from core.business_usage import UsageError
from tooling.providers.amap import AmapClient
from tooling.providers.amap_walking import AmapWalkingClient
from core.schemas.candidates import CandidateRequest
from core.schemas.route import RouteEndpoint, WalkingRouteRequest
from tooling.travel.candidates import prepare_candidates
from tooling.travel.schedule_routes import ScheduleRouteCollector
from core.usage_runtime import MeteredAsyncClient, MeteredTransport, media_usage
from tests.usage_fixture import UsageFixture

POI_URL = "https://restapi.amap.com/v5/place/text"
QWEN_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"


class RuntimeUsageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = UsageFixture()
        self.addCleanup(self.fixture.close)
        self.store = self.fixture.store

    def rows(self):
        with closing(sqlite3.connect(self.store.settings.business_usage_path)) as db:
            return db.execute("SELECT service, state, dispatched_at FROM usage_attempts ORDER BY rowid").fetchall()

    def client(self, handler):
        return httpx.AsyncClient(transport=MeteredTransport(httpx.MockTransport(handler)), follow_redirects=False)

    async def test_identity_and_trusted_dispatch_required_before_reserve(self):
        sent = []
        async with self.client(lambda request: sent.append(request) or httpx.Response(200)) as client:
            with self.assertRaises(UsageError):
                await client.get(POI_URL)
            with self.fixture.bind("/places/search"):
                for url in ("http://restapi.amap.com/v5/place/text", "https://other.test/v5/place/text",
                            "https://restapi.amap.com/arbitrary", "https://restapi.amap.com:444/v5/place/text",
                            "https://user:pass@restapi.amap.com/v5/place/text"):
                    with self.assertRaises(UsageError):
                        await client.get(url)
        self.assertEqual(sent, [])
        self.assertEqual(self.rows(), [])

    async def test_all_network_categories_and_bound_qwen_modes(self):
        async with self.client(lambda _: httpx.Response(200, json={})) as client:
            with self.fixture.bind("/places/search"):
                for path in ("/v5/place/text", "/v3/direction/walking", "/v3/direction/transit/integrated",
                             "/v3/weather/weatherInfo", "/v3/config/district"):
                    await client.get("https://restapi.amap.com" + path)
            for path, expected in (("/trips/schedule-explanation", "qwen_text"),
                                   ("/trips/adjustment-intent", "qwen_text"),
                                   ("/trips/itinerary-query", "qwen_text"),
                                   ("/media/landmark", "qwen_vision")):
                with self.fixture.bind(path):
                    await client.post(QWEN_URL, json={})
                self.assertEqual(self.rows()[-1][0], expected)
            with self.fixture.bind("/saved-trips"), self.assertRaises(UsageError):
                await client.post(QWEN_URL, json={})
        self.assertEqual([row[0] for row in self.rows()[:5]], ["amap_poi", "amap_route", "amap_route", "amap_weather", "amap_proxy"])
        self.assertTrue(all(row[1] == "received" and row[2] is not None for row in self.rows()))

    async def test_production_client_preserves_proxy_selection_and_counts_once(self):
        # Inspect construction only, never connect to this controlled proxy.
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:19876", "HTTP_PROXY": "",
                                    "ALL_PROXY": "", "NO_PROXY": ""}, clear=False):
            async with httpx.AsyncClient() as ordinary, MeteredAsyncClient() as metered:
                self.assertEqual([pattern.pattern for pattern in ordinary._mounts],
                                 [pattern.pattern for pattern in metered._mounts])
                self.assertTrue(any(transport is not None for transport in metered._mounts.values()))
        calls = []
        with self.fixture.bind("/places/search"):
            async with MeteredAsyncClient(transport=httpx.MockTransport(
                    lambda request: calls.append(request) or httpx.Response(200, json={}))) as client:
                await client.get(POI_URL)
                async with client.stream("GET", POI_URL):
                    pass
                with self.assertRaises(UsageError):
                    await client.get(POI_URL, follow_redirects=True)
        self.assertEqual(len(calls), 2)
        self.assertEqual([row[1] for row in self.rows()], ["received", "received"])

    async def test_connection_retry_counts_twice_no_refund_or_extra_retry(self):
        calls = []
        def upstream(request):
            calls.append(request)
            if len(calls) == 1:
                raise httpx.ConnectError("controlled", request=request)
            return httpx.Response(200, json={"status": "1", "pois": []})
        with self.fixture.bind("/places/search"):
            async with self.client(upstream) as client:
                self.assertEqual(await AmapClient(client, "test-only").search("park", "上海"), [])
        self.assertEqual(len(calls), 2)
        self.assertEqual([row[1] for row in self.rows()], ["failed", "received"])

    async def test_retry_denied_by_quota_never_reaches_second_transport_attempt(self):
        calls = []
        real_reserve = self.store.reserve
        def reserve(owner, service):
            if self.rows():
                raise UsageError("business_quota_exhausted", service=service, scope="account")
            return real_reserve(owner, service)
        def upstream(request):
            calls.append(request)
            raise httpx.ConnectError("controlled", request=request)
        with self.fixture.bind("/places/search"), patch.object(self.store, "reserve", reserve):
            async with self.client(upstream) as client:
                with self.assertRaises(UsageError):
                    await AmapClient(client, "test-only").search("park", "上海")
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.rows()), 1)

    async def test_same_place_is_free_and_invalid_upstream_is_still_counted(self):
        calls = []
        endpoint = RouteEndpoint(place_id="stay", longitude=121.4, latitude=31.2)
        with self.fixture.bind("/routes/walking"):
            async with self.client(lambda request: calls.append(request) or httpx.Response(200, json={})) as client:
                response = await AmapWalkingClient(client, "test").walking(WalkingRouteRequest(origin=endpoint, destination=endpoint))
        self.assertEqual(response.status, "same_place")
        self.assertEqual(calls, [])
        with self.fixture.bind("/places/search"):
            async with self.client(lambda _: httpx.Response(200, content=b"invalid-json")) as client:
                from tooling.providers.amap import AmapError
                with self.assertRaises(AmapError):
                    await AmapClient(client, "test").search("park", "上海")
        self.assertEqual(self.rows()[0][1], "received")

    async def test_cancel_during_reservation_drains_worker_without_send(self):
        entered, release = threading.Event(), threading.Event()
        real_reserve = self.store.reserve
        def reserve(owner, service):
            entered.set()
            release.wait(3)
            return real_reserve(owner, service)
        calls = []
        with self.fixture.bind("/places/search"), patch.object(self.store, "reserve", reserve):
            async with self.client(lambda request: calls.append(request) or httpx.Response(200)) as client:
                task = asyncio.create_task(client.get(POI_URL))
                while not entered.is_set():
                    await asyncio.sleep(.001)
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertEqual(calls, [])
        self.assertEqual([row[1:] for row in self.rows()], [("reserved", None)])

    async def test_dispatch_mark_must_commit_before_send_and_cancel_is_conservative(self):
        entered, release = threading.Event(), threading.Event()
        real_mark = self.store.mark
        def mark(identifier, state):
            if state == "dispatching":
                entered.set()
                release.wait(3)
            real_mark(identifier, state)
        calls = []
        with self.fixture.bind("/places/search"), patch.object(self.store, "mark", mark):
            async with self.client(lambda request: calls.append(request) or httpx.Response(200)) as client:
                task = asyncio.create_task(client.get(POI_URL))
                while not entered.is_set():
                    await asyncio.sleep(.001)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertEqual(calls, [])
        self.assertEqual(self.rows()[0][1], "cancelled")
        self.assertIsNotNone(self.rows()[0][2])

    async def test_cancel_after_send_is_counted_and_read_timeout_is_not_refunded(self):
        sent = asyncio.Event()
        async def upstream(request):
            sent.set()
            await asyncio.Future()
        with self.fixture.bind("/places/search"):
            async with self.client(upstream) as client:
                task = asyncio.create_task(client.get(POI_URL))
                await sent.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        def timeout(request):
            raise httpx.ReadTimeout("controlled", request=request)
        with self.fixture.bind("/places/search"):
            async with self.client(timeout) as client:
                with self.assertRaises(httpx.ReadTimeout):
                    await client.get(POI_URL)
        self.assertEqual([row[1] for row in self.rows()], ["cancelled", "failed"])

    async def test_incremental_body_failure_and_early_close_are_not_success(self):
        class Broken(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"first"
                raise httpx.ReadError("controlled")
        with self.fixture.bind("/places/search"):
            async with self.client(lambda _: httpx.Response(200, stream=Broken())) as client:
                with self.assertRaises(httpx.ReadError):
                    await client.get(POI_URL)
                async with client.stream("GET", POI_URL):
                    pass
        self.assertEqual([row[1] for row in self.rows()], ["failed", "cancelled"])

    async def test_counter_failure_blocks_before_send_and_after_dispatch_is_not_hidden(self):
        calls = []
        with self.fixture.bind("/places/search"):
            async with self.client(lambda request: calls.append(request) or httpx.Response(200)) as client:
                with patch.object(self.store, "reserve", side_effect=UsageError()), self.assertRaises(UsageError):
                    await client.get(POI_URL)
                with patch.object(self.store, "mark", side_effect=UsageError()), self.assertRaises(UsageError):
                    await client.get(POI_URL)
        self.assertEqual(calls, [])
        self.assertEqual(self.rows()[0][1], "reserved")

    async def test_photo_and_video_processing_have_separate_durable_units(self):
        with self.fixture.bind("/media/landmark"):
            async with media_usage("media_photo"):
                pass
            with self.assertRaises(ValueError):
                async with media_usage("media_photo"):
                    raise ValueError("controlled decoder failure")
            async with media_usage("media_video"):
                pass
            with patch.object(self.store, "reserve", side_effect=UsageError()):
                entered = False
                with self.assertRaises(UsageError):
                    async with media_usage("media_video"):
                        entered = True
                self.assertFalse(entered)
        self.assertEqual([(row[0], row[1]) for row in self.rows()],
                         [("media_photo", "received"), ("media_photo", "failed"), ("media_video", "received")])

    async def test_candidate_and_schedule_errors_propagate_and_drain_siblings(self):
        place = {"id": "stay", "name": "受控住宿", "address": None, "category": None,
                 "longitude": 121.4, "latitude": 31.2, "source": "amap"}
        candidates = CandidateRequest.model_validate({"accommodation_place": place, "must_visit_places": [], "interests": ["建筑", "购物"]})
        stopped = asyncio.Event()
        calls = 0
        async def search(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                await asyncio.sleep(.01)
                raise UsageError("business_quota_exhausted", service="amap_poi", scope="account")
            try:
                await asyncio.Future()
            finally:
                stopped.set()
        with self.assertRaises(UsageError):
            await prepare_candidates(candidates, AsyncMock(search=search))
        self.assertTrue(stopped.is_set())
        client = AsyncMock(spec=AmapWalkingClient)
        client.walking.side_effect = UsageError("business_rate_limited", service="amap_route", scope="global")
        a = RouteEndpoint(place_id="a", longitude=121.4, latitude=31.2)
        b = RouteEndpoint(place_id="b", longitude=121.5, latitude=31.3)
        with self.assertRaises(UsageError):
            await ScheduleRouteCollector(client).collect([(a, b), (b, a)])


if __name__ == "__main__":
    unittest.main()

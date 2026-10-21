"""Offline bounded POI transport and directory checks; never contacts AMap."""
import asyncio
from contextlib import closing
import sqlite3
import unittest
from unittest.mock import patch

import httpx

from app.integrations.amap import AmapTimeoutError, SEARCH_URL
from app.integrations.chat_places import ChatPlacesClient, ChatPlacesError, MAX_RESPONSE_BYTES
from app.business_usage import UsageError
from app.usage_runtime import MeteredAsyncClient
from tests.usage_fixture import UsageFixture


def poi(**changes):
    value = {"id": "B001", "name": "受控测试地点", "address": "受控测试地址", "location": "126.54,45.80",
             "type": "景点", "citycode": "0451", "cityname": "哈尔滨市", "adcode": "230102"}
    return {**value, **changes}


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


class ChatPlacesTests(unittest.IsolatedAsyncioTestCase):
    async def search(self, handler, keyword="测试", city_id="230100"):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ChatPlacesClient(client, "controlled-key").search(keyword, city_id)

    async def test_supported_nonlegacy_city_fixed_parameters_and_exact_poi_snapshot(self):
        requests = []
        def handler(request):
            requests.append(request)
            self.assertEqual(SEARCH_URL, str(request.url).split("?")[0])
            self.assertEqual("230100", request.url.params["region"])
            self.assertEqual("true", request.url.params["city_limit"])
            self.assertEqual("20", request.url.params["page_size"])
            self.assertEqual("测试", request.url.params["keywords"])
            return httpx.Response(200, json={"status": "1", "pois": [poi()]})
        result = await self.search(handler)
        self.assertEqual(1, len(requests))
        self.assertEqual("B001", result[0].id)
        self.assertEqual("230102", result[0].region.adcode)
        self.assertEqual(126.54, result[0].longitude)
        self.assertEqual(45.8, result[0].latitude)

    async def test_uncommon_citycode_and_direct_administration(self):
        result = await self.search(lambda _: httpx.Response(200, json={"status": "1", "pois": [poi(citycode="1391", cityname="济源市", adcode="419001")]}), city_id="419001")
        self.assertEqual("1391", result[0].region.citycode)

    async def test_cross_city_missing_or_contradictory_members_fail_entire_search(self):
        for changes in ({"citycode": None}, {"cityname": None}, {"adcode": None},
                        {"citycode": "021"}, {"cityname": "上海市"}, {"adcode": "230199"}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ChatPlacesError, "城市归属"):
                await self.search(lambda _: httpx.Response(200, json={"status": "1", "pois": [poi(), poi(id="bad", **changes)]}))

    async def test_only_actual_empty_list_is_no_result_and_duplicates_are_not_ambiguous(self):
        self.assertEqual([], await self.search(lambda _: httpx.Response(200, json={"status": "1", "pois": []})))
        result = await self.search(lambda _: httpx.Response(200, json={"status": "1", "pois": [poi(), poi()]}))
        self.assertEqual(1, len(result))
        with self.assertRaises(ChatPlacesError):
            await self.search(lambda _: httpx.Response(200, json={"status": "1", "pois": [poi(), poi(name="另一个快照")]}))
        for bad in ({"status": "0", "pois": []}, {"status": "1"}, {"status": "1", "pois": None}, {"status": "1", "pois": [poi(location="nan,45")]}, {"status": "1", "pois": [poi(id="x" * 121)]}):
            with self.subTest(bad=bad), self.assertRaises(ChatPlacesError):
                await self.search(lambda _: httpx.Response(200, json=bad))

    async def test_limits_and_duplicate_json_are_not_silently_truncated(self):
        responses = [httpx.Response(200, json={"status": "1", "pois": [poi(id=f"p{i}") for i in range(21)]}),
                     httpx.Response(200, headers={"content-length": str(MAX_RESPONSE_BYTES + 1)}, content=b"{}"),
                     httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1)),
                     httpx.Response(200, content=b'{"status":"1","status":"1","pois":[]}'),
                     httpx.Response(200, content=b'{"status":"1","pois":[NaN]}')]
        for response in responses:
            with self.subTest(response=response), self.assertRaises(ChatPlacesError):
                await self.search(lambda _: response)
        stream = Stream([b"x" * 1024] * 257)
        with self.assertRaises(ChatPlacesError):
            await self.search(lambda _: httpx.Response(200, stream=stream))
        self.assertTrue(stream.closed)

    async def test_only_one_connection_retry_read_timeout_never_retries(self):
        attempts = []
        def handler(request):
            attempts.append(request)
            if len(attempts) == 1:
                raise httpx.ConnectError("sensitive transport details", request=request)
            return httpx.Response(200, json={"status": "1", "pois": [poi()]})
        self.assertEqual(1, len(await self.search(handler)))
        self.assertEqual(2, len(attempts))
        attempts.clear()
        def timeout(request):
            attempts.append(request)
            raise httpx.ReadTimeout("secret", request=request)
        with self.assertRaises(AmapTimeoutError):
            await self.search(timeout)
        self.assertEqual(1, len(attempts))

    async def test_cancel_and_invalid_query_send_no_additional_attempts(self):
        calls = []
        started = asyncio.Event()
        async def handler(request):
            calls.append(request)
            started.set()
            await asyncio.Event().wait()
        task = asyncio.create_task(self.search(handler))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(1, len(calls))
        for keyword, city in (("", "230100"), ("x" * 81, "230100"), ("测试", "999999"), ("测试", "shanghai")):
            with self.assertRaises(ChatPlacesError):
                await self.search(lambda r: calls.append(r), keyword, city)
        self.assertEqual(1, len(calls))

    async def test_each_connection_attempt_is_durably_metered_and_quota_failure_blocks_retry(self):
        fixture = UsageFixture()
        self.addCleanup(fixture.close)
        calls = []
        def handler(request):
            calls.append(request)
            if len(calls) % 2:
                raise httpx.ConnectError("controlled", request=request)
            return httpx.Response(200, json={"status": "1", "pois": [poi()]})
        with fixture.bind("/chat/conversations/controlled/places"):
            async with MeteredAsyncClient(transport=httpx.MockTransport(handler)) as client:
                self.assertEqual(1, len(await ChatPlacesClient(client, "controlled").search("测试", "230100")))
        with closing(sqlite3.connect(fixture.store.settings.business_usage_path)) as db:
            rows = db.execute("SELECT service,state FROM usage_attempts ORDER BY rowid").fetchall()
        self.assertEqual([("amap_poi", "failed"), ("amap_poi", "received")], rows)
        calls.clear()
        reserve = fixture.store.reserve
        reservations = []
        def one(owner, service):
            reservations.append(service)
            if len(reservations) > 1:
                raise UsageError("business_quota_exhausted", service=service, scope="account")
            return reserve(owner, service)
        with fixture.bind("/chat/conversations/controlled/places"), patch.object(fixture.store, "reserve", one):
            async with MeteredAsyncClient(transport=httpx.MockTransport(handler)) as client:
                with self.assertRaises(UsageError):
                    await ChatPlacesClient(client, "controlled").search("测试", "230100")
        self.assertEqual(1, len(calls))
if __name__ == "__main__":
    unittest.main()

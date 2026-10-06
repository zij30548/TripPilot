import asyncio
import json
import os
import unittest
from collections.abc import AsyncIterator
from datetime import datetime
from time import monotonic
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from app.api.places import get_http_client
from app.config import Settings, get_settings
from app.integrations.amap import AmapClient
from app.schemas.candidates import CandidateRequest
from app.schemas.place import ConfirmedPlace, Place
from app.services.candidates import (
    BATCH_TIMEOUT_SECONDS, INTEREST_KEYWORDS, prepare_candidates, retrieval_sources,
)
from main import app


TEST_KEY = "candidate-fixture-key-not-real"
ALL_INTERESTS = [interest for interest, _ in INTEREST_KEYWORDS]


def place(identifier: str, name: str | None = None, **updates: object) -> dict[str, object]:
    return {
        "id": identifier, "name": name if name is not None else f"测试地点-{identifier}", "address": "测试地址",
        "longitude": 121.4, "latitude": 31.2, "category": "原始分类;子分类", "source": "amap",
    } | updates


def poi(identifier: str, name: str | None = None, **updates: object) -> dict[str, object]:
    return {
        "id": identifier, "name": name or f"测试地点-{identifier}", "address": "上游地址",
        "location": "121.5,31.3", "type": "上游原始分类;子分类",
    } | updates


def body(**updates: object) -> dict[str, object]:
    return {"accommodation_place": place("stay"), "must_visit_places": [], "interests": []} | updates


class CandidatesApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        self.settings = Settings(_env_file=None, amap_web_key=TEST_KEY)
        self.requests: list[httpx.Request] = []
        self.clients: list[httpx.AsyncClient] = []
        self.results: dict[str, list[dict[str, object]]] = {}
        self.errors: dict[str, type[httpx.RequestError]] = {}
        self.business_failures: set[str] = set()
        self.delays: dict[str, float] = {}
        self.active = 0
        self.maximum_active = 0

        async def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            self.active += 1
            self.maximum_active = max(self.active, self.maximum_active)
            keyword = request.url.params["keywords"]
            try:
                await asyncio.sleep(self.delays.get(keyword, 0))
                if keyword in self.errors:
                    raise self.errors[keyword](f"private {TEST_KEY} {request.url}", request=request)
                if keyword in self.business_failures:
                    return httpx.Response(200, json={"status": "0", "info": TEST_KEY})
                return httpx.Response(200, json={"status": "1", "pois": self.results.get(keyword, [])})
            finally:
                self.active -= 1

        async def client_dependency() -> AsyncIterator[httpx.AsyncClient]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                self.clients.append(client)
                yield client

        self.enterContext(patch.dict(app.dependency_overrides, {
            get_settings: lambda: self.settings, get_http_client: client_dependency,
        }))
        self.client = self.enterContext(TestClient(app))

    def post(self, data: dict[str, object] | None = None) -> httpx.Response:
        return self.client.post(
            "/places/candidates", content=json.dumps(body() if data is None else data),
            headers={"Content-Type": "application/json"},
        )

    def test_default_query_and_fixed_safe_upstream(self) -> None:
        self.results["旅游景点"] = [poi("one")]
        response = self.post()
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["keywords"], ["旅游景点"])
        self.assertEqual(result["queries"], [{"keyword": "旅游景点", "interest": None,
                         "status": "success", "result_count": 1, "message": None}])
        self.assertIsNotNone(datetime.fromisoformat(result["queried_at"]).tzinfo)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["role"], "optional")
        self.assertEqual(candidate["retrieval_sources"], [{"interest": None, "keyword": "旅游景点"}])
        self.assertEqual(candidate["place"]["category"], "上游原始分类;子分类")
        self.assertEqual(candidate["place"]["longitude"], 121.5)
        self.assertEqual(candidate["place"]["latitude"], 31.3)
        self.assertEqual(set(candidate["place"]), set(place("one")))
        request = self.requests[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(str(request.url).split("?")[0], "https://restapi.amap.com/v5/place/text")
        self.assertEqual(dict(request.url.params), {"key": TEST_KEY, "keywords": "旅游景点",
                         "region": "上海", "city_limit": "true", "page_size": "20", "page_num": "1"})
        self.assertEqual(request.extensions["timeout"]["connect"], 3.0)
        self.assertNotIn(TEST_KEY, response.text)
        self.assertTrue(all(client.is_closed for client in self.clients))

    def test_all_mappings_fixed_order_duplicate_interests_merge(self) -> None:
        result = self.post(body(interests=list(reversed(ALL_INTERESTS)) * 3)).json()
        self.assertEqual(result["keywords"], [keyword for _, keyword in INTEREST_KEYWORDS])
        self.assertEqual([query["interest"] for query in result["queries"]], ALL_INTERESTS)
        self.assertEqual(len(self.requests), 6)

    def test_empty_success_is_not_failure_and_must_is_kept(self) -> None:
        must = place("must")
        result = self.post(body(must_visit_places=[must])).json()
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["queries"][0]["result_count"], 0)
        self.assertEqual(result["candidates"], [{"place": must, "role": "must_visit", "retrieval_sources": []}])

    def test_must_snapshot_wins_and_all_sources_merge(self) -> None:
        must = place("must", "用户确认的原名")
        self.results = {"公园": [poi("must", "不同的上游名"), poi("shared")],
                        "餐厅": [poi("shared", "其他结果名"), poi("must")]}
        result = self.post(body(interests=["美食", "摄影"], must_visit_places=[must])).json()
        self.assertEqual([item["place"]["id"] for item in result["candidates"]], ["must", "shared"])
        self.assertEqual(result["candidates"][0]["place"], must)
        self.assertEqual(result["candidates"][0]["role"], "must_visit")
        for candidate in result["candidates"]:
            self.assertEqual(candidate["retrieval_sources"], [
                {"interest": "摄影", "keyword": "公园"}, {"interest": "美食", "keyword": "餐厅"},
            ])
        self.assertEqual(result["candidates"][1]["place"]["name"], "测试地点-shared")

    def test_same_name_different_ids_remain_distinct(self) -> None:
        self.results["旅游景点"] = [poi("id1", "同名"), poi("id2", "同名")]
        result = self.post().json()
        self.assertEqual([item["place"]["id"] for item in result["candidates"]], ["id1", "id2"])

    def test_stay_is_excluded_unless_also_must(self) -> None:
        self.results["旅游景点"] = [poi("stay"), poi("other")]
        result = self.post().json()
        self.assertEqual([item["place"]["id"] for item in result["candidates"]], ["other"])
        must = place("stay")
        result = self.post(body(must_visit_places=[must])).json()
        self.assertEqual(result["candidates"][0]["place"], must)
        self.assertEqual(result["candidates"][0]["role"], "must_visit")

    def test_round_robin_limit_does_not_limit_musts(self) -> None:
        self.results = {keyword: [poi(f"{index}-{n}") for n in range(20)]
                        for index, (_, keyword) in enumerate(INTEREST_KEYWORDS)}
        musts = [place(f"must-{n}") for n in range(21)]
        result = self.post(body(interests=ALL_INTERESTS, must_visit_places=musts)).json()
        self.assertEqual(len(result["candidates"]), 39)
        self.assertEqual([item["place"] for item in result["candidates"][:21]], musts)
        self.assertEqual([item["place"]["id"] for item in result["candidates"][21:]],
                         [f"{i}-{n}" for n in range(3) for i in range(6)])

    def test_round_robin_skips_ineligible_and_merged_ids(self) -> None:
        self.results = {"公园": [poi("stay"), poi("must"), poi("same"), poi("same"), poi("park")],
                        "餐厅": [poi("same"), poi("food"), poi("food2")]}
        result = self.post(body(interests=["摄影", "美食"], must_visit_places=[place("must")])).json()
        self.assertEqual([item["place"]["id"] for item in result["candidates"]],
                         ["must", "same", "food", "park", "food2"])
        self.assertEqual(len(result["candidates"][1]["retrieval_sources"]), 2)

    def test_first_page_limit_even_if_upstream_returns_extra(self) -> None:
        # The last five would be optional; all first-page records are the stay.
        self.results["旅游景点"] = [poi("stay")] * 20 + [poi(f"extra-{n}") for n in range(5)]
        result = self.post().json()
        self.assertEqual(result["queries"][0]["result_count"], 20)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(len(self.requests), 1)

    def test_completion_order_does_not_change_candidates_or_sources(self) -> None:
        self.results = {"公园": [poi("same", "优先名"), poi("park")],
                        "餐厅": [poi("food"), poi("same", "后续名")]}
        self.delays = {"公园": 0.02}
        first = self.post(body(interests=["美食", "摄影"])).json()
        self.delays = {"餐厅": 0.02}
        second = self.post(body(interests=["摄影", "美食"])).json()
        self.assertEqual(first["candidates"], second["candidates"])
        self.assertEqual(first["queries"], second["queries"])

    def test_concurrency_at_most_three(self) -> None:
        self.delays = {keyword: 0.025 for _, keyword in INTEREST_KEYWORDS}
        result = self.post(body(interests=ALL_INTERESTS)).json()
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.maximum_active, 3)
        self.assertEqual(self.active, 0)
        self.assertEqual(len(self.requests), 6)

    def test_partial_failure_keeps_valid_results_and_must(self) -> None:
        self.results["公园"] = [poi("park")]
        self.business_failures.add("餐厅")
        self.errors["商场"] = httpx.ReadTimeout
        result = self.post(body(interests=["摄影", "美食", "购物"], must_visit_places=[place("must")])).json()
        self.assertEqual(result["status"], "partial")
        self.assertEqual([q["status"] for q in result["queries"]], ["success", "failed", "timeout"])
        self.assertEqual([c["place"]["id"] for c in result["candidates"]], ["must", "park"])
        self.assertNotIn(TEST_KEY, json.dumps(result))

    def test_all_failed_is_failed_even_with_musts(self) -> None:
        self.business_failures.update(["公园", "餐厅"])
        result = self.post(body(interests=["摄影", "美食"], must_visit_places=[place("must")])).json()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["role"], "must_visit")
        self.assertTrue(all(query["result_count"] == 0 and query["message"] for query in result["queries"]))

    def test_connection_only_retry_budget_is_twelve(self) -> None:
        self.errors = {keyword: httpx.ConnectError for _, keyword in INTEREST_KEYWORDS}
        with self.assertLogs("app.integrations.amap", level="WARNING") as logs:
            response = self.post(body(interests=ALL_INTERESTS * 2))
        self.assertEqual(response.json()["status"], "failed")
        self.assertEqual(len(self.requests), 12)
        for _, keyword in INTEREST_KEYWORDS:
            self.assertEqual(sum(request.url.params["keywords"] == keyword for request in self.requests), 2)
        self.assertNotIn(TEST_KEY, response.text + " ".join(logs.output))
        self.assertNotIn("restapi.amap.com", response.text + " ".join(logs.output))

    def test_read_timeout_and_business_errors_are_not_retried(self) -> None:
        self.errors["公园"] = httpx.ReadTimeout
        self.business_failures.add("餐厅")
        response = self.post(body(interests=["摄影", "美食"]))
        self.assertEqual([q["status"] for q in response.json()["queries"]], ["timeout", "failed"])
        self.assertEqual(len(self.requests), 2)

    def test_missing_key_is_safe_503(self) -> None:
        self.settings = Settings(_env_file=None, amap_web_key="")
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.requests, [])

    def test_invalid_request_structure_and_untrusted_controls_rejected(self) -> None:
        invalid = [{}, body(accommodation_place=None), body(interests=None), body(interests="摄影"),
                   body(interests=["不支持"]), body(interests=[1]), body(must_visit_places=None),
                   body(must_visit_places=[place("same"), place(" same ")])]
        invalid += [body(**{key: TEST_KEY}) for key in ["city", "keyword", "keywords", "key", "upstream_url"]]
        for data in invalid:
            with self.subTest(data=data):
                response = self.post(data)
                self.assertEqual(response.status_code, 422)
                self.assertNotIn(TEST_KEY, response.text)
        self.assertEqual(self.requests, [])

    def test_strict_place_validation_on_both_roles(self) -> None:
        variations = [{"id": " "}, {"name": ""}, {"source": "other"}, {"latitude": True},
                      {"longitude": "121.4"}, {"latitude": 91}, {"longitude": -181},
                      {"longitude": float("nan")}, {"latitude": float("inf")},
                      {"price": 0}, {"address": []}]
        for update in variations:
            for role in ["accommodation_place", "must_visit_places"]:
                with self.subTest(update=update, role=role):
                    invalid = place("one", **update)
                    value = invalid if role == "accommodation_place" else [invalid]
                    response = self.post(body(**{role: value}))
                    self.assertEqual(response.status_code, 422)
                    self.assertNotIn("NaN", response.text)
        for field in place("one"):
            invalid = place("one")
            del invalid[field]
            self.assertEqual(self.post(body(accommodation_place=invalid)).status_code, 422)
        self.assertEqual(self.requests, [])

    def test_existing_post_cors_allowed_and_other_origin_not_added(self) -> None:
        for origin in ["http://localhost:3000", "http://127.0.0.1:3000"]:
            response = self.client.options("/places/candidates", headers={
                "Origin": origin, "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type",
            })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["access-control-allow-origin"], origin)
        response = self.client.options("/places/candidates", headers={
            "Origin": "https://untrusted.example", "Access-Control-Request-Method": "POST",
        })
        self.assertNotIn("access-control-allow-origin", response.headers)


class CandidateBudgetTests(unittest.IsolatedAsyncioTestCase):
    def request(self, interests: list[str] | None = None) -> CandidateRequest:
        return CandidateRequest.model_validate(body(interests=ALL_INTERESTS if interests is None else interests))

    async def test_whole_batch_deadline_cancels_running_and_queued_work(self) -> None:
        self.assertEqual(BATCH_TIMEOUT_SECONDS, 12.0)
        started: list[str] = []
        cancelled: list[str] = []

        async def search(keyword: str, city: str) -> list[Place]:
            started.append(keyword)
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(keyword)
                raise
            return []

        amap = AsyncMock(spec=AmapClient)
        amap.search.side_effect = search
        before = monotonic()
        with patch("app.services.candidates.BATCH_TIMEOUT_SECONDS", 0.035):
            result = await prepare_candidates(self.request(), amap)
        self.assertLess(monotonic() - before, 0.5)
        self.assertEqual(len(started), 3)
        self.assertCountEqual(cancelled, started)
        self.assertEqual(result.status, "failed")
        self.assertEqual([query.status for query in result.queries], ["timeout"] * 6)
        self.assertEqual(len(asyncio.all_tasks()), 1)

    async def test_deadline_retains_completed_results_in_fixed_order(self) -> None:
        async def search(keyword: str, city: str) -> list[Place]:
            if keyword == "公园":
                return [Place.model_validate(place("park"))]
            await asyncio.Event().wait()
            return []

        amap = AsyncMock(spec=AmapClient)
        amap.search.side_effect = search
        with patch("app.services.candidates.BATCH_TIMEOUT_SECONDS", 0.03):
            result = await prepare_candidates(self.request(), amap)
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.queries[0].status, "success")
        self.assertEqual([query.status for query in result.queries[1:]], ["timeout"] * 5)
        self.assertEqual([item.place.id for item in result.candidates], ["park"])

    async def test_parent_cancel_cleans_all_child_tasks(self) -> None:
        entered = asyncio.Event()
        cancelled: list[str] = []

        async def search(keyword: str, city: str) -> list[Place]:
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(keyword)
                raise
            return []

        amap = AsyncMock(spec=AmapClient)
        amap.search.side_effect = search
        task = asyncio.create_task(prepare_candidates(self.request(), amap))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(cancelled), 3)
        self.assertEqual(len(asyncio.all_tasks()), 1)

    async def test_unexpected_exception_does_not_expose_details_or_break_other_query(self) -> None:
        amap = AsyncMock(spec=AmapClient)
        amap.search.side_effect = [RuntimeError(TEST_KEY), []]
        result = await prepare_candidates(self.request(["摄影", "美食"]), amap)
        self.assertEqual(result.status, "partial")
        self.assertNotIn(TEST_KEY, result.model_dump_json())

    async def test_request_is_unchanged_after_merging(self) -> None:
        request = self.request(["摄影"])
        request.must_visit_places = [ConfirmedPlace.model_validate(place("must"))]
        original = request.model_dump_json()
        amap = AsyncMock(spec=AmapClient)
        amap.search.return_value = [Place.model_validate(place("must", "新名"))]
        result = await prepare_candidates(request, amap)
        self.assertEqual(request.model_dump_json(), original)
        self.assertEqual(result.candidates[0].place.name, request.must_visit_places[0].name)

    async def test_duplicate_keywords_are_merged_even_if_mapping_evolves(self) -> None:
        with patch("app.services.candidates.INTEREST_KEYWORDS", (("摄影", "公园"), ("美食", "公园"))):
            sources = retrieval_sources(["美食", "摄影"])
            self.assertEqual(len(sources), 1)
            self.assertEqual(sources[0].keyword, "公园")
            self.assertEqual(sources[0].interest, "摄影")


if __name__ == "__main__":
    unittest.main()

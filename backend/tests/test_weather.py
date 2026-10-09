"""Offline forecast contract, upstream boundaries and deadline tests."""

import asyncio
import copy
import json
import os
import time
import unittest
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.places import get_http_client
from app.config import Settings, get_settings
from app.integrations.amap import AmapError, AmapTimeoutError
from app.integrations.amap_weather import AmapWeatherClient, convert_forecast
from app.schemas.weather import (
    NON_PRECIPITATION, RAIN, RAIN_SNOW, SNOW, WeatherForecastResponse,
    WeatherRequest, precipitation_for,
)
from main import app


TEST_KEY = "mock-weather-key-not-a-real-credential"
QUERY_TIME = datetime(2026, 10, 10, 4, 0, tzinfo=timezone.utc)


def sample_request(start: str = "2026-10-10", end: str = "2026-10-12") -> dict[str, str]:
    return {"start_date": start, "end_date": end}


def sample_cast(day: str = "2026-10-10") -> dict[str, object]:
    return {
        "date": day, "week": "6", "dayweather": "多云", "nightweather": "小雨",
        "daytemp": "0", "nighttemp": "-3", "daywind": "东北", "nightwind": "东",
        "daypower": "≤3", "nightpower": "1-3",
    }


def sample_upstream() -> dict[str, object]:
    return {
        "status": "1", "count": "1", "info": "OK", "infocode": "10000",
        "forecasts": [{
            "city": "上海市", "adcode": "310000", "province": "上海", "reporttime": "2026-10-10 11:00:00",
            "casts": [sample_cast(f"2026-10-{day}") for day in (10, 11, 12, 13)],
        }],
    }


class WeatherConversionTests(unittest.TestCase):
    def convert(self, data: object | None = None, request: dict[str, str] | None = None) -> WeatherForecastResponse:
        return convert_forecast(
            sample_upstream() if data is None else data,
            WeatherRequest.model_validate(sample_request() if request is None else request),
            QUERY_TIME,
        )

    def test_complete_response_day_night_semantics_and_zero_negative_temperature(self) -> None:
        result = self.convert()
        self.assertEqual(result.coverage, "complete")
        self.assertEqual(result.source, "amap")
        self.assertEqual(result.city, "上海市")
        self.assertEqual(result.adcode, "310000")
        self.assertEqual(result.timezone, "Asia/Shanghai")
        self.assertEqual(result.queried_at, QUERY_TIME)
        self.assertEqual(result.reported_at.isoformat(), "2026-10-10T11:00:00+08:00")
        self.assertEqual(result.report_time_status, "valid")
        self.assertEqual(result.freshness_at_query, "fresh")
        self.assertEqual([day.date.isoformat() for day in result.days], ["2026-10-10", "2026-10-11", "2026-10-12"])
        self.assertEqual(result.days[0].day.temperature_celsius, 0)
        self.assertEqual(result.days[0].night.temperature_celsius, -3)
        self.assertEqual(result.days[0].day.wind_power, "≤3")
        self.assertEqual(result.days[0].night.wind_power, "1-3")
        self.assertEqual(result.days[0].day.precipitation, "none")
        self.assertEqual(result.days[0].night.precipitation, "rain")
        self.assertEqual(result.days[0].night.precipitation_basis, "小雨")
        encoded = result.model_dump(mode="json")
        self.assertNotIn("rain_risk", json.dumps(encoded))
        self.assertNotIn("min_temperature", json.dumps(encoded))
        self.assertEqual(WeatherForecastResponse.model_validate(encoded), result)

    def test_one_two_three_day_cross_month_year_and_leap_dates(self) -> None:
        cases = [
            ("2026-10-10", "2026-10-10", ["2026-10-10"]),
            ("2026-10-31", "2026-11-01", ["2026-10-31", "2026-11-01"]),
            ("2026-12-31", "2027-01-02", ["2026-12-31", "2027-01-01", "2027-01-02"]),
            ("2028-02-28", "2028-03-01", ["2028-02-28", "2028-02-29", "2028-03-01"]),
        ]
        for start, end, dates in cases:
            with self.subTest(start=start):
                payload = sample_upstream()
                payload["forecasts"][0]["casts"] = [sample_cast(day) for day in reversed(dates)]
                result = self.convert(payload, sample_request(start, end))
                self.assertEqual([day.date.isoformat() for day in result.days], dates)
                self.assertEqual(result.coverage, "complete")

    def test_partial_coverage_matches_dates_not_positions_or_min_max(self) -> None:
        payload = sample_upstream()
        payload["forecasts"][0]["casts"] = [sample_cast("2026-10-12"), sample_cast("2026-10-10")]
        result = self.convert(payload)
        self.assertEqual(result.coverage, "partial")
        self.assertEqual([day.status for day in result.days], ["available", "unavailable", "available"])
        self.assertIsNone(result.days[1].day)
        self.assertIsNone(result.days[1].night)
        self.assertEqual(result.days[2].day.weather, "多云")

    def test_valid_empty_lists_and_far_future_are_no_coverage(self) -> None:
        payloads = [{"status": "1", "forecasts": []}, sample_upstream(), sample_upstream()]
        payloads[1]["forecasts"][0]["casts"] = []
        for payload in payloads:
            with self.subTest(payload=payload):
                result = self.convert(payload, sample_request("2027-04-01", "2027-04-03"))
                self.assertEqual(result.coverage, "none")
                self.assertTrue(all(day.status == "unavailable" and day.day is None and day.night is None for day in result.days))
        self.assertEqual(self.convert(payloads[0]).report_time_status, "missing")
        self.assertEqual(self.convert(payloads[0]).freshness_at_query, "unknown")

    def test_missing_scalar_representations_are_explicitly_unknown(self) -> None:
        fields = ["dayweather", "daytemp", "daywind", "daypower", "nightweather", "nighttemp", "nightwind", "nightpower"]
        for missing in [None, "", "   ", []]:
            payload = sample_upstream()
            payload["forecasts"][0]["casts"] = [{"date": "2026-10-10", **dict.fromkeys(fields, missing)}]
            result = self.convert(payload, sample_request("2026-10-10", "2026-10-10"))
            self.assertEqual(result.coverage, "complete")
            for period in [result.days[0].day, result.days[0].night]:
                self.assertIsNone(period.weather)
                self.assertIsNone(period.temperature_celsius)
                self.assertIsNone(period.wind_direction)
                self.assertIsNone(period.wind_power)
                self.assertEqual(period.precipitation, "unknown")
                self.assertIsNone(period.precipitation_basis)
        payload["forecasts"][0]["casts"] = [{"date": "2026-10-10"}]
        self.assertEqual(self.convert(payload).days[0].day.precipitation, "unknown")

    def test_exact_precipitation_rules_not_substring_guesses(self) -> None:
        for expected, terms in [("rain", RAIN), ("snow", SNOW), ("rain_snow", RAIN_SNOW), ("none", NON_PRECIPITATION)]:
            for term in terms:
                self.assertEqual(precipitation_for(term), expected)
        for term in [None, "未知", "新型天气", "小雨转晴", "雪后晴", "毛毛雨", "细雨"]:
            self.assertEqual(precipitation_for(term), "unknown")
        payload = sample_upstream()
        payload["forecasts"][0]["casts"][0].update(dayweather="新型天气", nightweather="雨夹雪")
        result = self.convert(payload)
        self.assertEqual(result.days[0].day.weather, "新型天气")
        self.assertEqual(result.days[0].day.precipitation_basis, "新型天气")
        self.assertEqual(result.days[0].day.precipitation, "unknown")
        self.assertEqual(result.days[0].night.precipitation, "rain_snow")

    def test_illegal_nonempty_scalars_fail_instead_of_becoming_missing(self) -> None:
        for field in ["dayweather", "nightweather", "daywind", "nightwind", "daypower", "nightpower"]:
            for value in [True, 0, 3, {}, ["晴"], "a" * 101, "晴\x00", "晴\x85风", "晴\x9f", "\ufeff晴", "晴\ufeff", "晴\ufeff风"]:
                payload = sample_upstream()
                payload["forecasts"][0]["casts"][0][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(AmapError):
                    self.convert(payload)
        for value in [True, False, {}, ["1"], "NaN", "Infinity", "1e1", "23°C", float("nan"), float("inf"), -101, 101]:
            payload = sample_upstream()
            payload["forecasts"][0]["casts"][0]["daytemp"] = value
            with self.subTest(value=value), self.assertRaises(AmapError):
                self.convert(payload)

    def test_whitespace_numeric_temperature_and_source_text_normalize(self) -> None:
        payload = sample_upstream()
        payload["forecasts"][0]["casts"][0].update(dayweather=" 多云 ", daytemp=" -3.5 ", nighttemp=0, daypower=" 1-3 ")
        period = self.convert(payload).days[0].day
        self.assertEqual(period.weather, "多云")
        self.assertEqual(period.temperature_celsius, -3.5)
        self.assertEqual(period.wind_power, "1-3")

    def test_malformed_response_city_dates_and_duplicates_fail(self) -> None:
        malformed = [[], {}, {"status": "1"}, {"status": "1", "forecasts": None}, {"status": "1", "forecasts": {}}]
        for field, value in [("city", "北京市"), ("city", None), ("adcode", "310101"), ("adcode", 310000), ("casts", None), ("casts", {})]:
            payload = sample_upstream()
            payload["forecasts"][0][field] = value
            malformed.append(payload)
        for casts in [[None], [sample_cast("2026-02-30")], [sample_cast("2026-1-01")], [sample_cast("2026-01-01T00:00:00Z")], [sample_cast(), sample_cast()]]:
            payload = sample_upstream()
            payload["forecasts"][0]["casts"] = casts
            malformed.append(payload)
        payload = sample_upstream()
        payload["forecasts"].append(copy.deepcopy(payload["forecasts"][0]))
        malformed.append(payload)
        for payload in malformed:
            with self.subTest(payload=payload), self.assertRaises(AmapError):
                self.convert(payload)

    def test_invalid_unrequested_date_or_field_cannot_hide_as_zero_coverage(self) -> None:
        for cast in [sample_cast("2026-02-30"), {**sample_cast("2026-09-01"), "daytemp": "bad"}]:
            payload = sample_upstream()
            payload["forecasts"][0]["casts"] = [cast]
            with self.assertRaises(AmapError):
                self.convert(payload, sample_request("2027-01-01", "2027-01-01"))

    def test_missing_or_invalid_report_time_does_not_claim_freshness(self) -> None:
        cases = [(None, "missing"), ([], "missing"), ("", "missing"), ("bad", "invalid"),
                 ({}, "invalid"), (123, "invalid"), ("2026-10-10T11:00:00+08:00", "invalid"),
                 ("2026-02-30 11:00:00", "invalid"), ("1999-12-31 11:00:00", "invalid"),
                 ("2026-10-10 12:05:01", "invalid")]
        for raw, status in cases:
            payload = sample_upstream()
            payload["forecasts"][0]["reporttime"] = raw
            result = self.convert(payload)
            self.assertEqual(result.report_time_status, status)
            self.assertIsNone(result.reported_at)
            self.assertEqual(result.freshness_at_query, "unknown")
            self.assertEqual(result.coverage, "complete")

    def test_publication_24_hour_boundary_and_shanghai_timezone(self) -> None:
        for raw, expected in [("2026-10-09 12:00:00", "fresh"), ("2026-10-09 11:59:59", "stale"), ("2026-10-10 12:05:00", "fresh")]:
            payload = sample_upstream()
            payload["forecasts"][0]["reporttime"] = raw
            self.assertEqual(self.convert(payload).freshness_at_query, expected)
        payload = sample_upstream()
        payload["forecasts"][0]["reporttime"] = "2026-10-10 00:00:00"
        result = convert_forecast(payload, WeatherRequest.model_validate(sample_request()), datetime(2026, 10, 9, 16, tzinfo=timezone.utc))
        self.assertEqual(result.reported_at.astimezone(timezone.utc).isoformat(), "2026-10-09T16:00:00+00:00")
        self.assertEqual(result.freshness_at_query, "fresh")

    def test_response_model_rejects_coverage_publication_and_classification_mutations(self) -> None:
        original = self.convert().model_dump(mode="json")
        mutations = []
        for field, value in [("coverage", "none"), ("source", "mock"), ("adcode", "310101"), ("timezone", "UTC"), ("freshness_at_query", "stale"), ("reported_at", None)]:
            data = copy.deepcopy(original)
            data[field] = value
            mutations.append(data)
        for field, value in [("precipitation", "rain"), ("precipitation_basis", "小雨"), ("temperature_celsius", float("nan"))]:
            data = copy.deepcopy(original)
            data["days"][0]["day"][field] = value
            mutations.append(data)
        data = copy.deepcopy(original)
        data["days"].reverse()
        mutations.append(data)
        for data in mutations:
            with self.assertRaises(ValidationError):
                WeatherForecastResponse.model_validate(data)

    def test_normalized_model_rejects_bom_in_edge_and_internal_text(self) -> None:
        original = self.convert().model_dump(mode="json")
        for field in ["weather", "wind_direction", "wind_power"]:
            for text in ["\ufeff晴", "晴\ufeff", "晴\ufeff风"]:
                data = copy.deepcopy(original)
                data["days"][0]["day"][field] = text
                if field == "weather":
                    data["days"][0]["day"].update(precipitation="unknown", precipitation_basis=text)
                with self.subTest(field=field, text=text), self.assertRaises(ValidationError):
                    WeatherForecastResponse.model_validate(data)


class WeatherApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        self.requests: list[httpx.Request] = []
        self.clients: list[httpx.AsyncClient] = []
        self.response = httpx.Response(200, json=sample_upstream())
        self.failures: list[type[httpx.RequestError] | None] = []
        self.settings = Settings(_env_file=None, amap_web_key=TEST_KEY)

        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            failure = self.failures.pop(0) if self.failures else None
            if failure:
                raise failure(f"Sensitive error: {request.url}", request=request)
            return self.response

        async def mock_client() -> AsyncIterator[httpx.AsyncClient]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                self.clients.append(client)
                yield client

        self.enterContext(patch.dict(app.dependency_overrides, {get_settings: lambda: self.settings, get_http_client: mock_client}))
        self.client = self.enterContext(TestClient(app))

    def forecast(self, payload: object | None = None) -> httpx.Response:
        return self.client.post("/weather/forecast", json=sample_request() if payload is None else payload)

    def test_fixed_shanghai_one_batch_for_three_dates_with_finite_timeout(self) -> None:
        before = datetime.now(timezone.utc)
        response = self.forecast()
        after = datetime.now(timezone.utc)
        self.assertEqual(response.status_code, 200)
        result = WeatherForecastResponse.model_validate(response.json())
        self.assertLessEqual(before, result.queried_at)
        self.assertLessEqual(result.queried_at, after)
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(str(request.url).split("?")[0], "https://restapi.amap.com/v3/weather/weatherInfo")
        self.assertEqual(dict(request.url.params), {"key": TEST_KEY, "city": "310000", "extensions": "all", "output": "JSON"})
        self.assertTrue(all(0 < seconds <= 10 for seconds in request.extensions["timeout"].values()))
        self.assertLessEqual(request.extensions["timeout"]["connect"], 3)
        self.assertTrue(all(client.is_closed for client in self.clients))
        self.assertNotIn(TEST_KEY, response.text)
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})

    def test_invalid_inputs_and_client_configuration_rejected_without_upstream(self) -> None:
        payloads = [{}, [], {"start_date": "2026-10-10"}, sample_request("2026-10-11", "2026-10-10"), sample_request("2026-10-10", "2026-10-13")]
        for value in [True, 123, None, [], "2026-1-10", "2026-02-30", "2026-10-10T00:00:00Z"]:
            payloads.append({"start_date": value, "end_date": "2026-10-10"})
        for field in ["city", "adcode", "key", "url", "extensions", "transport_mode"]:
            payloads.append({**sample_request(), field: "sensitive-client-value"})
        for payload in payloads:
            with self.subTest(payload=payload):
                response = self.forecast(payload)
                self.assertEqual(response.status_code, 422)
                self.assertNotIn("sensitive-client-value", response.text)
        self.assertEqual(self.requests, [])

    def test_missing_key_is_safe_503_without_upstream(self) -> None:
        self.settings = Settings(_env_file=None, amap_web_key=" ")
        response = self.forecast()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.requests, [])

    def test_connect_failures_retry_once_and_nothing_else_retries(self) -> None:
        for first in [httpx.ConnectError, httpx.ConnectTimeout]:
            self.requests.clear()
            self.failures = [first, None]
            self.assertEqual(self.forecast().status_code, 200)
            self.assertEqual(len(self.requests), 2)
        for failure, code in [(httpx.ConnectError, 502), (httpx.ConnectTimeout, 504), (httpx.ReadTimeout, 504), (httpx.WriteTimeout, 504), (httpx.PoolTimeout, 504), (httpx.ReadError, 502)]:
            self.requests.clear()
            self.failures = [failure, failure]
            response = self.forecast()
            self.assertEqual(response.status_code, code)
            self.assertEqual(len(self.requests), 2 if failure in (httpx.ConnectError, httpx.ConnectTimeout) else 1)
            self.assertNotIn(TEST_KEY, response.text)
            self.assertNotIn("Sensitive", response.text)
            self.assertNotIn("restapi", response.text)

    def test_redirect_business_http_and_parse_errors_do_not_retry(self) -> None:
        responses = [
            httpx.Response(302, headers={"Location": "https://untrusted.example/"}),
            httpx.Response(429, text="private error " + TEST_KEY),
            httpx.Response(500, text="private error " + TEST_KEY),
            httpx.Response(200, json={"status": "0", "info": TEST_KEY, "infocode": "10001"}),
            httpx.Response(200, text="malformed " + TEST_KEY),
            httpx.Response(200, json={"status": "1", "forecasts": {"secret": TEST_KEY}}),
        ]
        for upstream in responses:
            self.requests.clear()
            self.response = upstream
            response = self.forecast()
            self.assertEqual(response.status_code, 502)
            self.assertEqual(len(self.requests), 1)
            self.assertNotIn(TEST_KEY, response.text)
            self.assertNotIn("untrusted", response.text)

    def test_logger_records_only_safe_failure_class(self) -> None:
        self.failures = [httpx.ConnectError, httpx.ReadTimeout]
        with self.assertLogs("app.integrations.amap_weather", level="WARNING") as captured:
            self.assertEqual(self.forecast().status_code, 504)
        logs = "\n".join(captured.output)
        self.assertIn("ConnectError", logs)
        self.assertNotIn(TEST_KEY, logs)
        self.assertNotIn("key=", logs)
        self.assertNotIn("restapi", logs)


class WeatherDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_second_attempt_shares_remaining_deadline_and_cleanup(self) -> None:
        requests: list[httpx.Request] = []
        cleaned = asyncio.Event()

        async def handle(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if len(requests) == 1:
                await asyncio.sleep(0.02)
                raise httpx.ConnectError("mock-connect-error", request=request)
            try:
                await asyncio.sleep(1)
                return httpx.Response(200, json=sample_upstream())
            finally:
                # Cleanup can legitimately extend return beyond the search cutoff.
                await asyncio.sleep(0.01)
                cleaned.set()

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            started = asyncio.get_running_loop().time()
            with patch("app.integrations.amap_weather.BATCH_TIMEOUT_SECONDS", 0.06):
                with self.assertRaises(AmapTimeoutError):
                    await AmapWeatherClient(client, TEST_KEY).forecast(WeatherRequest.model_validate(sample_request()))
            elapsed = asyncio.get_running_loop().time() - started
        self.assertEqual(len(requests), 2)
        self.assertTrue(cleaned.is_set())
        first, second = [request.extensions["timeout"]["read"] for request in requests]
        self.assertLess(second, first - 0.01)
        self.assertGreaterEqual(elapsed, 0.06)
        self.assertLess(elapsed, 0.5)

    async def test_expired_before_start_never_sends_request(self) -> None:
        attempts = 0

        def handle(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(200, json=sample_upstream())

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with patch("app.integrations.amap_weather.BATCH_TIMEOUT_SECONDS", 0), self.assertRaises(AmapTimeoutError):
                await AmapWeatherClient(client, TEST_KEY).forecast(WeatherRequest.model_validate(sample_request()))
        self.assertEqual(attempts, 0)

    async def test_external_cancellation_propagates_and_cleans_without_retry(self) -> None:
        started, cleaned = asyncio.Event(), asyncio.Event()
        attempts = 0

        async def handle(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            started.set()
            try:
                await asyncio.sleep(1)
                return httpx.Response(200, json=sample_upstream())
            finally:
                cleaned.set()

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            task = asyncio.create_task(AmapWeatherClient(client, TEST_KEY).forecast(WeatherRequest.model_validate(sample_request())))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(cleaned.is_set())
        self.assertEqual(attempts, 1)

    async def test_synchronous_parsing_still_consumes_deadline(self) -> None:
        response = httpx.Response(200, json=sample_upstream())
        actual_conversion = convert_forecast

        def delayed(*args: object, **kwargs: object) -> WeatherForecastResponse:
            time.sleep(0.025)
            return actual_conversion(*args, **kwargs)

        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as client:
            with patch("app.integrations.amap_weather.BATCH_TIMEOUT_SECONDS", 0.01), patch("app.integrations.amap_weather.convert_forecast", delayed):
                with self.assertRaises(AmapTimeoutError):
                    await AmapWeatherClient(client, TEST_KEY).forecast(WeatherRequest.model_validate(sample_request()))

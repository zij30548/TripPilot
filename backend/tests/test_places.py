import os
import unittest
from collections.abc import AsyncIterator
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.api.places import get_http_client
from app.config import BACKEND_ENV_FILE, Settings, get_settings
from main import app


TEST_KEY = "mock-key-not-a-real-credential"


def sample_poi() -> dict[str, object]:
    # Fictional test fixture, not a real AMap search result.
    return {
        "id": "mock-poi-1", "name": "测试地点", "address": "测试地址",
        "location": "121.4,31.2", "type": "测试分类;子分类",
    }


class PlaceApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.requests: list[httpx.Request] = []
        self.clients: list[httpx.AsyncClient] = []
        self.response = httpx.Response(200, json={"status": "1", "pois": [sample_poi()]})
        self.failure: type[httpx.RequestError] | None = None
        self.settings = Settings(_env_file=None, amap_web_key=TEST_KEY)

        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if self.failure:
                raise self.failure(f"Sensitive upstream URL: {request.url}", request=request)
            return self.response

        async def mock_http_client() -> AsyncIterator[httpx.AsyncClient]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                self.clients.append(client)
                yield client

        overrides = {
            get_settings: lambda: self.settings,
            get_http_client: mock_http_client,
        }
        self.enterContext(patch.dict(app.dependency_overrides, overrides))
        self.client = self.enterContext(TestClient(app))

    def search(self) -> httpx.Response:
        return self.client.get("/places/search", params={"keyword": " 武康路 ", "city": " 上海 "})

    def test_conversion_and_v5_request(self) -> None:
        response = self.search()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [{
            "id": "mock-poi-1", "name": "测试地点", "address": "测试地址",
            "latitude": 31.2, "longitude": 121.4,
            "category": "测试分类;子分类", "source": "amap",
        }])
        request = self.requests[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(str(request.url).split("?")[0], "https://restapi.amap.com/v5/place/text")
        self.assertEqual(dict(request.url.params), {
            "key": TEST_KEY, "keywords": "武康路", "region": "上海",
            "city_limit": "true", "page_size": "20", "page_num": "1",
        })
        self.assertEqual(request.extensions["timeout"], {
            "connect": 3.0, "read": 10.0, "write": 10.0, "pool": 10.0,
        })
        self.assertTrue(all(client.is_closed for client in self.clients))
        self.assertNotIn(TEST_KEY, response.text)

    def test_empty_results(self) -> None:
        self.response = httpx.Response(200, json={"status": "1", "pois": []})
        response = self.search()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_bad_locations_are_skipped(self) -> None:
        for location in [None, "", [], "121", "a,b", "121,31,1", "181,31",
                         "121,91", "-181,31", "121,-91", "NaN,31", "121,Infinity"]:
            with self.subTest(location=location):
                invalid = sample_poi() | {"location": location}
                self.response = httpx.Response(200, json={
                    "status": "1", "pois": [invalid, sample_poi()],
                })
                with self.assertLogs("app.integrations.amap", level="WARNING") as logs:
                    response = self.search()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(response.json()), 1)
                self.assertNotIn(TEST_KEY, " ".join(logs.output))

    def test_missing_location_and_bad_identity(self) -> None:
        no_location = sample_poi()
        del no_location["location"]
        bad_pois = [no_location, None, [], sample_poi() | {"id": " "},
                    sample_poi() | {"name": None}]
        self.response = httpx.Response(200, json={"status": "1", "pois": bad_pois})
        with self.assertLogs("app.integrations.amap", level="WARNING"):
            response = self.search()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_missing_optional_fields_are_null(self) -> None:
        for value in [None, "", "  ", [], {}]:
            with self.subTest(value=value):
                self.response = httpx.Response(200, json={
                    "status": "1", "pois": [sample_poi() | {"address": value, "type": value}],
                })
                place = self.search().json()[0]
                self.assertIsNone(place["address"])
                self.assertIsNone(place["category"])

    def test_upstream_business_error_is_safe(self) -> None:
        self.response = httpx.Response(200, json={
            "status": "0", "info": TEST_KEY, "infocode": "10001",
        })
        response = self.search()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(TEST_KEY, response.text)

    def test_http_errors_and_redirects(self) -> None:
        for status in [302, 400, 403, 429, 500]:
            with self.subTest(status=status):
                self.response = httpx.Response(status, text=TEST_KEY, headers={
                    "Location": "https://example.com/should-not-follow",
                })
                self.requests.clear()
                response = self.search()
                self.assertEqual(response.status_code, 502)
                self.assertEqual(len(self.requests), 1)
                self.assertNotIn(TEST_KEY, response.text)

    def test_network_failure_and_timeout(self) -> None:
        for failure, status in [(httpx.ConnectError, 502), (httpx.ReadTimeout, 504),
                                (httpx.ConnectTimeout, 504)]:
            with self.subTest(failure=failure):
                self.failure = failure
                response = self.search()
                self.assertEqual(response.status_code, status)
                self.assertNotIn(TEST_KEY, response.text)
                self.assertNotIn("restapi.amap.com", response.text)
                self.assertTrue(all(client.is_closed for client in self.clients))

    def test_malformed_payloads(self) -> None:
        for data in [None, [], {}, {"status": "1"}, {"status": "1", "pois": None},
                     {"status": "1", "pois": {}}, {"status": "unexpected", "pois": []}]:
            with self.subTest(data=data):
                self.response = httpx.Response(200, json=data)
                self.assertEqual(self.search().status_code, 502)
        self.response = httpx.Response(200, text="not JSON")
        self.assertEqual(self.search().status_code, 502)

    def test_missing_key_does_not_break_health_or_make_requests(self) -> None:
        for key in ["", "  "]:
            with self.subTest(key=key):
                self.settings = Settings(_env_file=None, amap_web_key=key)
                response = self.search()
                self.assertEqual(response.status_code, 503)
                self.assertIn("AMAP_WEB_KEY", response.json()["detail"])
                self.assertEqual(self.client.get("/health").status_code, 200)
                self.assertEqual(self.requests, [])

    def test_query_validation(self) -> None:
        for params in [{}, {"city": "上海"}, {"keyword": "武康路"},
                       {"keyword": "  ", "city": "上海"}, {"keyword": "外滩", "city": " "},
                       {"keyword": "字" * 81, "city": "上海"}]:
            with self.subTest(params=params):
                response = self.client.get("/places/search", params=params)
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.requests, [])

    def test_httpx_logs_redact_key(self) -> None:
        with self.assertLogs("httpx", level="INFO") as logs:
            self.assertEqual(self.search().status_code, 200)
        output = " ".join(logs.output)
        self.assertIn("[REDACTED]", output)
        self.assertNotIn(TEST_KEY, output)

    def test_search_cors(self) -> None:
        for origin in ["http://localhost:3000", "http://127.0.0.1:3000"]:
            with self.subTest(origin=origin):
                preflight = self.client.options("/places/search", headers={
                    "Origin": origin, "Access-Control-Request-Method": "GET",
                })
                self.assertEqual(preflight.status_code, 200)
                self.assertEqual(preflight.headers["access-control-allow-origin"], origin)
                response = self.client.get("/places/search", params={
                    "keyword": "外滩", "city": "上海",
                }, headers={"Origin": origin})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["access-control-allow-origin"], origin)


class SettingsTests(unittest.TestCase):
    def test_env_file_and_environment_precedence(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            env_file = Path(directory) / ".env"
            env_file.write_text("AMAP_WEB_KEY=fixture-file-key\nUNRELATED_SETTING=ok\n", encoding="utf-8")
            settings = Settings(_env_file=env_file)
            self.assertEqual(settings.amap_web_key.get_secret_value(), "fixture-file-key")
            self.assertNotIn("fixture-file-key", repr(settings))
            with patch.dict(os.environ, {"AMAP_WEB_KEY": "fixture-env-key"}):
                self.assertEqual(Settings(_env_file=env_file).amap_web_key.get_secret_value(), "fixture-env-key")
            with patch.dict(os.environ, {"AMAP_WEB_KEY": ""}):
                self.assertEqual(Settings(_env_file=env_file).amap_web_key.get_secret_value(), "")

    def test_missing_key_is_allowed_in_settings(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(Settings(_env_file=None).amap_web_key.get_secret_value(), "")

    def test_dotenv_path_does_not_depend_on_working_directory(self) -> None:
        expected = Path(__file__).resolve().parents[1] / ".env"
        self.assertTrue(BACKEND_ENV_FILE.is_absolute())
        self.assertEqual(BACKEND_ENV_FILE, expected)
        self.assertEqual(Settings.model_config["env_file"], expected)


if __name__ == "__main__":
    unittest.main()

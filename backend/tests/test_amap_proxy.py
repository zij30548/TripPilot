import json
import logging
import os
import unittest
from collections.abc import AsyncIterator
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from uvicorn.logging import AccessFormatter

from app.api.amap_proxy import get_proxy_http_client
from app.config import Settings, get_settings
from app.integrations.amap_proxy import MAX_RESPONSE_BYTES
from app.logging_filters import RedactHttpCredentials, install_http_log_redaction
from main import app


JS_KEY = "fixture-js-key-not-a-credential"
SECURITY_CODE = "fixture-security-code-not-a-credential"
WEB_KEY = "fixture-web-key-not-a-credential"
DISTRICT = "/_AMapService/v3/config/district"
STYLES = "/_AMapService/v4/map/styles"


class AmapProxyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        self.requests: list[httpx.Request] = []
        self.clients: list[httpx.AsyncClient] = []
        self.settings = Settings(
            _env_file=None, amap_js_key=JS_KEY,
            amap_js_security_code=SECURITY_CODE, amap_web_key=WEB_KEY,
        )
        self.payload = {"status": "1", "districts": [{"adcode": "310000", "center": "121.4,31.2"}]}
        self.response = httpx.Response(200, json=self.payload)
        self.failure: type[httpx.RequestError] | None = None

        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if self.failure:
                raise self.failure(f"Sensitive URL {request.url}", request=request)
            return self.response

        async def mock_http_client() -> AsyncIterator[httpx.AsyncClient]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                self.clients.append(client)
                yield client

        self.enterContext(patch.dict(app.dependency_overrides, {
            get_settings: lambda: self.settings,
            get_proxy_http_client: mock_http_client,
        }))
        self.client = self.enterContext(TestClient(app))

    def request(self, params: dict[str, str] | None = None, **kwargs: object) -> httpx.Response:
        return self.client.get(DISTRICT, params={"key": JS_KEY, "keywords": "上海"} | (params or {}), **kwargs)

    def assert_safe(self, response: httpx.Response) -> None:
        for value in (JS_KEY, SECURITY_CODE, WEB_KEY):
            self.assertNotIn(value, response.text)
            self.assertNotIn(value, str(response.headers))

    def test_district_conversion_and_server_only_credentials(self) -> None:
        response = self.request({"subdistrict": "0", "extensions": "base", "level": "province", "s": "rsv3"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self.payload)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        request = self.requests[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(str(request.url).split("?")[0], "https://restapi.amap.com/v3/config/district")
        self.assertEqual(dict(request.url.params), {
            "key": JS_KEY, "jscode": SECURITY_CODE, "keywords": "上海", "subdistrict": "0",
            "extensions": "base", "level": "province", "page": "1", "s": "rsv3",
        })
        self.assertEqual(request.extensions["timeout"], {"connect": 3.0, "read": 10.0, "write": 10.0, "pool": 10.0})
        self.assertTrue(all(client.is_closed for client in self.clients))
        self.assert_safe(response)

    def test_shanghai_names_and_missing_limits(self) -> None:
        for name in ["上海", "上海市", "310000"]:
            with self.subTest(name=name):
                self.response = httpx.Response(200, json=self.payload)
                self.assertEqual(self.request({"keywords": name}).status_code, 200)
                self.assertEqual(self.requests[-1].url.params["subdistrict"], "0")
                self.assertEqual(self.requests[-1].url.params["extensions"], "base")

    def test_unused_custom_styles_are_not_exposed(self) -> None:
        response = self.client.get(STYLES, params={"key": JS_KEY, "output": "json"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.requests, [])
        self.assert_safe(response)

    def test_jsonp_uses_exact_callback_and_json_only(self) -> None:
        for callback in ["_AMap_cb_1", "AMap._callback1"]:
            with self.subTest(callback=callback):
                self.response = httpx.Response(200, text=f"/**/{callback}({json.dumps(self.payload)});", headers={
                    "Content-Type": "text/javascript; charset=utf-8",
                })
                response = self.request({"callback": callback, "s": "rsv3", "output": "json"})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.text.startswith(callback + "("))
                self.assertIn("application/javascript", response.headers["content-type"])
                self.assert_safe(response)
        self.response = httpx.Response(200, text="wrongCallback({});", headers={"Content-Type": "application/javascript"})
        self.assertEqual(self.request({"callback": "expected"}).status_code, 502)
        self.response = httpx.Response(200, text="expected({});alert(1);", headers={"Content-Type": "application/javascript"})
        self.assertEqual(self.request({"callback": "expected"}).status_code, 502)

    def test_jsonp_can_wrap_plain_json_without_html_interpolation(self) -> None:
        self.response = httpx.Response(200, json={"data": "</script><img src=x onerror=alert(1)>"})
        response = self.request({"callback": "safeCallback"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<", response.text)
        self.assertIn("\\u003c", response.text)

    def test_wrong_missing_duplicate_keys_and_duplicate_parameters(self) -> None:
        for params, status in [
            ({"keywords": "上海"}, 403),
            ({"key": "wrong", "keywords": "上海"}, 403),
            ({"key": "", "keywords": "上海"}, 403),
            ([("key", JS_KEY), ("key", "wrong"), ("keywords", "上海")], 403),
            ([("key", JS_KEY), ("keywords", "上海"), ("keywords", "上海")], 400),
        ]:
            with self.subTest(params=params):
                response = self.client.get(DISTRICT, params=params)
                self.assertEqual(response.status_code, status)
                self.assert_safe(response)
        self.assertEqual(self.requests, [])

    def test_observed_sdk_query_normalizes_credentials_and_strips_metadata(self) -> None:
        # Browser-observed parameter shape; every identity/credential is fictional.
        params = [
            ("platform", "JS"), ("s", "rsv3"), ("logversion", "2.0"), ("key", JS_KEY),
            ("sdkversion", "2.3.5.6"), ("appname", "http%3A%2F%2F127.0.0.1%3A3000%2F"),
            ("csid", "11111111-2222-4333-8444-555555555555"), ("level", "province"),
            ("subdistrict", "0"), ("extensions", "base"), ("key", JS_KEY), ("s", "rsv3"),
            ("output", "json"), ("keywords", "上海"), ("callback", "AMap.testCallback"),
        ]
        self.response = httpx.Response(200, text=f"AMap.testCallback({json.dumps(self.payload)});", headers={
            "Content-Type": "application/javascript",
        })
        response = self.client.get(DISTRICT, params=params)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.text.startswith("AMap.testCallback("))
        self.assert_safe(response)
        upstream = self.requests[0].url
        self.assertEqual(str(upstream).split("?")[0], "https://restapi.amap.com/v3/config/district")
        self.assertEqual(dict(upstream.params), {
            "key": JS_KEY, "s": "rsv3",
            "level": "province", "subdistrict": "0", "extensions": "base", "page": "1",
            "output": "json", "keywords": "上海", "callback": "AMap.testCallback", "jscode": SECURITY_CODE,
        })
        self.assertEqual(upstream.params.get_list("key"), [JS_KEY])
        self.assertEqual(upstream.params.get_list("s"), ["rsv3"])
        self.assertEqual(upstream.params.get_list("jscode"), [SECURITY_CODE])
        for name in ["platform", "logversion", "sdkversion", "appname", "csid"]:
            self.assertNotIn(name, upstream.params)

    def test_same_key_and_sdk_flag_duplicates_are_allowed(self) -> None:
        for copies in [1, 2, 3]:
            with self.subTest(copies=copies):
                params = [("key", JS_KEY), ("s", "rsv3")] * copies + [("keywords", "上海")]
                response = self.client.get(DISTRICT, params=params)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.requests[-1].url.params.get_list("key"), [JS_KEY])
                self.assertEqual(self.requests[-1].url.params.get_list("s"), ["rsv3"])

    def test_conflicting_key_or_sdk_flag_cannot_be_hidden_by_order(self) -> None:
        for name, invalid, status in [("key", "wrong", 403), ("key", "", 403), ("s", "wrong", 400), ("s", "", 400)]:
            for index in [0, 1, 4]:
                with self.subTest(name=name, invalid=invalid, index=index):
                    params = [("key", JS_KEY), ("s", "rsv3"), ("key", JS_KEY), ("s", "rsv3"), ("keywords", "上海")]
                    params.insert(index, (name, invalid))
                    response = self.client.get(DISTRICT, params=params)
                    self.assertEqual(response.status_code, status)
                    self.assert_safe(response)
        self.assertEqual(self.requests, [])

    def test_optional_sdk_metadata_supports_safe_variants_without_forwarding(self) -> None:
        for params in [
            {"platform": "JS"}, {"logversion": "2.0"}, {"sdkversion": "2.3"}, {"sdkversion": "2.3.5.7"},
            {"csid": "ABCDEF0123456789abcdef0123456789"},
            {"csid": "11111111-2222-4333-8444-555555555555"},
            {"appname": "http://localhost:3000/"}, {"appname": "http://127.0.0.1:3000/trip?tab=map#result"},
            {"appname": "http%3A%2F%2Flocalhost%3A3000%2F"},
        ]:
            with self.subTest(params=params):
                self.assertEqual(self.request(params).status_code, 200)
                for name in params:
                    self.assertNotIn(name, self.requests[-1].url.params)

    def test_invalid_sdk_metadata_is_rejected_without_an_upstream_request(self) -> None:
        for params in [
            {"platform": "ios"}, {"platform": ""}, {"logversion": "3.0"}, {"logversion": ""},
            {"sdkversion": "3.0"}, {"sdkversion": "2.3.5.6.js"}, {"sdkversion": "2.99999"},
            {"csid": "not-a-request-id"}, {"csid": "<script>"}, {"csid": ""},
            {"appname": "https://evil.example/"}, {"appname": "http://localhost:8000/"},
            {"appname": "http://user:password@localhost:3000/"}, {"appname": "//localhost:3000/"},
            {"appname": "http://localhost:3000.evil.example/"}, {"appname": "http://localhost:3000/\n"},
            {"appname": "http%253A%252F%252Flocalhost%253A3000%252F"}, {"appname": ""},
            {"unverified_sdk_field": "test"},
        ]:
            with self.subTest(params=params):
                response = self.request(params)
                self.assertEqual(response.status_code, 400)
                self.assert_safe(response)
        self.assertEqual(self.requests, [])

    def test_noncredential_duplicates_remain_rejected(self) -> None:
        for name, value in [
            ("keywords", "上海"), ("callback", "safeCallback"), ("platform", "JS"),
            ("logversion", "2.0"), ("sdkversion", "2.3.5.6"), ("appname", "http://localhost:3000/"),
            ("csid", "11111111-2222-4333-8444-555555555555"),
        ]:
            with self.subTest(name=name):
                params = [("key", JS_KEY), ("s", "rsv3"), ("key", JS_KEY), ("s", "rsv3"), ("keywords", "上海")]
                params.extend([(name, value), (name, value)])
                self.assertEqual(self.client.get(DISTRICT, params=params).status_code, 400)
        self.assertEqual(self.requests, [])

    def test_client_cannot_override_security_or_target(self) -> None:
        for params in [
            {"jscode": "client-secret"}, {"JSCODE": "client-secret"}, {"securityJsCode": "client-secret"},
            {"url": "https://evil.example"}, {"target": "https://evil.example"}, {"host": "evil.example"},
            {"keywords": "北京"}, {"extensions": "all"}, {"subdistrict": "1"}, {"level": "district"},
            {"page": "2"}, {"offset": "200"}, {"output": "xml"}, {"s": "anything"},
            {"callback": "alert(1)//"}, {"callback": "a[0]"}, {"callback": "a;run"},
            {"callback": "a\n"}, {"callback": ""}, {"callback": "a" * 129},
        ]:
            with self.subTest(params=params):
                response = self.request(params)
                self.assertEqual(response.status_code, 400)
                self.assert_safe(response)
        self.assertEqual(self.requests, [])

    def test_unknown_encoded_and_out_of_scope_paths(self) -> None:
        for path in [
            "/_AMapService/", "/_AMapService/https://evil.example", "/_AMapService/v3/ip",
            "/_AMapService/v3/log/init",
            "/_AMapService/v3/direction/driving", "/_AMapService/v3/weather/weatherInfo",
            "/_AMapService/v5/place/text", "/_AMapService/v4/map/styles/extra",
            "/_AMapService/v3%2Fconfig%2Fdistrict", "/_AMapService/v3/config/../ip",
            "/_AMapService/%2e%2e/health", "/_AMapService/v3/config/district%00",
        ]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path, params={"key": JS_KEY}).status_code, 404)
        self.assertEqual(self.requests, [])

    def test_only_get_is_forwarded(self) -> None:
        for method in ["POST", "PUT", "PATCH", "DELETE", "HEAD", "TRACE"]:
            with self.subTest(method=method):
                response = self.client.request(method, DISTRICT, params={"key": JS_KEY, "keywords": "上海"})
                self.assertEqual(response.status_code, 405)
        self.assertEqual(self.requests, [])

    def test_missing_settings_does_not_break_health(self) -> None:
        for key, security in [("", SECURITY_CODE), (JS_KEY, ""), (" ", " ")]:
            with self.subTest(key=bool(key.strip()), code=bool(security.strip())):
                self.settings = Settings(_env_file=None, amap_js_key=key, amap_js_security_code=security)
                response = self.request()
                self.assertEqual(response.status_code, 503)
                self.assertEqual(self.client.get("/health").status_code, 200)
                self.assert_safe(response)
        self.assertEqual(self.requests, [])

    def test_timeouts_and_network_failures_do_not_expose_urls(self) -> None:
        for failure, status in [(httpx.ConnectTimeout, 504), (httpx.ReadTimeout, 504), (httpx.ConnectError, 502)]:
            with self.subTest(failure=failure):
                self.failure = failure
                response = self.request()
                self.assertEqual(response.status_code, status)
                self.assertNotIn("amap.com", response.text)
                self.assert_safe(response)
                self.assertTrue(all(client.is_closed for client in self.clients))

    def test_upstream_errors_and_redirects_are_not_forwarded(self) -> None:
        for status in [301, 302, 307, 400, 403, 429, 500]:
            with self.subTest(status=status):
                self.requests.clear()
                self.response = httpx.Response(status, text=SECURITY_CODE, headers={
                    "location": f"https://evil.example/?key={JS_KEY}&jscode={SECURITY_CODE}",
                    "set-cookie": f"secret={SECURITY_CODE}",
                })
                response = self.request()
                self.assertEqual(response.status_code, 502)
                self.assertEqual(len(self.requests), 1)
                self.assert_safe(response)
                self.assertNotIn("location", response.headers)
                self.assertNotIn("set-cookie", response.headers)

    def test_sensitive_or_malformed_response_is_blocked(self) -> None:
        for body in [
            {"status": "0", "info": "vendor detailed error"}, {"errcode": 10001},
            {"data": SECURITY_CODE}, {"data": JS_KEY}, {"data": WEB_KEY}, None,
        ]:
            with self.subTest(body=body):
                self.response = httpx.Response(200, json=body)
                response = self.request()
                self.assertEqual(response.status_code, 502)
                self.assert_safe(response)
                self.assertNotIn("vendor detailed error", response.text)
        # Unicode escapes may conceal a secret in raw JSON; check parsed values.
        escaped = "".join(f"\\u{ord(character):04x}" for character in SECURITY_CODE)
        self.response = httpx.Response(200, content=f'{{"data":"{escaped}"}}', headers={"Content-Type": "application/json"})
        self.assertEqual(self.request().status_code, 502)
        for body in [b"not json", b"\xff\xfe", b"{};alert(1)"]:
            self.response = httpx.Response(200, content=body, headers={"Content-Type": "application/json"})
            self.assertEqual(self.request().status_code, 502)

    def test_response_content_type_and_size_limits(self) -> None:
        for content_type in ["text/html", "application/octet-stream", ""]:
            with self.subTest(content_type=content_type):
                self.response = httpx.Response(200, content=b"{}", headers={"Content-Type": content_type})
                self.assertEqual(self.request().status_code, 502)
        self.response = httpx.Response(200, json={"data": "a" * MAX_RESPONSE_BYTES})
        self.assertEqual(self.request().status_code, 502)
        self.assertTrue(all(client.is_closed for client in self.clients))

    def test_browser_headers_and_upstream_headers_are_not_forwarded(self) -> None:
        self.response = httpx.Response(200, json=self.payload, headers={
            "Set-Cookie": f"secret={SECURITY_CODE}", "X-Vendor-Debug": SECURITY_CODE,
            "Access-Control-Allow-Origin": "*",
        })
        response = self.request(headers={"Authorization": "Bearer client-private", "Cookie": "session=private"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("authorization", self.requests[0].headers)
        self.assertNotIn("cookie", self.requests[0].headers)
        self.assertEqual(self.requests[0].headers["host"], "restapi.amap.com")
        self.assertNotIn("set-cookie", response.headers)
        self.assertNotIn("x-vendor-debug", response.headers)
        self.assertNotIn("access-control-allow-origin", response.headers)
        self.assert_safe(response)

    def test_query_length_limit_and_styles_remain_disabled(self) -> None:
        self.assertEqual(self.request({"extra": "a" * 5000}).status_code, 400)
        self.assertEqual(self.client.get(STYLES, params={"key": JS_KEY, "url": "https://evil.example"}).status_code, 404)
        self.assertEqual(self.requests, [])

    def test_cors_stays_restricted_and_preflight_does_not_use_upstream(self) -> None:
        for origin, status in [("http://localhost:3000", 200), ("http://127.0.0.1:3000", 200), ("https://evil.example", 400)]:
            response = self.client.options(DISTRICT, headers={"Origin": origin, "Access-Control-Request-Method": "GET"})
            self.assertEqual(response.status_code, status)
            self.assertNotEqual(response.headers.get("Access-Control-Allow-Origin"), "*")
        self.assertEqual(self.requests, [])

    def test_logs_do_not_expose_credentials_even_at_debug(self) -> None:
        with self.assertLogs("httpx", level="INFO") as logs:
            self.assertEqual(self.request().status_code, 200)
        for secret in (JS_KEY, SECURITY_CODE, WEB_KEY):
            self.assertNotIn(secret, " ".join(logs.output))
        self.assertIn("[REDACTED]", " ".join(logs.output))
        install_http_log_redaction()
        for name in ["httpcore.http11", "httpcore.connection", "uvicorn.access"]:
            with self.subTest(logger=name), self.assertLogs(name, level="DEBUG") as logs:
                if name == "uvicorn.access":
                    logging.getLogger(name).info('%s - "%s %s HTTP/%s" %d', "local", "GET", f"{DISTRICT}?key={JS_KEY}&jscode={SECURITY_CODE}", "1.1", 200)
                else:
                    try:
                        raise ValueError(f"Secret URL ?key={JS_KEY}&jscode={SECURITY_CODE}")
                    except ValueError:
                        logging.getLogger(name).exception(f"request target ?key={JS_KEY}&jscode={SECURITY_CODE}")
            self.assertNotIn(JS_KEY, " ".join(logs.output))
            self.assertNotIn(SECURITY_CODE, " ".join(logs.output))

    def test_uvicorn_access_formatter_retains_structured_arguments(self) -> None:
        record = logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
            ("127.0.0.1", "GET", f"{DISTRICT}?key={JS_KEY}&jscode={SECURITY_CODE}", "1.1", 200), None,
        )
        RedactHttpCredentials().filter(record)
        formatted = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False).format(record)
        self.assertIn("GET", formatted)
        self.assertIn("200", formatted)
        self.assertIn("[REDACTED]", formatted)
        self.assertNotIn(JS_KEY, formatted)
        self.assertNotIn(SECURITY_CODE, formatted)


if __name__ == "__main__":
    unittest.main()

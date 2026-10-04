"""Small allowlisted serviceHost adapter, not a general-purpose HTTP proxy."""

import hmac
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

import httpx

from app.logging_filters import install_http_log_redaction


install_http_log_redaction()
PROXY_TIMEOUT = httpx.Timeout(10.0, connect=3.0)
MAX_RESPONSE_BYTES = 1_000_000
DISTRICT_PATH = "v3/config/district"
# Standard map styling does not require the optional custom-style service.
# Add no other upstream until a scoped feature needs it and its protocol is verified.
UPSTREAM_URLS = {
    DISTRICT_PATH: "https://restapi.amap.com/v3/config/district",
}
CALLBACK_PATTERN = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*")
SDK_METADATA = {"platform", "logversion", "sdkversion", "appname", "csid"}
SDK_VERSION_PATTERN = re.compile(r"2(?:\.[0-9]{1,4}){1,3}")
CSID_PATTERN = re.compile(r"(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})")


class ProxyError(Exception):
    """Only use fixed, safe messages: never include raw input or upstream text."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ProxyPayload:
    body: bytes
    media_type: str


def _strip_sdk_metadata(params: dict[str, str]) -> None:
    """Validate observed SDK diagnostic fields, without forwarding them upstream."""
    if ("platform" in params and params["platform"] != "JS") or (
        "logversion" in params and params["logversion"] != "2.0"
    ):
        raise ProxyError(400, "地图 SDK 元参数无效。")
    if "sdkversion" in params and SDK_VERSION_PATTERN.fullmatch(params["sdkversion"]) is None:
        raise ProxyError(400, "地图 SDK 版本参数无效。")
    if "csid" in params and CSID_PATTERN.fullmatch(params["csid"]) is None:
        raise ProxyError(400, "地图 SDK 请求标识无效。")
    if "appname" in params:
        # QueryParams already decoded the query once. The SDK can additionally
        # encode its page URL; decode that layer once, never recursively.
        page_url = unquote(params["appname"])
        try:
            parsed = urlsplit(page_url)
            valid_page = (
                not any(ord(character) < 32 or ord(character) == 127 for character in page_url)
                and parsed.scheme == "http"
                and parsed.netloc in {"localhost:3000", "127.0.0.1:3000"}
            )
        except ValueError:
            valid_page = False
        if not valid_page:
            raise ProxyError(400, "地图 SDK 页面地址无效。")
    for name in SDK_METADATA:
        params.pop(name, None)


def validate_proxy_query(path: str, items: Sequence[tuple[str, str]], key: str) -> dict[str, str]:
    if path not in UPSTREAM_URLS:
        raise ProxyError(404, "不支持的地图服务路径。")
    allowed = {
        "key", "callback", "output", "s", "keywords", "subdistrict", "extensions", "level", "page", "offset",
    } | SDK_METADATA
    params: dict[str, str] = {}
    for name, value in items:
        # Reject overrides even if they equal the server's own value.
        if name.lower() in {"jscode", "securityjscode"}:
            raise ProxyError(400, "地图请求不能提供安全密钥。")
        if name not in allowed or len(value) > 256:
            raise ProxyError(400, "地图请求包含不支持的参数。")
        # The real SDK appends key/s twice. Validate every copy before folding
        # them; neither first-value nor last-value semantics can hide a conflict.
        if name == "key" and (not value or not hmac.compare_digest(value.encode(), key.encode())):
            raise ProxyError(403, "地图 JS Key 与服务配置不匹配。")
        if name == "s" and value != "rsv3":
            raise ProxyError(400, "地图 SDK 参数无效。")
        if name in params and name not in {"key", "s"}:
            raise ProxyError(400, "地图请求不允许重复查询参数。")
        params[name] = value
    if "key" not in params:
        raise ProxyError(403, "地图 JS Key 与服务配置不匹配。")
    _strip_sdk_metadata(params)
    callback = params.get("callback")
    if callback is not None and (len(callback) > 128 or CALLBACK_PATTERN.fullmatch(callback) is None):
        raise ProxyError(400, "地图回调参数无效。")
    if "output" in params and params["output"].lower() != "json":
        raise ProxyError(400, "地图服务仅支持 JSON 数据。")
    if params.get("keywords") not in {"上海", "上海市", "310000"}:
        raise ProxyError(400, "当前地图只支持上海。")
    fixed = {"subdistrict": "0", "extensions": "base", "level": "province", "page": "1"}
    for name, value in fixed.items():
        if name in params and params[name] != value:
            raise ProxyError(400, "行政区查询仅允许上海本级基础信息。")
    if "offset" in params and params["offset"] not in {"1", "10", "20"}:
        raise ProxyError(400, "行政区查询参数无效。")
    # Explicit server limits also apply if the browser omits them.
    params.update(fixed)
    return params


class AmapProxyClient:
    def __init__(self, client: httpx.AsyncClient, key: str, security_code: str, web_key: str = "") -> None:
        self._client = client
        self._key = key
        self._security_code = security_code
        self._sensitive_values = tuple(value for value in (key, security_code, web_key) if value)

    async def request(self, path: str, items: Sequence[tuple[str, str]]) -> ProxyPayload:
        params = validate_proxy_query(path, items, self._key)
        params["jscode"] = self._security_code
        try:
            async with self._client.stream(
                "GET", UPSTREAM_URLS[path], params=params, timeout=PROXY_TIMEOUT,
                follow_redirects=False,
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type not in {"application/json", "application/javascript", "text/javascript"}:
                    raise ProxyError(502, "地图服务返回了不支持的数据类型。")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise ProxyError(502, "地图服务响应超过大小限制。")
                    body.extend(chunk)
        except httpx.TimeoutException:
            raise ProxyError(504, "地图服务请求超时，请稍后重试。") from None
        except httpx.HTTPError:
            raise ProxyError(502, "暂时无法连接地图服务，请稍后重试。") from None
        return self._safe_payload(bytes(body), params.get("callback"))

    def _safe_payload(self, body: bytes, callback: str | None) -> ProxyPayload:
        try:
            text = body.decode("utf-8-sig").strip()
        except UnicodeError:
            raise ProxyError(502, "地图服务返回的数据格式异常。") from None
        if any(value in unquote(text) for value in self._sensitive_values):
            raise ProxyError(502, "地图服务返回的数据未通过安全检查。")
        # SDK requests may use JSONP. Accept only the exact validated callback
        # containing JSON, never arbitrary JavaScript from an upstream response.
        if callback:
            prefix = callback + "("
            if text.startswith("/**/"):
                text = text[4:].lstrip()
            if text.startswith(prefix):
                text = text[len(prefix):].rstrip(";").rstrip()
                if not text.endswith(")"):
                    raise ProxyError(502, "地图服务返回的数据格式异常。")
                text = text[:-1]
        try:
            data = json.loads(text)
        except ValueError:
            raise ProxyError(502, "地图服务返回的数据格式异常。") from None
        if not isinstance(data, (dict, list)):
            raise ProxyError(502, "地图服务返回的数据格式异常。")
        decoded = json.dumps(data, ensure_ascii=False)
        if any(value in unquote(decoded) for value in self._sensitive_values):
            raise ProxyError(502, "地图服务返回的数据未通过安全检查。")
        if isinstance(data, dict) and (
            ("status" in data and str(data["status"]) not in {"1", "200"})
            or ("errcode" in data and str(data["errcode"]) != "0")
        ):
            raise ProxyError(502, "高德地图请求失败，请检查服务配置或稍后重试。")
        safe_json = json.dumps(data, ensure_ascii=True, separators=(",", ":"))
        safe_json = safe_json.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        if callback:
            return ProxyPayload(f"{callback}({safe_json});".encode(), "application/javascript")
        return ProxyPayload(safe_json.encode(), "application/json")

import ipaddress
from pathlib import Path
import re
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
DEFAULT_AMAP_PAGE_ORIGINS = ("http://localhost:3000", "http://127.0.0.1:3000")
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def normalize_map_page_origin(value: str) -> str:
    """Trusted deployment origin, never an upstream URL supplied by a browser."""
    error = "地图页面来源须为明确的 HTTP(S) origin，不得包含凭证、路径、查询或片段。"
    if (not isinstance(value, str) or not value or len(value) > 512
            or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value)
            or any(character in value for character in "\\?#%")):
        raise ValueError(error)
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
        if (parsed.scheme not in {"http", "https"} or not value.startswith(parsed.scheme + "://")
                or not host or parsed.username is not None or parsed.password is not None
                or parsed.path not in {"", "/"} or port is not None and not 1 <= port <= 65535):
            raise ValueError(error)
        numeric_host = all(re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]+)", label) for label in host.split("."))
        if ":" in host or numeric_host:
            address = ipaddress.ip_address(host)
            if address.compressed != host:
                raise ValueError(error)
        elif (len(host) > 253 or not host.isascii()
              or any(_HOST_LABEL.fullmatch(label) is None for label in host.split("."))):
            raise ValueError(error)
        authority = f"[{host}]" if ":" in host else host
        if parsed.netloc != authority + (f":{port}" if port is not None else ""):
            # Reject alternate spellings (case, empty/zero-padded port, encoded
            # host) rather than relying on different clients' URL normalization.
            raise ValueError(error)
        if port is not None and (parsed.scheme, port) not in {("http", 80), ("https", 443)}:
            authority += f":{port}"
        return f"{parsed.scheme}://{authority}"
    except (ValueError, UnicodeError):
        raise ValueError(error) from None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Optional at startup: the existing health and Mock trip APIs need no key.
    amap_web_key: SecretStr = SecretStr("")
    amap_js_key: SecretStr = SecretStr("")
    amap_js_security_code: SecretStr = SecretStr("")
    # SDK appname metadata is checked against these server-configured page
    # origins. This does not widen CORS or change the fixed AMap upstream.
    amap_allowed_page_origins: tuple[str, ...] = DEFAULT_AMAP_PAGE_ORIGINS
    qwen_api_key: SecretStr = SecretStr("")
    qwen_base_url: str = ""
    qwen_model: str = "qwen3.7-flash-2026-07-15"
    qwen_vision_model: str = "qwen3-vl-plus"

    @field_validator("amap_allowed_page_origins", mode="before")
    @classmethod
    def validate_amap_page_origins(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 32:
            raise ValueError("地图页面来源须为包含 1～32 个 origin 的 JSON 列表。")
        origins = tuple(normalize_map_page_origin(origin) for origin in value)
        if len(set(origins)) != len(origins):
            raise ValueError("地图页面来源不得重复。")
        return origins


def get_settings() -> Settings:
    return Settings()

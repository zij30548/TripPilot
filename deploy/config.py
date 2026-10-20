"""Read/validate only our deliberately small systemd EnvironmentFile subset.

Not dotenv or shell: no export, interpolation, inline comments, continuations,
double quoting or escapes. Never print values or include them in exceptions.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import stat

BACKEND = frozenset({"AMAP_WEB_KEY", "AMAP_JS_KEY", "AMAP_JS_SECURITY_CODE", "AMAP_ALLOWED_PAGE_ORIGINS",
    "QWEN_API_KEY", "QWEN_BASE_URL", "QWEN_MODEL", "QWEN_VISION_MODEL", "AUTH_COOKIE_SECURE", "AUTH_ALLOWED_ORIGINS",
    "AUTH_SESSION_SECONDS", "TRIPPILOT_DATABASE_PATH", "BUSINESS_USAGE_PATH", "BUSINESS_RATE_WINDOW_SECONDS",
    "BUSINESS_RATE_ACCOUNT_LIMIT", "BUSINESS_RATE_GLOBAL_LIMIT", "BUSINESS_DAILY_LIMITS"})
BACKEND_OPTIONAL = frozenset({"TRIPPILOT_MYSQL_URL", "BUSINESS_USAGE_IDENTITY_PATH"})
FRONTEND = frozenset({"TRIPPILOT_BACKEND_ORIGIN", "NEXT_PUBLIC_AMAP_JS_KEY"})
ORIGIN = "https://trip.zijinglab.com"


class ConfigError(ValueError):
    pass


def parse(text: str, allowed: frozenset[str]) -> dict[str, str]:
    if len(text.encode()) > 16384: raise ConfigError("config_too_large")
    output: dict[str, str] = {}
    # Keep every historical required key exact. Only these two explicitly named
    # backend keys may be omitted, so an existing production file remains valid.
    optional = BACKEND_OPTIONAL if allowed == BACKEND else frozenset()
    for line in text.splitlines():
        if not line or line.startswith("#"): continue
        key, separator, raw = line.partition("=")
        if not separator or key not in allowed | optional or key in output: raise ConfigError("config_key_or_duplicate")
        if raw.startswith("'") and raw.endswith("'") and len(raw) >= 2:
            value = raw[1:-1]
            if "'" in value: raise ConfigError("config_quote")
        else:
            value = raw
            if any(c.isspace() or c in "'\"#\\" for c in value): raise ConfigError("config_syntax")
        if any(ord(c) < 32 or c in "\\$`%" for c in value): raise ConfigError("config_unsupported_expansion")
        output[key] = value
    if not allowed <= set(output) or set(output) - allowed - optional: raise ConfigError("config_exact_keys_required")
    return output


def validate_mysql(backend: dict[str, str]) -> bool:
    """Deployment policy is intentionally narrower than trusted backend URLs.

    One same-host MySQL instance/database; URL-safe credentials need no percent
    escaping in the deliberately restricted EnvironmentFile dialect. Never echo
    this URL: it contains the database password.
    """
    raw = backend.get("TRIPPILOT_MYSQL_URL", "")
    anchor = backend.get("BUSINESS_USAGE_IDENTITY_PATH", "")
    if anchor not in {"", "/var/lib/trippilot/usage.sqlite3.identity"}:
        raise ConfigError("production_usage_anchor_required")
    if not raw:
        return False
    matched = re.fullmatch(
        r"mysql\+pymysql://(?P<username>[A-Za-z0-9_]{1,32}):(?P<password>[A-Za-z0-9_-]{16,256})"
        r"@127\.0\.0\.1:3306/trippilot(?:\?charset=utf8mb4)?", raw, re.ASCII,
    )
    if (not matched or matched.group("username").lower() == "root"
            or "REPLACE" in matched.group("password")):
        raise ConfigError("production_loopback_mysql_required")
    if anchor != "/var/lib/trippilot/usage.sqlite3.identity":
        raise ConfigError("production_usage_anchor_required")
    return True


def validate(backend: dict[str, str], frontend: dict[str, str]) -> None:
    validate_mysql(backend)
    if (backend["AUTH_COOKIE_SECURE"] != "true"
        or backend["TRIPPILOT_DATABASE_PATH"] != "/var/lib/trippilot/identity.sqlite3"
        or backend["BUSINESS_USAGE_PATH"] != "/var/lib/trippilot/usage.sqlite3"
        or frontend["TRIPPILOT_BACKEND_ORIGIN"] != "http://127.0.0.1:8000"):
        raise ConfigError("production_boundary_mismatch")
    for key in ("AUTH_ALLOWED_ORIGINS", "AMAP_ALLOWED_PAGE_ORIGINS"):
        if json.loads(backend[key]) != [ORIGIN]: raise ConfigError("exact_https_origin_required")
    if backend["AMAP_JS_KEY"] != frontend["NEXT_PUBLIC_AMAP_JS_KEY"]: raise ConfigError("public_map_keys_mismatch")
    for key in ("AMAP_WEB_KEY", "AMAP_JS_KEY", "AMAP_JS_SECURITY_CODE", "QWEN_API_KEY"):
        if backend[key] and ("REPLACE" in backend[key] or len(backend[key]) > 512): raise ConfigError("credential_placeholder_or_length")
    if frontend["NEXT_PUBLIC_AMAP_JS_KEY"] and not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", frontend["NEXT_PUBLIC_AMAP_JS_KEY"]): raise ConfigError("public_key_format")
    # Match the backend's Beijing public/workspace hosts and fixed base path.
    # Keep this pre-venv check stdlib-only and reject URL credentials, ports,
    # query/fragment delimiters (even empty) and whitespace rather than normalize.
    if backend["QWEN_BASE_URL"] and not re.fullmatch(
        r"(?i:https://(?:dashscope\.aliyuncs\.com|[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
        r"\.cn-beijing\.maas\.aliyuncs\.com))/compatible-mode/v1/?",
        backend["QWEN_BASE_URL"], re.ASCII,
    ): raise ConfigError("beijing_model_origin_required")
    if backend["QWEN_MODEL"] != "qwen3.7-flash-2026-07-15" or backend["QWEN_VISION_MODEL"] != "qwen3-vl-plus": raise ConfigError("model_configuration_changed")
    if not re.fullmatch(r"[0-9]{1,6}", backend["AUTH_SESSION_SECONDS"]) or not 60 <= int(backend["AUTH_SESSION_SECONDS"]) <= 604800: raise ConfigError("session_duration")
    for key in ("BUSINESS_RATE_WINDOW_SECONDS", "BUSINESS_RATE_ACCOUNT_LIMIT", "BUSINESS_RATE_GLOBAL_LIMIT"):
        if not re.fullmatch(r"0|[1-9][0-9]{0,9}", backend[key]): raise ConfigError("rate_integer")
    if not 1 <= int(backend["BUSINESS_RATE_WINDOW_SECONDS"]) <= 3600: raise ConfigError("rate_window")
    if any(int(backend[k]) > 2147483647 for k in ("BUSINESS_RATE_ACCOUNT_LIMIT", "BUSINESS_RATE_GLOBAL_LIMIT")): raise ConfigError("rate_limit")
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result: raise ConfigError("duplicate_quota_key")
            result[key] = value
        return result
    limits = json.loads(backend["BUSINESS_DAILY_LIMITS"], object_pairs_hook=unique)
    if not isinstance(limits, dict) or set(limits) != {"amap_poi", "amap_route", "amap_weather", "amap_proxy", "qwen_text", "qwen_vision", "media_photo", "media_video"}: raise ConfigError("quota_services")
    for value in limits.values():
        if (not isinstance(value, dict) or set(value) != {"account", "global"}
            or any(type(n) is not int or not 0 <= n <= 2147483647 for n in value.values())): raise ConfigError("quota_limits")


def check_files(directory: Path) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for name, allowed, mode in (("backend", BACKEND, 0o600), ("frontend", FRONTEND, 0o640)):
        path = directory / f"{name}.env"
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != mode:
            raise ConfigError("config_permissions")
        if name == "backend" and info.st_gid != 0: raise ConfigError("backend_config_group")
        if name == "frontend":
            import grp
            if info.st_gid != grp.getgrnam("trippilot-web").gr_gid: raise ConfigError("frontend_config_group")
        result[name] = parse(path.read_text(), allowed)
    validate(result["backend"], result["frontend"])
    return result

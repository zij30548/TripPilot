"""release-1E local Chrome harness: real auth/quotas/SQLite, synthetic providers.

No live fallback exists. Only fixed POI and walking responses are permitted by
the controlled transport. Credentials stay in a separate private file, never
stdout, receipts or public evidence. Never use backend/data or an existing DB.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
from threading import Lock
from typing import Iterator

from app.business_usage import SERVICES, UsageError, UsageSettings, UsageStore
from app.storage import StorageError


def _paths(database: Path, usage_database: Path, credentials: Path | None = None) -> None:
    protected = Path(__file__).resolve().parents[1] / "data"
    values = [database, usage_database] + ([credentials] if credentials is not None else [])
    if (any(not value.is_absolute() or value.is_symlink() or value.resolve().is_relative_to(protected.resolve()) for value in values)
            or len({value.resolve() for value in values}) != len(values)):
        raise ValueError("independent_absolute_acceptance_paths_required")


def environment(database: Path, usage_database: Path) -> dict[str, str]:
    _paths(database, usage_database)
    limits = {service: {"account": 100, "global": 1000} for service in SERVICES}
    limits.update({"amap_poi": {"account": 3, "global": 5}, "amap_route": {"account": 8, "global": 8},
                   "qwen_text": {"account": 0, "global": 0}, "qwen_vision": {"account": 0, "global": 0}})
    return {"TRIPPILOT_DATABASE_PATH": str(database), "BUSINESS_USAGE_PATH": str(usage_database),
            "AUTH_COOKIE_SECURE": "false", "AUTH_ALLOWED_ORIGINS": '["http://127.0.0.1:3000","http://localhost:3000"]',
            "BUSINESS_RATE_WINDOW_SECONDS": "60", "BUSINESS_RATE_ACCOUNT_LIMIT": "100",
            "BUSINESS_RATE_GLOBAL_LIMIT": "200", "BUSINESS_DAILY_LIMITS": json.dumps(limits)}


@contextmanager
def _configured(values: dict[str, str]) -> Iterator[None]:
    original = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def prepare(database: Path, usage_database: Path, credentials: Path) -> None:
    _paths(database, usage_database, credentials)
    targets = (database, usage_database, credentials, Path(str(usage_database) + ".identity"))
    if any(value.exists() for value in targets) or len({value.resolve() for value in targets}) != len(targets):
        raise ValueError("acceptance_targets_must_be_new")
    from tools.identity_acceptance import prepare as prepare_identity
    with _configured(environment(database, usage_database)):
        # Reuse the existing private random-credentials utility. Its stdout is
        # already restricted to safe booleans/counts, never secrets or paths.
        prepare_identity(database, credentials)
        UsageStore(UsageSettings()).initialize()
    print(json.dumps({"usage_prepared": True, "external_calls": 0}))


class _Receipt:
    """Small static-event receipt, not a raw request/response/network log."""
    def __init__(self, path: Path | None):
        self.path, self.lock, self.sequence = path, Lock(), 0
        if path is None:
            return
        protected = Path(__file__).resolve().parents[1] / "data"
        if not path.is_absolute() or path.is_symlink() or path.resolve().is_relative_to(protected.resolve()):
            raise ValueError("independent_receipt_required")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.exists():
            if path.stat().st_size > 64 * 1024:
                raise ValueError("receipt_too_large")
            for line in path.read_text().splitlines():
                row = json.loads(line)
                if (set(row) != {"sequence", "service", "path", "controlled"}
                        or row["sequence"] != self.sequence + 1 or row["controlled"] is not True
                        or (row["service"], row["path"]) not in {
                            ("amap_poi", "/v5/place/text"), ("amap_route", "/v3/direction/walking")}):
                    raise ValueError("invalid_receipt")
                self.sequence += 1
        else:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)

    def append(self, service: str, path: str) -> None:
        with self.lock:
            if self.sequence >= 100:
                raise ValueError("controlled_dispatch_limit")
            self.sequence += 1
            if self.path is not None:
                row = {"sequence": self.sequence, "service": service, "path": path, "controlled": True}
                descriptor = os.open(self.path, os.O_APPEND | os.O_WRONLY)
                with os.fdopen(descriptor, "w") as output:
                    output.write(json.dumps(row, separators=(",", ":")) + "\n")
                    output.flush()
                    os.fsync(output.fileno())


def controlled_app(receipt: Path | None = None):
    import httpx
    from app.api.amap_proxy import get_proxy_http_client
    from app.api.places import get_http_client
    from app.api.explanation import get_qwen_http_client
    from app.config import Settings, get_settings
    from app.usage_runtime import MeteredAsyncClient
    from main import app

    events = _Receipt(receipt)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method != "GET" or request.url.host != "restapi.amap.com":
            raise AssertionError("acceptance_external_request_forbidden")
        if request.url.path == "/v5/place/text":
            events.append("amap_poi", request.url.path)
            home = "住宿" in request.url.params.get("keywords", "")
            return httpx.Response(200, json={"status": "1", "pois": [{
                "id": "controlled-home" if home else "controlled-visit",
                "name": "受控住宿参考点" if home else "受控游览地点",
                "address": "合成测试地址（非真实高德资料）", "location": "121.40,31.20" if home else "121.41,31.20",
                "type": "受控测试", "cityname": "上海市", "citycode": "021", "adcode": "310106",
            }]})
        if request.url.path == "/v3/direction/walking":
            events.append("amap_route", request.url.path)
            origin = request.url.params.get("origin", "")
            destination = request.url.params.get("destination", "")
            allowed = {"121.4,31.2", "121.41,31.2", "121.400000,31.200000", "121.410000,31.200000"}
            if origin not in allowed or destination not in allowed:
                raise AssertionError("acceptance_coordinates_not_allowed")
            return httpx.Response(200, json={"status": "1", "route": {"paths": [{
                "distance": "600", "duration": "600", "steps": [{"polyline": origin + ";" + destination}],
            }]}})
        # Includes Qwen, proxy, transit and weather: never a real HTTP transport.
        raise AssertionError("acceptance_business_request_not_allowed")

    async def client():
        # Production per-attempt quota implementation wraps the local provider.
        # AuthenticationMiddleware supplies the real account context. Replacing
        # this with a plain MockTransport would invalidate quota acceptance.
        async with MeteredAsyncClient(transport=httpx.MockTransport(handle), follow_redirects=False) as value:
            yield value

    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, amap_web_key="controlled-not-a-real-key")
    app.dependency_overrides[get_http_client] = client
    app.dependency_overrides[get_qwen_http_client] = client
    app.dependency_overrides[get_proxy_http_client] = client
    return app


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "serve"))
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--usage-database", type=Path, required=True)
    parser.add_argument("--credentials", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    try:
        _paths(args.database, args.usage_database, args.credentials)
        if args.action == "prepare":
            if args.credentials is None:
                raise ValueError("private_credentials_required")
            prepare(args.database, args.usage_database, args.credentials)
        else:
            if not args.database.is_file() or not 1 <= args.port <= 65535:
                raise ValueError("prepared_database_required")
            os.environ.update(environment(args.database, args.usage_database))
            UsageStore().verify()
            import uvicorn
            uvicorn.run(controlled_app(args.receipt), host="127.0.0.1", port=args.port, access_log=False)
    except (UsageError, StorageError, ValueError, OSError):
        print("Controlled usage acceptance setup refused; no external fallback or database reset.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

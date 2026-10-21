"""Release-1D local harness: real identity/SQLite, explicitly synthetic business.

Never run against user data. Credentials are generated into a private temporary
file for automation, never stdout/evidence. External clients use MockTransport;
the ordinary production app contains none of these overrides.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import time


def prepare(database: Path, credentials: Path) -> None:
    if database.exists() or credentials.exists():
        raise ValueError("acceptance_targets_must_be_new")
    from core.auth import create_account
    values = {name: secrets.token_urlsafe(24) for name in ("invited-a", "invited-b")}
    for name, password in values.items():
        create_account(name, password)
    credentials.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(credentials, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(values, output)
    print(json.dumps({"prepared": True, "accounts": len(values), "external_calls": 0}))


def controlled_app():
    import httpx
    from api.places import get_http_client
    from api.explanation import get_qwen_http_client
    from core.config import Settings, get_settings
    from main import app

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/text"):
            keyword = request.url.params.get("keywords", "")
            home = "住宿" in keyword
            identifier = "controlled-home" if home else "controlled-visit"
            return httpx.Response(200, json={"status": "1", "pois": [{
                "id": identifier, "name": "受控住宿参考点" if home else "受控游览地点",
                "address": "合成测试地址（非真实高德资料）", "location": "121.40,31.20" if home else "121.41,31.20",
                "type": "受控测试", "cityname": "上海市", "citycode": "021", "adcode": "310106",
            }]})
        if request.url.path.endswith("/walking"):
            return httpx.Response(200, json={"status": "1", "route": {"paths": [{
                "distance": "600", "duration": "600", "steps": [{"polyline": "121.4,31.2;121.41,31.2"}],
            }]}})
        raise AssertionError("acceptance_business_request_not_allowed")

    async def client():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as value:
            yield value

    async def forbidden():
        raise AssertionError("no_model_calls_in_release_1d")
        yield  # pragma: no cover

    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, amap_web_key="controlled-not-a-real-key")
    app.dependency_overrides[get_http_client] = client
    app.dependency_overrides[get_qwen_http_client] = forbidden
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "serve", "expire"))
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--credentials", type=Path)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not args.database.is_absolute():
        raise ValueError("absolute_test_database_required")
    from memory.storage import LEGACY_PATH
    if args.database.resolve() == LEGACY_PATH.resolve() or args.database.parent == LEGACY_PATH.parent:
        raise ValueError("user_database_not_allowed")
    os.environ["TRIPPILOT_DATABASE_PATH"] = str(args.database)
    os.environ["AUTH_COOKIE_SECURE"] = "false"  # local HTTP acceptance only
    if args.action == "prepare":
        if args.credentials is None or not args.credentials.is_absolute():
            raise ValueError("private_credentials_file_required")
        prepare(args.database, args.credentials)
    elif args.action == "expire":
        from memory.identity_models import SessionRow
        from memory.storage import engine_for
        from sqlmodel import Session, select
        with Session(engine_for(str(args.database))) as session:
            rows = session.exec(select(SessionRow)).all()
            for row in rows:
                row.expires_at = int(time.time()) - 1
                session.add(row)
            session.commit()
        print(json.dumps({"expired_test_sessions": len(rows)}))
    else:
        import uvicorn
        uvicorn.run(controlled_app(), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()

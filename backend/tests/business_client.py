"""Explicit business-only fixture; security tests use the real main ASGI stack.

Old adapter/contract tests intentionally exercise authenticated business handlers
without spending each assertion on password verification. This fixture exists only
under tests, removes the auth middleware from a shallow app copy, and supplies a
fixed trusted ASGI identity (not a request header or production configuration).
"""
from copy import copy

from fastapi.testclient import TestClient


class BusinessTestClient(TestClient):
    def __init__(self, app, *args, **kwargs):
        from app.identity_models import AccountRow

        isolated = copy(app)
        isolated.user_middleware = [m for m in app.user_middleware if m.cls.__name__ != "AuthenticationMiddleware"]
        isolated.middleware_stack = None

        async def authenticated(scope, receive, send):
            if scope["type"] == "http":
                scope.setdefault("state", {})["user"] = AccountRow(
                    id="a" * 32, username="business-fixture", password_hash="not-a-password-hash",
                    disabled=False, created_at=0,
                )
            await isolated(scope, receive, send)

        super().__init__(authenticated, *args, **kwargs)

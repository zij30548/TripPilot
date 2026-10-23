"""Explicit historical login application, used only by retained regression tests.

The production app never mounts these routes. This fixture retains the previous
identity boundary so old login/registration, migration and acceptance assertions
remain meaningful without adding a runtime authentication switch or bypass.
"""
from fastapi import FastAPI

from api.auth import router as auth_router
from core.auth import AuthenticationMiddleware
from main import app as production_app

app = FastAPI()
app.include_router(production_app.router)
app.dependency_overrides = production_app.dependency_overrides
app.exception_handlers = dict(production_app.exception_handlers)
app.user_middleware = [entry for entry in production_app.user_middleware if entry.cls.__name__ != "WorkspaceMiddleware"]
app.add_middleware(AuthenticationMiddleware)
app.include_router(auth_router)

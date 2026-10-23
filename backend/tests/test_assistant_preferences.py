"""Real authentication/CSRF and temporary storage; no model or external API."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from core import auth
from api.chat import get_chat_store
from api.assistant_preferences import get_preference_store
from memory.chat_storage import ChatStore
from memory.storage import engine_for
from memory.assistant_store import AssistantStore
from tools.chat_storage import provision_chat_extension
from tools.assistant_storage import provision_assistant_extension
from tests.legacy_identity_app import app


class PreferencesAPI(unittest.TestCase):
    def setUp(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        path = folder / "identity.sqlite3"
        self.enterContext(patch.dict(os.environ, {"TRIPPILOT_MYSQL_URL": "", "TRIPPILOT_DATABASE_PATH": str(path)}))
        settings = auth.AuthSettings(_env_file=None, auth_cookie_secure=False)
        for module in ("core.auth", "api.auth"):
            self.enterContext(patch(module + ".get_auth_settings", return_value=settings))
        self.owner = auth.create_account("pref-a", "temporary-controlled-password")
        auth.create_account("pref-b", "temporary-controlled-password")
        self.engine = engine_for(str(path))
        self.addCleanup(self.engine.dispose)
        self.addCleanup(engine_for.cache_clear)
        provision_chat_extension(self.engine); provision_assistant_extension(self.engine)
        self.store = AssistantStore(self.engine); self.chat = ChatStore(self.engine)
        self.enterContext(patch.dict(app.dependency_overrides, {get_chat_store: lambda: self.chat, get_preference_store: lambda: self.store}))
        self.client = self.enterContext(TestClient(app))
        self.headers = self.login("pref-a")
        cid = self.chat.create(self.owner, str(uuid4()))["id"]
        self.path = f"/chat/conversations/{cid}/assistant-preferences"

    def login(self, username):
        token = self.client.get("/auth/session").json()["csrf_token"]
        response = self.client.post("/auth/login", headers={"origin": "http://127.0.0.1:3000", "x-csrf-token": token},
            json={"username": username, "password": "temporary-controlled-password"})
        self.assertEqual(response.status_code, 200)
        return {"origin": "http://127.0.0.1:3000", "x-csrf-token": response.json()["csrf_token"]}

    def command(self, **data):
        return self.client.post(self.path, headers=self.headers, json={"action": "set", "key": "travel_preferences", "value": "希望留出休息时间", "expected_version": 0, "consent": True, **data})

    def test_explicit_consent_version_delete_and_no_automatic_inference(self):
        self.assertEqual(self.client.get(self.path).json()["items"], [])
        self.assertEqual(self.command(consent=False).status_code, 422)
        saved = self.command()
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["items"][0]["source"], "user")
        self.assertEqual(self.command().status_code, 409)
        deleted = self.client.post(self.path, headers=self.headers, json={"action": "delete", "key": "travel_preferences", "expected_version": 1})
        self.assertEqual(deleted.json()["items"], [])
        self.assertEqual(self.command().status_code, 409)
        self.assertEqual(deleted.json()["versions"]["travel_preferences"], 2)
        self.assertEqual(self.command(expected_version=2).json()["items"][0]["version"], 3)

    def test_cross_account_read_write_and_csrf_fail_before_store(self):
        self.command()
        self.headers = self.login("pref-b")
        self.assertEqual(self.client.get(self.path).status_code, 404)
        self.assertEqual(self.command().status_code, 404)
        with patch.object(self.store, "set_preference", side_effect=AssertionError("must not enter")):
            self.assertEqual(self.client.post(self.path, json={}).status_code, 403)

    def test_fields_bounds_extra_and_query_rejected(self):
        for data in ({"owner_id": self.owner}, {"value": "x" * 501}, {"key": "authorization"}, {"value": "a\u0000b"}, {"expected_version": True}):
            self.assertEqual(self.command(**data).status_code, 422)
        self.assertEqual(self.client.get(self.path + "?owner=other").status_code, 422)
        self.assertEqual(self.client.post(self.path, headers=self.headers, content="{" + '"action":"set",' * 2000 + "}").status_code, 413)


if __name__ == "__main__":
    unittest.main()

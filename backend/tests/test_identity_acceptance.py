"""Harness-only checks: isolated credentials and no real business transport."""
import asyncio
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from app.storage import engine_for
from tools.identity_acceptance import controlled_app, prepare


class AcceptanceHarness(unittest.TestCase):
    def test_prepare_private_credentials_no_stdout_secrets_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            database, credentials = Path(folder) / "test.sqlite3", Path(folder) / "credentials.json"
            with patch.dict(os.environ, {"TRIPPILOT_DATABASE_PATH": str(database)}):
                output = io.StringIO()
                with redirect_stdout(output):
                    prepare(database, credentials)
                values = json.loads(credentials.read_text())
                self.assertEqual(len(values), 2)
                self.assertEqual(stat.S_IMODE(credentials.stat().st_mode), 0o600)
                for password in values.values():
                    self.assertNotIn(password, output.getvalue())
                before = database.read_bytes(), credentials.read_bytes()
                with self.assertRaises(ValueError):
                    prepare(database, credentials)
                self.assertEqual(before, (database.read_bytes(), credentials.read_bytes()))
                engine_for(str(database)).dispose(); engine_for.cache_clear()

    def test_business_inputs_are_explicitly_synthetic_and_models_blocked(self):
        from app.api.places import get_http_client
        from app.api.explanation import get_qwen_http_client
        from main import app
        previous = dict(app.dependency_overrides)
        try:
            controlled_app()
            async def check():
                async for client in app.dependency_overrides[get_http_client]():
                    response = await client.get("https://controlled.invalid/v5/place/text?keywords=住宿")
                    self.assertIn("合成测试", response.json()["pois"][0]["address"])
                    with self.assertRaises(AssertionError):
                        await client.get("https://controlled.invalid/unplanned")
                with self.assertRaises(AssertionError):
                    async for _ in app.dependency_overrides[get_qwen_http_client]():
                        self.fail("model dependency must never yield")
            asyncio.run(check())
        finally:
            app.dependency_overrides.clear(); app.dependency_overrides.update(previous)

"""Real isolated MySQL/ASGI initial inputs; all model sending is forbidden."""
from __future__ import annotations

from contextlib import asynccontextmanager
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx

from core.config import Settings, get_settings
from core.schemas.dialogue import SubmitDialogueTurn
from tests.mysql_support import temporary_mysql
from tools.echomind_full_acceptance_fixture import full_fixture_app, seed, turn_body, write_new
from tools.echomind_full_acceptance_protocol import load_manifest
from tools.echomind_travel_controlled import initialize, ORIGIN


class FullAcceptanceFixtureTests(unittest.IsolatedAsyncioTestCase):
    async def test_normal_owner_and_eight_independent_inputs_no_model_or_business_send(self):
        engine, cleanup = temporary_mysql()
        self.addCleanup(cleanup)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            root.chmod(0o700)
            anchor = root / "identity"
            anchor.write_text("a" * 32 + "\n")
            anchor.chmod(0o600)
            initialize(engine, anchor)
            @asynccontextmanager
            async def forbidden():
                raise AssertionError("setup must not obtain a model client")
                yield
            settings = Settings(_env_file=None, assistant_runtime="echomind_travel_full_v2",
                qwen_api_key="controlled-not-real", amap_web_key="controlled-not-real")
            with full_fixture_app(engine, anchor, settings, forbidden, lambda: "single") as (app, business):
                self.assertEqual(app.dependency_overrides[get_settings]().assistant_runtime, "echomind_travel_full_v2")
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
                    identity, fixtures, cookies = await seed(client, engine, load_manifest())
                    self.assertEqual(len(fixtures), 8)
                    self.assertEqual(len({v["conversation"]["id"] for v in fixtures.values()}), 8)
                    self.assertIsNone(fixtures["f01"]["conversation"]["requirements"]["start_date"])
                    self.assertEqual(fixtures["f02"]["conversation"]["requirements"]["travelers"], 2)
                    self.assertTrue(cookies)
                    self.assertEqual(business.events, [])
                    for case in load_manifest()["operations"]:
                        body = turn_body(case, fixtures[case["id"]])
                        SubmitDialogueTurn.model_validate(body)
                        self.assertNotIn("expected", body)
                        self.assertNotIn("case_id", body)
                        for message in fixtures[case["id"]]["conversation"]["messages"]:
                            self.assertNotIn(case["id"], message["text"])
                    self.assertEqual(fixtures["f03"]["selection_context"]["items"][0]["name"], "受控景点甲")

    async def test_settings_scope_must_be_actual_full_runtime(self):
        with self.assertRaises(ValueError):
            with full_fixture_app(None, Path("/not-used"), Settings(_env_file=None), lambda: None, lambda: "single"):
                pass

    async def test_exclusive_private_evidence_and_no_silent_replacement(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            root.chmod(0o700)
            path = root / "record.json"
            write_new(path, {"synthetic": True})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                write_new(path, {"synthetic": False})
            link = root / "link"
            link.symlink_to(path)
            with self.assertRaises(ValueError):
                write_new(link, {})

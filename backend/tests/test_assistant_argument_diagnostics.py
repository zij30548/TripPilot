"""Actual anonymous API/MySQL receipts with controlled native failures only."""
from __future__ import annotations

import json
import importlib.util
from pathlib import Path
import tempfile
from types import ModuleType
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from api import assistant
from core.config import get_settings
from core.llm_utils import RuntimeFailure, tool_argument_diagnostics, validate_arguments
from core.schemas.chat import Intent
from memory.assistant_store import AssistantStore
from memory.chat_places_storage import ChatPlacesStore
from memory.chat_storage import ChatStore
from tests.mysql_support import temporary_mysql
from tools.echomind_travel_controlled import ControlledUpstream, controlled_app, initialize, native_tool


class AssistantArgumentDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.engine, cleanup = temporary_mysql()
        self.addCleanup(cleanup)
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="argument-diagnostic-api-"))).resolve()
        anchor = self.folder / "usage.identity"
        anchor.write_text(uuid4().hex + "\n", encoding="ascii")
        anchor.chmod(0o600)
        initialize(self.engine, anchor)
        self.upstream = ControlledUpstream()
        self.app, _ = self.enterContext(controlled_app(self.engine, anchor,
            origin="http://127.0.0.1:3000", upstream=self.upstream))
        self.settings = self.app.dependency_overrides[get_settings]()
        self.settings.assistant_runtime = "legacy"
        self.client = self.enterContext(TestClient(self.app))
        seed = self.client.get("/workspace/session").json()
        identity = self.client.post("/workspace/session", json={}, headers={"origin": "http://127.0.0.1:3000",
            "x-csrf-token": seed["csrf_token"]}).json()
        self.owner = identity["user"]["id"]
        self.workspace = str(uuid4())
        self.headers = {"origin": "http://127.0.0.1:3000", "x-csrf-token": identity["csrf_token"],
                        "x-trippilot-surface": "dialogue-v3"}
        self.chat, self.tasks, self.places = ChatStore(self.engine), AssistantStore(self.engine), ChatPlacesStore(self.engine)
        self.view = self.chat.create(self.owner, str(uuid4()))
        op = str(uuid4())
        reserved = self.chat.begin_turn(self.owner, self.view["id"], op, self.view["revision"], "去上海，两个人")
        self.view = self.chat.finish_turn(self.owner, self.view["id"], op, reserved["revision"],
            Intent(destination_id="shanghai", travelers=2).model_dump(mode="json"), "受控既有需求", [])
        self.original = self.view["requirements"]

    def submit(self):
        current = self.chat.get(self.owner, self.view["id"])
        return self.client.post(f"/chat/conversations/{self.view['id']}/assistant-turns", headers=self.headers,
            json={"operation_id": str(uuid4()), "expected_revision": current["revision"], "text": "先核对当前需求",
                "dialogue_context": {"workspace_version": self.workspace, "display": "none", "items": []}})

    def assert_failed_unchanged(self):
        view = self.chat.get(self.owner, self.view["id"])
        task = self.tasks.latest(self.owner, self.view["id"])
        self.assertEqual(view["requirements"], self.original)
        self.assertEqual(view["last_turn"]["state"], "failed")
        self.assertEqual(task["state"], "failed")
        self.assertFalse(any(row.get("tool") not in {None, "model"} for row in task["checkpoint"]["trace"]))
        self.assertTrue(all(row["service"] == "controlled_model" for row in self.upstream.events))
        return task

    def test_native_extra_field_is_rejected_before_handler_and_saved_safely(self):
        key, value = "PRIVATE_UNKNOWN_FIELD_DO_NOT_LOG", "PRIVATE_MODEL_BODY_DO_NOT_LOG"
        self.upstream.responses = [native_tool("update_trip_requirements", {key: value})]
        with patch("api.assistant.apply_selection", side_effect=AssertionError("invalid arguments reached handler")):
            response = self.submit()
        self.assertEqual(response.status_code, 502, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "assistant_tool_arguments")
        self.assertEqual(detail["diagnostics"]["errors"][0], {"path": [], "reason": "additional_fields", "count": 1})
        task = self.assert_failed_unchanged()
        self.assertEqual(task["checkpoint"]["result"]["argument_diagnostics"], detail["diagnostics"])
        for forbidden in (key, value):
            self.assertNotIn(forbidden, response.text + json.dumps(task))
        self.assertEqual(len(self.upstream.events), 1)

    def test_native_wrong_type_identifies_known_field_and_executes_no_tool(self):
        self.upstream.responses = [native_tool("request_clarification", {"reason": None})]
        response = self.submit()
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(response.json()["detail"]["diagnostics"]["errors"], [{"path": ["reason"],
            "reason": "type_mismatch", "expected_type": "string", "actual_type": "null"}])
        self.assert_failed_unchanged()

    def test_unknown_runtime_details_never_become_http_or_checkpoint_data(self):
        error = RuntimeFailure("tool_arguments")
        error.details = {"schema_version": "tool-argument-diagnostics-v1", "errors": [
            {"path": ["PRIVATE_UNKNOWN_FIELD"], "reason": "missing_required", "input": "PRIVATE_BODY"}], "truncated": False}
        class Broken:
            async def run(self, _request):
                raise error
        self.app.dependency_overrides[assistant.get_orchestrator] = lambda: lambda *_args, **_kwargs: Broken()
        response = self.submit()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("diagnostics", response.json()["detail"])
        task = self.assert_failed_unchanged()
        self.assertNotIn("argument_diagnostics", task["checkpoint"].get("result", {}))
        self.assertNotIn("PRIVATE_", response.text + json.dumps(task))

    @unittest.skipUnless(importlib.util.find_spec("jsonschema"), "full-candidate optional dependency")
    def test_full_failure_retains_receipt_surface_and_workspace_next_to_diagnostic(self):
        from echomind_core.integration import AdmittedTravelTurn
        from tests.test_dialogue_v3 import execution_receipt
        receipt = execution_receipt(models=1, tools=0)
        class FailedFull(AdmittedTravelTurn):
            async def preflight(self, _settings):
                return None
            async def run(self, _settings, _client, current, _places, _preferences):
                await current()
                try:
                    validate_arguments({"reason": None}, {"type": "object", "properties": {
                        "reason": {"type": "string"}}, "required": ["reason"], "additionalProperties": False})
                except RuntimeFailure as error:
                    error.trace = {"execution_receipt": receipt, "model_calls": 1, "tool_calls": 0, "events": []}
                    raise
        module = ModuleType("echomind_core.integration_full")
        module.AdmittedFullTravelTurn = FailedFull
        async def close():
            return None
        module.close_full_services = close
        self.settings.assistant_runtime = "echomind_travel_full_v2"
        with patch.dict("sys.modules", {"echomind_core.integration_full": module}):
            response = self.submit()
        self.assertEqual(response.status_code, 502, response.text)
        task = self.assert_failed_unchanged()
        result = task["checkpoint"]["result"]
        self.assertEqual(result["runtime"]["execution_receipt"], receipt)
        self.assertEqual(result["surface"], "dialogue-v3")
        self.assertEqual(result["workspace_version"], self.workspace)
        self.assertEqual(result["argument_diagnostics"], response.json()["detail"]["diagnostics"])

    def test_postcommit_failure_keeps_readback_503_and_uncertain_semantics(self):
        self.upstream.responses = [native_tool("travel_budget", {"action": "read_budget"})]
        original_sync = self.places.sync
        def fail_after_commit(*args, **kwargs):
            latest = self.chat.get(self.owner, self.view["id"])
            if latest["last_turn"] and latest["last_turn"]["state"] == "succeeded":
                validate_arguments(None, {"type": "integer"})
            return original_sync(*args, **kwargs)
        # API obtains the same store through its dependency, not a fake handler.
        from api.chat_places import get_chat_places_store
        self.app.dependency_overrides[get_chat_places_store] = lambda: self.places
        with patch.object(self.places, "sync", side_effect=fail_after_commit):
            response = self.submit()
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["detail"]["code"], "assistant_post_commit_readback_required")
        self.assertNotIn("diagnostics", response.json()["detail"])
        view = self.chat.get(self.owner, self.view["id"])
        self.assertEqual(view["last_turn"]["state"], "succeeded")
        task = self.tasks.latest(self.owner, self.view["id"])
        self.assertEqual(task["state"], "uncertain")
        self.assertEqual(task["checkpoint"]["continuation"]["kind"], "read_receipts_only")
        self.assertTrue(task["checkpoint"]["result"]["chat_committed"])
        self.assertIn("argument_diagnostics", task["checkpoint"]["result"])


if __name__ == "__main__":
    unittest.main()

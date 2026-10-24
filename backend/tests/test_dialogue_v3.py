"""V3 read batches and exact diagnostics; no real provider/business calls.

Protocol unit tests exercise production projections. HTTP tests use true
anonymous identity/CAS/persistence in an independent temporary MySQL database
and an explicitly controlled admission adapter, not claimed model execution.
"""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
from types import ModuleType
import unittest
from unittest.mock import patch
from uuid import uuid4

from pydantic import ValidationError

from api.assistant import ExecutionReceipt, TaskViewV2, _captured_skill_views, _full_trace_rows
from core.config import Settings, get_settings
from core.llm_utils import RuntimeFailure
from core.schemas.dialogue import DialogueActions, validate_action
from tooling.travel.dialogue import project_actions
from tools.full_travel_readiness import _receipt


def execution_receipt(*, models=10, tools=12, dispatch=None):
    return {"schema_version": "travel-execution-receipt-v1", "budget": {
        "version": "travel-task-budget-v1", "tier": "collaboration", "model_limit": 10,
        "tool_limit": 12, "output_tokens_reserved_limit": 16384, "deadline_seconds": 90.0,
        "shared": True, "real_call_authorization": False},
        "reservations": {"models": models, "tools": tools, "output_tokens": 0},
        "dispatch": dispatch or {"status": "unknown", "sent": None, "received": None,
            "failed": None, "cancelled": None, "uncertain": None}, "trace_complete": False}


SCHEDULE = ("travel_schedule", {"name": "read_schedule", "arguments": {"date": None, "day": 2}})
BUDGET = ("travel_budget", {"name": "read_budget", "arguments": {}})
WORKSPACE = "10000000-0000-4000-8000-000000000003"


def current_receipt(actions=None):
    conversation, task = _receipt(actions if actions is not None else [SCHEDULE, BUDGET])
    task["checkpoint"]["result"].update(surface="dialogue-v3", workspace_version=WORKSPACE)
    return conversation, task


class DialogueV3ContractTests(unittest.TestCase):
    def test_exact_independent_pair_and_each_single_are_accepted_but_not_executed(self):
        for values in ([], [SCHEDULE], [BUDGET], [SCHEDULE, BUDGET], [BUDGET, SCHEDULE]):
            with self.subTest(values=values):
                conversation, task = current_receipt(values)
                view = project_actions(conversation, task)
                self.assertEqual([value["name"] for value in view["actions"]], [value[1]["name"] for value in values])
                self.assertEqual(view["workspace_version"], WORKSPACE)
                self.assertEqual(view["operation_id"], conversation["last_turn"]["operation_id"])
                self.assertEqual(view["program_messages"], [])
                self.assertNotIn("executed", view)

    def test_duplicate_write_mix_three_items_and_wrong_group_are_rejected(self):
        write = ("travel_schedule", {"name": "generate_schedule", "arguments": {}})
        for values in ([SCHEDULE, SCHEDULE], [SCHEDULE, write], [BUDGET, write], [SCHEDULE, BUDGET, SCHEDULE]):
            conversation, task = current_receipt(values)
            with self.subTest(values=values), self.assertRaises(RuntimeFailure):
                project_actions(conversation, task)
        conversation, task = current_receipt([SCHEDULE])
        task["checkpoint"]["result"]["tool_results"][0]["name"] = "travel_budget"
        with self.assertRaises(RuntimeFailure):
            project_actions(conversation, task)

    def test_nonempty_actions_require_frozen_workspace_and_current_operation(self):
        conversation, task = current_receipt()
        for workspace in (None, "bad", True):
            bad = deepcopy(task)
            bad["checkpoint"]["result"]["workspace_version"] = workspace
            with self.subTest(workspace=workspace), self.assertRaises(RuntimeFailure):
                project_actions(conversation, bad)
        for field, value in (("stale", True), ("state", "failed"), ("conversation_revision", 1),
                             ("operation_id", str(uuid4()))):
            view = project_actions(conversation, task | {field: value})
            self.assertEqual(view["actions"], [])
            self.assertIsNone(view["operation_id"])

    def test_old_surface_does_not_become_new_action_authority(self):
        conversation, task = _receipt([SCHEDULE])
        self.assertEqual(project_actions(conversation, task)["actions"], [])
        view = project_actions(conversation, None)
        self.assertEqual(set(view), {"schema_version", "conversation_id", "revision", "operation_id",
                                     "workspace_version", "actions", "program_messages"})

    def test_program_projection_is_complete_bound_and_only_full_runtime(self):
        conversation, task = current_receipt([])
        messages = ["整理摘要：" + "完整条件、例外和来源。" * 400]
        task["checkpoint"]["result"]["runtime"] = {"runtime": "echomind_travel_full_v2", "program_messages": messages}
        view = project_actions(conversation, task)
        self.assertEqual(view["program_messages"], messages)
        for invalid in (messages + ["x"] * 4, [""], ["x" * 32001], ["中" * 32000, "中"], ["x\x00"]):
            bad = deepcopy(task)
            bad["checkpoint"]["result"]["runtime"]["program_messages"] = invalid
            with self.subTest(size=len(invalid)), self.assertRaises(RuntimeFailure):
                project_actions(conversation, bad)
        task["checkpoint"]["result"]["runtime"]["runtime"] = "legacy"
        with self.assertRaises(RuntimeFailure):
            project_actions(conversation, task)

    def test_dispatch_unknown_is_not_zero_and_observed_totals_must_match(self):
        value = ExecutionReceipt.model_validate(execution_receipt())
        self.assertIsNone(value.dispatch.sent)
        observed = {"status": "observed", "sent": 3, "received": 1, "failed": 1, "cancelled": 0, "uncertain": 1}
        self.assertEqual(ExecutionReceipt.model_validate(execution_receipt(dispatch=observed)).dispatch.sent, 3)
        for dispatch in (observed | {"received": 2}, observed | {"sent": 11}, observed | {"failed": None},
                         observed | {"status": "unknown"}, observed | {"sent": True}):
            with self.subTest(dispatch=dispatch), self.assertRaises(ValidationError):
                ExecutionReceipt.model_validate(execution_receipt(dispatch=dispatch))

    def test_all_reservations_survive_projection_and_public_model_matches(self):
        receipt = execution_receipt()
        rows = _full_trace_rows({"execution_receipt": receipt})
        self.assertEqual(len(rows), 22)
        self.assertEqual(sum(row["tool"] == "model" for row in rows), 10)
        self.assertTrue(all(row["state"] == "uncertain" for row in rows))
        view = {"id": str(uuid4()), "state": "succeeded", "model_calls": 10, "tool_calls": 12,
                "stop_reason": "answered", "steps": [], "execution_receipt": receipt}
        TaskViewV2.model_validate(view)
        with self.assertRaises(ValidationError):
            TaskViewV2.model_validate(view | {"model_calls": 3})
        with self.assertRaises(ValidationError):
            ExecutionReceipt.model_validate(receipt | {"budget": receipt["budget"] | {"deadline_seconds": 120.0}})

    @unittest.skipUnless(importlib.util.find_spec("jsonschema"), "requires existing optional echo dependency")
    def test_full_task_skills_are_captured_hashes_not_current_files_or_prompt_content(self):
        from echomind_core.travel_package import TravelDomainPackage
        package = TravelDomainPackage().capture().summary()
        runtime = {"runtime": "echomind_travel_full_v2", "skills": package, "domain_details": {"skills": package}}
        with patch.object(TravelDomainPackage, "capture", side_effect=AssertionError("do not reload task rules")):
            rows = _captured_skill_views(runtime)
        self.assertEqual(len(rows), 8)
        self.assertEqual([row["id"] for row in rows], [row["id"] for row in package["skills"]["skills"]])
        self.assertTrue(all(set(row) == {"id", "name", "version"} for row in rows))
        self.assertTrue(all(row["version"].startswith("sha256-") for row in rows))
        for mutate in (lambda value: value.pop("domain_details"),
                       lambda value: value["skills"]["skills"]["skills"][0].update(sha256="a" * 64),
                       lambda value: value["skills"]["skills"]["skills"][0].update(prompt="not allowed")):
            bad = deepcopy(runtime)
            mutate(bad)
            with self.assertRaises(RuntimeFailure):
                _captured_skill_views(bad)
        self.assertEqual(_captured_skill_views({"execution_receipt": execution_receipt()}), [])
        self.assertIsNone(_captured_skill_views({}))


@unittest.skipUnless(importlib.util.find_spec("jsonschema"), "requires existing optional echo dependency")
class DialogueV3MySQLHTTPTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from memory.chat_storage import ChatStore
        from memory.assistant_store import AssistantStore
        from tests.mysql_support import temporary_mysql
        from tools.echomind_travel_controlled import ControlledUpstream, controlled_app, initialize
        self.engine, cleanup = temporary_mysql()
        self.addCleanup(cleanup)
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="v3-http-"))).resolve()
        anchor = self.folder / "usage.identity"
        anchor.write_text(uuid4().hex + "\n", encoding="ascii")
        anchor.chmod(0o600)
        initialize(self.engine, anchor)
        self.upstream = ControlledUpstream()
        app, _ = self.enterContext(controlled_app(self.engine, anchor, origin="http://127.0.0.1:3000", upstream=self.upstream))
        self.app = app
        self.settings = app.dependency_overrides[get_settings]()
        self.settings.assistant_runtime = "echomind_travel_full_v2"
        self.client = self.enterContext(TestClient(app))
        first_response = self.client.get("/workspace/session")
        self.assertEqual(first_response.status_code, 200, first_response.text)
        first = first_response.json()
        identity_response = self.client.post("/workspace/session", json={}, headers={"origin": "http://127.0.0.1:3000",
            "x-csrf-token": first["csrf_token"]})
        self.assertEqual(identity_response.status_code, 200, identity_response.text)
        identity = identity_response.json()
        self.owner = identity["user"]["id"]
        self.headers = {"origin": "http://127.0.0.1:3000", "x-csrf-token": identity["csrf_token"],
                        "x-trippilot-surface": "dialogue-v3"}
        self.view = self.client.post("/chat/conversations", headers=self.headers,
            json={"operation_id": str(uuid4())}).json()
        self.path = "/chat/conversations/" + self.view["id"]
        self.chat, self.tasks = ChatStore(self.engine), AssistantStore(self.engine)
        self.plan = {"preflight": True, "failure": False, "calls": 0, "models": 10, "tools": 12}
        plan = self.plan
        from echomind_core.integration import AdmittedTravelTurn, IntegratedOutcome
        class ControlledFullAdapter(AdmittedTravelTurn):
            async def preflight(self, settings):
                if not plan["preflight"]:
                    raise RuntimeFailure("full_resources_unavailable")

            async def run(self, settings, client, current, read_places, read_preferences):
                await current()
                plan["calls"] += 1
                receipt = execution_receipt(models=plan["models"], tools=plan["tools"])
                trace = {"execution_receipt": receipt, "model_calls": plan["models"], "tool_calls": plan["tools"],
                         "events": [], "stop_reason": "awaiting_user"}
                if plan["failure"]:
                    error = RuntimeFailure("controlled_full_failure")
                    error.trace = trace
                    raise error
                rows = [{"name": group, "data": {"message": "未执行的只读提议。", "dialogue_action":
                    validate_action(action["name"], action["arguments"])}} for group, action in (SCHEDULE, BUDGET)]
                from echomind_core.travel_package import TravelDomainPackage
                # Match the production adapter's bounded JSON detachment;
                # AgentProfile tuple fields are not checkpoint JSON values.
                package = json.loads(json.dumps(TravelDomainPackage().capture().summary()))
                return IntegratedOutcome(self.initial["requirements"], self.initial["sources"], self.initial["issues"],
                    "只读提议，等待浏览器。", rows, trace,
                    {"runtime": "echomind_travel_full_v2", "execution_receipt": receipt,
                     "skills": package, "domain_details": {"skills": package}})
        fake = ModuleType("echomind_core.integration_full")
        fake.AdmittedFullTravelTurn = ControlledFullAdapter
        async def close_controlled_services():
            # This adapter opens no native resources; satisfy the same module
            # lifecycle contract without weakening production shutdown checks.
            return None
        fake.close_full_services = close_controlled_services
        self.enterContext(patch.dict("sys.modules", {"echomind_core.integration_full": fake}))

    def submit(self, *, operation=None, context=True, headers=None):
        current = self.chat.get(self.owner, self.view["id"])
        body = {"operation_id": operation or str(uuid4()), "expected_revision": current["revision"],
                "text": "读取当前安排与费用"}
        if context:
            body["dialogue_context"] = {"workspace_version": WORKSPACE, "display": "schedule", "items": []}
        return self.client.post(self.path + "/assistant-turns", headers=headers or self.headers, json=body)

    def test_true_mysql_admission_and_exact_readback_keep_all_counts_and_binding(self):
        response = self.submit()
        self.assertEqual(response.status_code, 200, response.text)
        task = self.tasks.latest(self.owner, self.view["id"])
        self.assertEqual(len(task["checkpoint"]["trace"]), 22)
        result = self.client.get(self.path + "/dialogue-action", headers=self.headers)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(len(result.json()["actions"]), 2)
        self.assertEqual(result.json()["workspace_version"], WORKSPACE)
        state = self.client.get(self.path + "/assistant-state", headers=self.headers)
        self.assertEqual(state.status_code, 200, state.text)
        self.assertEqual(state.json()["schema_version"], "assistant-state-v2")
        self.assertEqual((state.json()["task"]["model_calls"], state.json()["task"]["tool_calls"]), (10, 12))
        self.assertIsNone(state.json()["task"]["execution_receipt"]["dispatch"]["sent"])
        self.assertEqual(len(state.json()["skills"]), 8)
        self.assertTrue(all(row["version"].startswith("sha256-") for row in state.json()["skills"]))
        old = self.client.get(self.path + "/assistant-state")
        self.assertEqual(old.status_code, 409, old.text)
        self.assertEqual(old.json()["detail"]["code"], "assistant_state_surface_required")
        self.assertNotIn("model_calls", old.json())
        self.assertEqual(self.upstream.events, [])

    def test_preflight_failure_is_503_before_turn_task_or_send(self):
        self.plan["preflight"] = False
        response = self.submit()
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["detail"]["code"], "assistant_full_runtime_not_ready")
        self.assertEqual(self.chat.get(self.owner, self.view["id"])["revision"], 0)
        self.assertIsNone(self.tasks.latest(self.owner, self.view["id"]))
        self.assertEqual(self.plan["calls"], 0)
        self.assertEqual(self.upstream.events, [])

    def test_full_requires_surface_and_workspace_and_duplicate_operation_is_burned(self):
        self.assertEqual(self.submit(context=False).status_code, 422)
        self.assertEqual(self.submit(headers=self.headers | {"x-trippilot-surface": "dialogue-v2"}).status_code, 422)
        operation = str(uuid4())
        self.assertEqual(self.submit(operation=operation).status_code, 200)
        self.assertEqual(self.submit(operation=operation).status_code, 409)
        self.assertEqual(self.plan["calls"], 1)

    def test_failure_keeps_aggregate_receipt_without_action_success(self):
        self.plan["failure"] = True
        response = self.submit()
        self.assertEqual(response.status_code, 502, response.text)
        state = self.client.get(self.path + "/assistant-state", headers=self.headers).json()
        self.assertEqual(state["task"]["state"], "failed")
        self.assertEqual((state["task"]["model_calls"], state["task"]["tool_calls"]), (10, 12))
        self.assertEqual(self.client.get(self.path + "/dialogue-action", headers=self.headers).json()["actions"], [])

    def test_new_surface_remains_usable_with_legacy_and_direct_candidate(self):
        from tools.echomind_travel_controlled import native_tool
        for runtime in ("legacy", "echomind_travel_v1"):
            with self.subTest(runtime=runtime):
                self.settings.assistant_runtime = runtime
                self.upstream.responses = [native_tool("travel_budget", {"action": "read_budget"})]
                response = self.submit()
                self.assertEqual(response.status_code, 200, response.text)
                action = self.client.get(self.path + "/dialogue-action", headers=self.headers)
                self.assertEqual(action.status_code, 200, action.text)
                self.assertEqual(action.json()["actions"], [{"name": "read_budget", "arguments": {}}])
                self.assertEqual(action.json()["workspace_version"], WORKSPACE)
                state = self.client.get(self.path + "/assistant-state", headers=self.headers)
                self.assertEqual(state.status_code, 200, state.text)
                self.assertIsNone(state.json()["task"]["execution_receipt"])
                old = self.client.get(self.path + "/assistant-state")
                self.assertEqual(old.status_code, 200, old.text)
                self.assertEqual(old.json()["schema_version"], "assistant-state-v1")
                self.assertEqual(len(old.json()["skills"]), 3)
        self.assertEqual(self.plan["calls"], 0)


if __name__ == "__main__":
    unittest.main()

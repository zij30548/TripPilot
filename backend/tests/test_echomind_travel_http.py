"""Real independent MySQL + production HTTP admission; controlled upstream only."""
from __future__ import annotations

from concurrent.futures import CancelledError as FutureCancelledError, ThreadPoolExecutor
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import text

if any(importlib.util.find_spec(name) is None for name in ("jsonschema", "prometheus_client")):
    raise unittest.SkipTest("requires explicit echomind candidate dependencies")

from memory.chat_storage import ChatStore
from memory.assistant_store import AssistantStore
from tests.mysql_support import temporary_mysql
from tools.echomind_travel_controlled import (
    REQUIREMENTS_TEXT, VERSION, ControlledError, ControlledEventJournal, ControlledUpstream, admin_url, controlled_app,
    initialize, native_text, native_tool, prepare, scenario_tools, serve, validate_scenarios,
)
from core.llm_utils import RuntimeFailure

ORIGIN = "http://127.0.0.1:3000"


class TravelMySQLHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine, cleanup = temporary_mysql()
        self.addCleanup(cleanup)
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="echo-http-"))).resolve()
        self.anchor = self.folder / "usage.identity"
        self.anchor.write_text(uuid4().hex + "\n", encoding="ascii")
        self.anchor.chmod(0o600)
        initialize(self.engine, self.anchor)
        self.upstream = ControlledUpstream()
        app, _ = self.enterContext(controlled_app(self.engine, self.anchor, origin=ORIGIN, upstream=self.upstream))
        self.a = self.enterContext(TestClient(app))
        self.b = self.enterContext(TestClient(app))
        self.identity = self.bootstrap(self.a)
        self.headers = {"origin": ORIGIN, "x-csrf-token": self.identity["csrf_token"], "x-trippilot-surface": "dialogue-v2"}
        response = self.a.post("/chat/conversations", headers=self.headers, json={"operation_id": str(uuid4())})
        self.assertEqual(response.status_code, 200, response.text)
        self.view = response.json()
        self.path = f"/chat/conversations/{self.view['id']}"
        self.owner = self.identity["user"]["id"]

    def bootstrap(self, client):
        first = client.get("/workspace/session")
        self.assertEqual(first.status_code, 200, first.text)
        response = client.post("/workspace/session", json={}, headers={"origin": ORIGIN, "x-csrf-token": first.json()["csrf_token"]})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def submit(self, statement="查看当前需求", operation=None):
        self.view = self.a.get(self.path).json()
        return self.a.post(self.path + "/assistant-turns", headers=self.headers, json={
            "operation_id": operation or str(uuid4()), "expected_revision": self.view["revision"], "text": statement})

    def attempts(self):
        with self.engine.connect() as db:
            return db.execute(text("SELECT owner_id,service,state FROM usage_attempts ORDER BY created_at,id")).all()

    def test_native_call_feedback_program_text_and_mysql_restart_readback(self):
        response = self.submit()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertNotIn("CONTROLLED_FREE_PROSE", str(result))
        self.assertIn("需求", result["messages"][-1]["text"])
        self.assertEqual([row["native_tool_feedback"] for row in self.upstream.events], [False, True])
        self.assertEqual(len(self.attempts()), 2)
        self.assertTrue(all(row == (self.owner, "qwen_text", "received") for row in self.attempts()))
        task = AssistantStore(self.engine).latest(self.owner, self.view["id"])
        self.assertFalse(task["stale"])
        self.engine.dispose()
        self.assertEqual(ChatStore(self.engine).get(self.owner, self.view["id"]), result)
        self.assertEqual(AssistantStore(self.engine).get(self.owner, task["id"]), task)

    def test_requirements_persist_as_unconfirmed_draft_not_confirmed_poi(self):
        response = self.submit(REQUIREMENTS_TEXT)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["requirements"]["destination_id"], "shanghai")
        self.assertEqual(result["requirements"]["budget_cny"], "3000")
        self.assertEqual(result["requirements"]["required_place_hints"], ["受控景点甲"])
        self.assertIsNone(result["confirmed_revision"])
        places = self.a.get(self.path + "/places").json()
        self.assertIsNone(places["accommodation"])
        self.assertEqual(places["required_places"], [])
        self.assertEqual(len(self.attempts()), 2)

    def test_budget_action_is_unexecuted_proposal_not_invented_cost(self):
        response = self.submit("查询本次费用和未知项")
        self.assertEqual(response.status_code, 200, response.text)
        proposal = self.a.get(self.path + "/dialogue-action")
        self.assertEqual(proposal.status_code, 200, proposal.text)
        self.assertEqual(proposal.json()["action"], {"name": "read_budget", "arguments": {}})
        self.assertNotIn("¥0", response.json()["messages"][-1]["text"])
        task = AssistantStore(self.engine).latest(self.owner, self.view["id"])
        self.assertEqual(task["checkpoint"]["result"]["surface"], "dialogue-v2")
        self.assertNotIn("CONTROLLED_FREE_PROSE", str(task))

    def test_same_operation_cannot_send_again_and_owner_b_cannot_read(self):
        operation = str(uuid4())
        self.assertEqual(self.submit(operation=operation).status_code, 200)
        before = len(self.upstream.events)
        response = self.submit(operation=operation)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(len(self.upstream.events), before)
        self.bootstrap(self.b)
        self.assertEqual(self.b.get(self.path).status_code, 404)
        self.assertEqual(self.b.get(self.path + "/assistant-state").status_code, 404)
        self.assertEqual(self.b.get(self.path + "/dialogue-action").status_code, 404)
        self.assertEqual(self.b.get("/chat/conversations").json()["items"], [])

    def test_auth_csrf_wrong_surface_and_bad_body_are_rejected_before_model(self):
        body = {"operation_id": str(uuid4()), "expected_revision": 0, "text": "查看当前需求"}
        self.assertEqual(self.b.post(self.path + "/assistant-turns", json=body).status_code, 401)
        self.assertEqual(self.a.post(self.path + "/assistant-turns", json=body).status_code, 403)
        wrong_surface = self.headers | {"x-trippilot-surface": "dialogue-v1"}
        self.assertEqual(self.a.post(self.path + "/assistant-turns", headers=wrong_surface, json=body).status_code, 422)
        self.assertEqual(self.a.post(self.path + "/assistant-turns", headers=self.headers, json=body | {"owner_id": "b" * 32}).status_code, 422)
        self.assertEqual(self.upstream.events, [])
        self.assertEqual(self.attempts(), [])

    def test_bad_native_tool_preserves_failed_receipts_without_partial_action(self):
        self.upstream.responses = [native_tool("unknown_tool", {})]
        response = self.submit()
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(len(self.upstream.events), 1)
        chat = self.a.get(self.path).json()
        self.assertEqual(chat["last_turn"]["state"], "failed")
        self.assertEqual(AssistantStore(self.engine).latest(self.owner, self.view["id"])["state"], "failed")
        self.assertEqual(self.a.get(self.path + "/dialogue-action").json()["action"], None)

    def test_feedback_transport_failure_keeps_both_attempts_and_no_success_proposal(self):
        self.upstream.responses = [native_tool("travel_budget", {"action": "read_budget"}), httpx.ReadTimeout("controlled timeout")]
        response = self.submit("查询本次费用和未知项")
        self.assertIn(response.status_code, {502, 504}, response.text)
        self.assertEqual(len(self.attempts()), 2)
        self.assertEqual(sorted(row[2] for row in self.attempts()), ["failed", "received"])
        self.assertEqual(self.a.get(self.path + "/dialogue-action").json()["action"], None)

    def test_quota_before_feedback_blocks_second_actual_send_and_readonly_still_works(self):
        from core.usage_runtime import get_usage_store
        from core.business_usage import DailyLimit
        usage = get_usage_store()
        usage.settings.business_daily_limits["qwen_text"] = DailyLimit(account=1, **{"global": 100})
        response = self.submit()
        self.assertEqual(response.status_code, 429, response.text)
        self.assertEqual(response.json()["detail"]["code"], "business_quota_exhausted")
        self.assertEqual(len(self.upstream.events), 1)
        self.assertEqual(len(self.attempts()), 1)
        self.assertEqual(self.a.get("/saved-trips").status_code, 200)
        self.assertEqual(self.a.get(self.path).status_code, 200)

    def test_cancel_during_controlled_send_cannot_commit_late_success(self):
        self.upstream.pause_next = True
        operation = str(uuid4())
        payload = {"operation_id": operation, "expected_revision": 0, "text": "查看当前需求"}
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.a.post, self.path + "/assistant-turns", headers=self.headers, json=payload)
            try:
                self.assertTrue(self.upstream.entered.wait(10))
                cancelled = self.a.post(self.path + "/cancel", headers=self.headers, json={"operation_id": operation})
                self.assertEqual(cancelled.status_code, 200, cancelled.text)
            finally:
                self.upstream.release.set()
            try:
                response = future.result(timeout=10)
            except FutureCancelledError:
                # TestClient's ASGI portal exposes task cancellation rather
                # than fabricating a HTTP response for an already closed call.
                response = None
        if response is not None:
            self.assertIn(response.status_code, {409, 499, 502}, response.text)
        current = self.a.get(self.path).json()
        self.assertEqual(current["last_turn"]["state"], "cancelled")
        self.assertEqual(len(self.attempts()), 1)
        self.assertIn(self.attempts()[0][2], {"cancelled", "received"})
        self.assertEqual(self.a.get(self.path + "/dialogue-action").json()["action"], None)


class ControlledHarnessSafetyTests(unittest.TestCase):
    def test_all_scripted_actions_use_existing_exact_argument_contracts(self):
        plan = scenario_tools()
        validate_scenarios(plan)
        self.assertEqual(plan["查看历史行程"], ("travel_archives", {"action": "list_saved", "offset": 0}))
        with self.assertRaises(RuntimeFailure):
            validate_scenarios({"controlled": ("travel_archives", {"action": "list_saved"})})

    def test_administrator_target_is_only_independent_loopback_control(self):
        accepted = "mysql+pymysql://controlled@127.0.0.1:13306/trippilot_test_control"
        self.assertEqual(admin_url(accepted).database, "trippilot_test_control")
        for raw in ("", accepted.replace("127.0.0.1", "localhost"), accepted.replace("13306", "3306"),
                    accepted.replace("trippilot_test_control", "trippilot"), accepted + "?charset=utf8mb4"):
            with self.subTest(raw_type="controlled"), self.assertRaises(ControlledError):
                admin_url(raw)

    def test_existing_output_fails_before_creating_or_mutating_any_database(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder).resolve()
            with patch("tools.echomind_travel_controlled.create_engine") as create:
                with self.assertRaises(FileExistsError):
                    prepare(path, "mysql+pymysql://controlled@127.0.0.1:13306/trippilot_test_control")
            create.assert_not_called()
            self.assertEqual(list(path.iterdir()), [])

    def test_serve_binds_callable_not_context_tuple_and_only_final_fixed_port(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder).resolve()
            runtime = {"schema_version": VERSION, "database_url": "mysql+pymysql://tpec_" + "a" * 16
                + ":controlled-not-a-real-password@127.0.0.1:13306/trippilot_test_" + "b" * 32 + "?charset=utf8mb4",
                "origin": "http://127.0.0.1:3112"}
            config = path / "runtime.json"
            config.write_text(json.dumps(runtime), encoding="utf-8"); config.chmod(0o600)
            fake_engine = MagicMock()
            fake_app = MagicMock()

            @contextmanager
            def context(*_args, **_kwargs):
                yield fake_app, ControlledUpstream()

            with patch("tools.echomind_travel_controlled.create_engine", return_value=fake_engine), \
                    patch("tools.echomind_travel_controlled.controlled_app", context), patch("uvicorn.run") as run:
                serve(path, 8812)
            self.assertIs(run.call_args.args[0], fake_app)
            self.assertEqual(run.call_args.kwargs["host"], "127.0.0.1")
            self.assertEqual(run.call_args.kwargs["port"], 8812)
            self.assertFalse(run.call_args.kwargs["access_log"])
            counts = list(path.glob("counts-*.json"))
            self.assertEqual(len(counts), 1)
            self.assertNotIn("controlled-not-a-real-password", counts[0].read_text())
            self.assertEqual(json.loads(counts[0].read_text())["real_model_calls"], 0)
            with patch("tools.echomind_travel_controlled.create_engine") as create:
                with self.assertRaises(ControlledError):
                    serve(path, 8000)
            create.assert_not_called()

    def test_private_config_links_and_unsafe_mode_are_rejected_without_engine(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder).resolve()
            target = path / "not-runtime.json"
            target.write_text("{}", encoding="utf-8")
            config = path / "runtime.json"
            config.symlink_to(target)
            with patch("tools.echomind_travel_controlled.create_engine") as create:
                with self.assertRaises(ControlledError):
                    serve(path, 8812)
                config.unlink()
                config.write_text("{}", encoding="utf-8"); config.chmod(0o644)
                with self.assertRaises(ControlledError):
                    serve(path, 8812)
            create.assert_not_called()


class ControlledTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_event_is_private_durable_and_readable_before_response_and_shutdown(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = ControlledEventJournal(Path(folder).resolve())
            self.addCleanup(journal.close)
            upstream = ControlledUpstream([native_text()], journal=journal)
            request = httpx.Request("POST", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "PRIVATE_PROMPT_DO_NOT_LOG"}]},
                headers={"authorization": "Bearer PRIVATE_AUTH_DO_NOT_LOG"})
            response = await upstream.handle(request)
            self.assertEqual(response.status_code, 200)
            self.assertGreaterEqual(journal._descriptor, 0)  # No shutdown hook needed for persistence.
            raw = journal.path.read_text()
            rows = [json.loads(line) for line in raw.splitlines()]
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1]["ordinal"], 1)
            self.assertEqual(rows[1]["event"]["service"], "controlled_model")
            self.assertEqual(set(rows[1]["event"]), {"service", "payload_sha256", "native_tool_feedback"})
            self.assertNotIn("PRIVATE_PROMPT_DO_NOT_LOG", raw)
            self.assertNotIn("PRIVATE_AUTH_DO_NOT_LOG", raw)
            self.assertEqual(journal.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(len(upstream.summary()["events"]), 1)

    async def test_evidence_fsync_failure_prevents_response_and_latches_future_simulation(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = ControlledEventJournal(Path(folder).resolve())
            self.addCleanup(journal.close)
            upstream = ControlledUpstream([native_text()], journal=journal)
            request = httpx.Request("POST", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "controlled"}]})
            with patch("tools.echomind_travel_controlled.os.fsync", side_effect=OSError("PRIVATE_PATH_DO_NOT_LOG")):
                with self.assertRaisesRegex(ControlledError, "^controlled_evidence_unavailable$"):
                    await upstream.handle(request)
            self.assertEqual(upstream.responses, [native_text()])
            self.assertEqual(upstream.summary()["events"], [])
            size = journal.path.stat().st_size
            with self.assertRaisesRegex(ControlledError, "^controlled_evidence_unavailable$"):
                await upstream.handle(request)
            self.assertEqual(journal.path.stat().st_size, size)
            self.assertEqual(upstream.responses, [native_text()])

    async def test_journal_rejects_private_fields_and_never_overwrites_prior_sessions(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder).resolve()
            first = ControlledEventJournal(directory)
            self.addCleanup(first.close)
            first.append({"service": "controlled_poi", "count": 1})
            original = first.path.read_bytes()
            second = ControlledEventJournal(directory)
            self.addCleanup(second.close)
            self.assertNotEqual(first.path, second.path)
            with self.assertRaisesRegex(ControlledError, "^controlled_evidence_unavailable$"):
                second.append({"service": "controlled_model", "private_prompt": "DO_NOT_LOG"})
            self.assertEqual(first.path.read_bytes(), original)
            self.assertNotIn(b"private_prompt", second.path.read_bytes())
            self.assertNotIn(b"DO_NOT_LOG", second.path.read_bytes())

    async def test_bounded_event_count_refuses_more_without_truncating_existing_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = ControlledEventJournal(Path(folder).resolve())
            self.addCleanup(journal.close)
            with patch("tools.echomind_travel_controlled.MAX_EVENTS", 1):
                journal.append({"service": "controlled_walking"})
                before = journal.path.read_bytes()
                with self.assertRaisesRegex(ControlledError, "^controlled_evidence_unavailable$"):
                    journal.append({"service": "controlled_walking"})
            self.assertEqual(journal.path.read_bytes(), before)

    async def test_unknown_upstream_has_no_network_fallback(self):
        controlled = ControlledUpstream()
        async with httpx.AsyncClient(transport=httpx.MockTransport(controlled.handle)) as client:
            with self.assertRaisesRegex(ControlledError, "^external_request_forbidden$"):
                await client.get("https://unapproved.invalid/anything")
        self.assertEqual(controlled.events, [])

    async def test_unknown_question_fails_instead_of_silently_generating_fake_success(self):
        controlled = ControlledUpstream()
        async with httpx.AsyncClient(transport=httpx.MockTransport(controlled.handle)) as client:
            with self.assertRaisesRegex(ControlledError, "^controlled_question_not_in_plan$"):
                await client.post("https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                    json={"messages": [{"role": "user", "content": "not a controlled scenario"}]})
        self.assertEqual(len(controlled.events), 1)
        self.assertEqual(controlled.summary()["real_model_calls"], 0)


if __name__ == "__main__":
    unittest.main()

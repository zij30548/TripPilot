"""Offline runner admission/recording checks; no live transport or fixture DB."""
from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from starlette.requests import Request

from core.config import Settings
from tools import echomind_full_acceptance_runner as runner
from tools.echomind_full_acceptance_transport import FullTransportObservation

CID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OPERATION = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
WORKSPACE = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"


def fixture():
    return {"conversation": {"id": CID, "revision": 3}, "operation_id": OPERATION,
        "selection_context": {"workspace_version": WORKSPACE, "display": "none", "items": []}}


def body(case):
    return {"operation_id": OPERATION, "expected_revision": 3, "text": case["text"],
        "dialogue_context": {"workspace_version": WORKSPACE, "display": "none", "items": []}}


def request(value, *, cid=CID, surface="dialogue-v3"):
    raw = json.dumps(value, ensure_ascii=False).encode()
    delivered = False
    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": raw, "more_body": False}
    return Request({"type": "http", "method": "POST", "path": f"/chat/conversations/{cid}/assistant-turns",
        "path_params": {"conversation_id": cid}, "headers": [(b"x-trippilot-surface", surface.encode())],
        "query_string": b"", "scheme": "http", "server": ("127.0.0.1", 8812),
        "client": ("127.0.0.1", 12345)}, receive)


class CheckedRequestTests(unittest.TestCase):
    def setUp(self):
        self.case = {"id": "f01", "text": "日期还未决定，请先澄清"}

    def test_exact_question_conversation_revision_and_context_required(self):
        good = body(self.case)
        accepted = runner.checked_request(self.case, fixture(), good, CID)
        self.assertEqual(accepted, good)
        for field, replacement in (("text", "不同的临时简单问题"), ("expected_revision", 4),
                                   ("dialogue_context", None)):
            changed = deepcopy(good)
            changed[field] = replacement
            with self.subTest(field=field), self.assertRaises(ValueError):
                runner.checked_request(self.case, fixture(), changed, CID)
        with self.assertRaises(ValueError):
            runner.checked_request(self.case, fixture(), good, "dddddddd-dddd-4ddd-8ddd-dddddddddddd")

    def test_strict_shape_and_returned_capture_not_shared_with_later_input_mutation(self):
        good = body(self.case)
        for changed in ({**good, "owner_id": "client-owner"}, {**good, "expected_revision": True},
                        {**good, "operation_id": "not-a-uuid"}):
            with self.assertRaises(ValueError):
                runner.checked_request(self.case, fixture(), changed, CID)
        captured = runner.checked_request(self.case, fixture(), good, CID)
        good["dialogue_context"]["display"] = "budget"
        good["text"] = "later edit"
        self.assertEqual(captured["dialogue_context"]["display"], "none")
        self.assertEqual(captured["text"], self.case["text"])

    def test_api_operation_and_entire_selection_context_are_frozen(self):
        for field, replacement in (("operation_id", "dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
                ("dialogue_context", {"workspace_version": WORKSPACE, "display": "budget", "items": []}),
                ("dialogue_context", {"workspace_version": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                                       "display": "none", "items": []})):
            changed = body(self.case)
            changed[field] = replacement
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "unplanned"):
                runner.checked_request(self.case, fixture(), changed, CID)

    def test_chrome_context_identity_is_captured_live_not_replaced_by_predicted_uuid(self):
        case = {**self.case, "channel": "chrome"}
        changed = body(case)
        changed["operation_id"] = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
        changed["dialogue_context"]["workspace_version"] = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
        accepted = runner.checked_request(case, fixture(), changed, CID)
        self.assertEqual(accepted, changed)
        changed["text"] = "unplanned text"
        with self.assertRaisesRegex(ValueError, "unplanned"):
            runner.checked_request(case, fixture(), changed, CID)


class RunnerBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()
        self.folder.chmod(0o700)
        self.trial = runner.Trial(self.folder, Settings(_env_file=None))
        self.trial.binding = {"protocol_sha256": "a" * 64, "grant_sha256": "b" * 64}
        self.trial.options = {"fixture_binding": {"fixture": "c" * 64}}
        self.trial.identity = {"user": {"id": "a" * 32}}
        self.trial.ledger = Mock()
        self.trial.ledger.verify.return_value = {"paused": False, "pending": False, "recovery_required": False}
        self.trial.ledger.summary.return_value = {"paused": True, "pending": False}
        self.trial.fixtures = {case["id"]: fixture() for case in self.trial.cases}
        self.transport = Mock(side_effect=AssertionError("no live client permitted"))
        self.patch_transport = patch.object(runner, "full_acceptance_client", self.transport)
        self.patch_transport.start()
        self.addCleanup(self.patch_transport.stop)

    async def test_unarmed_clients_and_dependency_fail_without_transport_or_operation(self):
        with self.assertRaisesRegex(ValueError, "explicit_start_required"):
            async with self.trial.client_for("prep01"):
                self.fail("must not yield")
        generator = self.trial.qwen_dependency(request(body(self.trial.case())))
        with self.assertRaises(HTTPException) as caught:
            await anext(generator)
        self.assertEqual(caught.exception.status_code, 503)
        self.transport.assert_not_called()
        self.trial.ledger.start_operation.assert_not_called()

    async def test_nonfrozen_question_revision_conversation_or_surface_pauses_before_dispatch(self):
        self.trial.armed = True
        case = self.trial.case()
        requests = [request({**body(case), "text": "替换一个问题"}),
                    request({**body(case), "expected_revision": 4}),
                    request(body(case), cid="dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
                    request(body(case), surface="dialogue-v2")]
        for raw in requests:
            with self.subTest(path=raw.url.path):
                generator = self.trial.qwen_dependency(raw)
                with self.assertRaises(HTTPException) as caught:
                    await anext(generator)
                self.assertEqual((caught.exception.status_code, caught.exception.detail),
                                 (409, "full_acceptance_unplanned_request"))
        self.assertEqual(self.trial.ledger.pause.call_count, len(requests))
        self.trial.ledger.start_operation.assert_not_called()
        self.transport.assert_not_called()
        self.assertFalse(list(self.folder.glob("*-request.json")))

    async def test_client_error_still_keeps_safe_native_observations_without_raw_exception(self):
        self.trial.armed = True
        observation = FullTransportObservation(records=[{"attempt_id": "prep01-a01", "state": "received",
            "metadata": {"request_model": "text-embedding-v4", "response_sha256": "e" * 64}}])
        exact_client = object()
        @asynccontextmanager
        async def controlled(**_kwargs):
            yield exact_client, observation
        with patch.object(runner, "full_acceptance_client", controlled):
            with self.assertRaisesRegex(RuntimeError, "DO_NOT_LOG_PRIVATE"):
                async with self.trial.client_for("prep01") as pair:
                    self.assertIs(pair[0], exact_client)
                    raise RuntimeError("DO_NOT_LOG_PRIVATE original prompt body")
        self.assertEqual(self.trial.observations["prep01"], observation.records)
        saved = json.loads((self.folder / "prep01-native.json").read_bytes())
        self.assertEqual(saved["records"], observation.records)
        self.assertIs(saved["body_exception"], True)
        self.assertNotIn("DO_NOT_LOG_PRIVATE", json.dumps(saved))
        self.assertNotIn("original prompt", json.dumps(saved))
        self.transport.assert_not_called()

    async def test_verify_rejects_paused_and_recovery_required_without_sending(self):
        with patch.object(runner, "verify", return_value={"verified": True}) as verify:
            for state in ({"paused": True, "pending": False, "recovery_required": False},
                          {"paused": False, "pending": True, "recovery_required": True},
                          {"paused": False, "pending": False, "recovery_required": True}):
                self.trial.ledger.verify.return_value = state
                with self.subTest(state=state), self.assertRaises(ValueError):
                    self.trial.verify()
            self.assertEqual(verify.call_count, 3)
        self.transport.assert_not_called()

    async def test_own_pending_sibling_does_not_make_a_new_operation_or_disable_parallel_send_checks(self):
        self.trial.ledger.verify.return_value = {"paused": False, "pending": True, "recovery_required": False}
        with patch.object(runner, "verify", return_value={"verified": True}):
            self.trial.verify()
        self.trial.ledger.start_operation.assert_not_called()
        self.transport.assert_not_called()

    async def test_valid_dependency_preserves_observation_and_event_on_body_error(self):
        self.trial.armed = True
        case = self.trial.case()
        observation = FullTransportObservation(records=[{"attempt_id": "f01-a01", "state": "failed",
            "metadata": {"error_code": "upstream"}}])
        exact_client = object()
        @asynccontextmanager
        async def controlled(**_kwargs):
            yield exact_client, observation
        with patch.object(runner, "full_acceptance_client", controlled):
            generator = self.trial.qwen_dependency(request(body(case)))
            self.assertIs(await anext(generator), exact_client)
            with self.assertRaisesRegex(RuntimeError, "PRIVATE_BODY_ERROR"):
                await generator.athrow(RuntimeError("PRIVATE_BODY_ERROR raw prompt"))
        self.trial.ledger.start_operation.assert_called_once()
        self.assertTrue(self.trial.events[case["id"]].is_set())
        stored = json.loads((self.folder / f"{case['id']}-native.json").read_bytes())
        self.assertEqual(stored["records"], observation.records)
        self.assertNotIn("PRIVATE_BODY_ERROR", json.dumps(stored))
        self.assertTrue((self.folder / f"{case['id']}-request.json").exists())
        self.transport.assert_not_called()

    def marker(self, **changes):
        marker = {"execute": True, "protocol_sha256": self.trial.binding["protocol_sha256"],
                  "grant_sha256": self.trial.binding["grant_sha256"]}
        marker.update(changes)
        path = self.trial.folder / "execute-once.json"
        path.write_text(json.dumps(marker))
        path.chmod(0o600)
        return path

    async def test_start_marker_wrong_grant_is_rejected_and_never_arms(self):
        self.marker(grant_sha256="f" * 64)
        with patch.object(self.trial, "verify") as verify:
            with self.assertRaisesRegex(ValueError, "start_marker_mismatch"):
                await asyncio.wait_for(runner.wait_start(self.trial), 1)
            verify.assert_not_called()
        self.assertFalse(self.trial.armed)
        self.assertFalse((self.folder / "execution-started.json").exists())
        self.transport.assert_not_called()

    async def test_start_marker_group_readable_is_rejected_before_arming(self):
        self.marker().chmod(0o640)
        with patch.object(self.trial, "verify"):
            with self.assertRaises(ValueError):
                await asyncio.wait_for(runner.wait_start(self.trial), 1)
        self.assertFalse(self.trial.armed)
        self.assertFalse((self.folder / "execution-started.json").exists())
        self.transport.assert_not_called()

    async def test_start_marker_must_be_exact_0600_not_readonly_or_executable(self):
        for mode in (0o400, 0o700, 0o4600):
            folder = self.folder / f"mode-{mode:o}"
            folder.mkdir(mode=0o700)
            original = self.trial.folder
            self.trial.folder = folder
            try:
                self.marker().chmod(mode)
                with patch.object(self.trial, "verify"), self.assertRaises(ValueError):
                    await asyncio.wait_for(runner.wait_start(self.trial), 1)
                self.assertFalse(self.trial.armed)
                self.assertFalse((folder / "execution-started.json").exists())
            finally:
                self.trial.folder = original
        self.transport.assert_not_called()

    async def test_start_marker_integer_true_is_not_an_explicit_boolean_confirmation(self):
        self.marker(execute=1)
        with patch.object(self.trial, "verify"), self.assertRaises(ValueError):
            await asyncio.wait_for(runner.wait_start(self.trial), 1)
        self.assertFalse(self.trial.armed)
        self.transport.assert_not_called()

    async def test_start_marker_too_large_or_wrong_owner_is_rejected_without_transport(self):
        path = self.marker()
        original = os.geteuid()
        with patch.object(runner.os, "geteuid", return_value=original + 1), self.assertRaises(ValueError):
            runner.read_private_document(path)
        path.write_bytes(b" " * (64 * 1024 + 1))
        path.chmod(0o600)
        with patch.object(self.trial, "verify"), self.assertRaises(ValueError):
            await asyncio.wait_for(runner.wait_start(self.trial), 1)
        self.assertFalse(self.trial.armed)
        self.transport.assert_not_called()

    async def test_start_marker_symlink_and_hardlink_are_rejected_before_arming(self):
        path = self.marker()
        target = self.folder / "other.json"
        path.rename(target)
        path.symlink_to(target)
        with patch.object(self.trial, "verify"):
            with self.assertRaises(ValueError):
                await asyncio.wait_for(runner.wait_start(self.trial), 1)
        path.unlink()
        os.link(target, path)
        # Preserve the first refusal's ready file; wait_start is one-shot and
        # must not overwrite it merely to attempt another unsafe marker.
        (self.folder / "ready.json").unlink()
        with patch.object(self.trial, "verify"):
            with self.assertRaises(ValueError):
                await asyncio.wait_for(runner.wait_start(self.trial), 1)
        self.assertFalse(self.trial.armed)
        self.transport.assert_not_called()

    async def test_valid_private_start_marker_only_arms_after_freeze_verify(self):
        self.marker()
        with patch.object(self.trial, "verify") as verify:
            await asyncio.wait_for(runner.wait_start(self.trial), 1)
        verify.assert_called_once_with()
        self.assertTrue(self.trial.armed)
        self.assertTrue((self.folder / "execution-started.json").exists())
        self.transport.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""Offline capture policies and isolated real ASGI/MySQL; no external sends."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from tools.echomind_travel_controlled import ControlledError, canonical, initialize
from tools.echomind_travel_trial_capture import (
    M01, MODEL, _CaptureUpstream, _TOOLS, _fixture, _output, _payload_summary, capture_initial_request,
)


def payload() -> dict:
    return {"model": MODEL, "max_tokens": 900, "temperature": 0.1, "stream": False,
            "enable_thinking": False, "parallel_tool_calls": False, "tool_choice": "auto",
            "messages": [{"role": "system", "content": "synthetic trusted policy"},
                         {"role": "user", "content": M01}],
            "tools": [{"type": "function", "function": {"name": name, "description": "synthetic schema",
                       "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}
                      for name in sorted(_TOOLS)]}


class CapturePolicyTests(unittest.TestCase):
    def test_summary_has_hashes_parameters_but_no_body_or_review(self):
        value = _payload_summary(canonical(payload()))
        self.assertEqual(value["parameters"]["model"], MODEL)
        self.assertEqual(value["tool_count"], len(_TOOLS))
        for key in ("payload_sha256", "system_sha256", "messages_sha256", "tool_schema_sha256"):
            self.assertRegex(value[key], r"^[0-9a-f]{64}$")
        self.assertNotIn(M01, canonical(value).decode())
        self.assertNotIn("synthetic trusted policy", canonical(value).decode())
        self.assertNotIn("messages", value)

    def test_parameter_change_extra_field_and_invalid_json_rejected(self):
        for key, changed in (("model", "another"), ("max_tokens", True), ("temperature", 1),
                             ("stream", True), ("parallel_tool_calls", 0), ("enable_thinking", True),
                             ("tool_choice", "required"), ("question_id", "m01")):
            with self.subTest(key=key):
                value = payload() | {key: changed}
                with self.assertRaisesRegex(ControlledError, "payload_invalid"):
                    _payload_summary(canonical(value))
        for raw in (b"{", b"", b"{}", b" " * 48_001):
            with self.assertRaisesRegex(ControlledError, "payload_invalid"):
                _payload_summary(raw)

    def test_unknown_duplicate_and_missing_tools_rejected(self):
        for mode in ("unknown", "duplicate", "missing", "extra_field"):
            value = payload()
            if mode == "unknown":
                value["tools"][0]["function"]["name"] = "arbitrary_tool"
            elif mode == "duplicate":
                value["tools"][0] = deepcopy(value["tools"][1])
            elif mode == "missing":
                value["tools"].pop()
            else:
                value["tools"][0]["function"]["answer"] = "HIDDEN_REVIEW"
            with self.subTest(mode=mode), self.assertRaisesRegex(ControlledError, "payload_invalid"):
                _payload_summary(canonical(value))

    def test_other_problem_and_feedback_cannot_be_captured_as_m01(self):
        for message in ({"role": "user", "content": "先考虑上海"},
                        {"role": "tool", "tool_call_id": "untrusted", "content": "result"}):
            value = payload()
            value["messages"][-1] = message
            with self.assertRaisesRegex(ControlledError, "payload_invalid"):
                _payload_summary(canonical(value))

    def test_wrong_origin_path_method_never_reaches_capture(self):
        for method, url in (("GET", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"),
                            ("POST", "https://example.com/compatible-mode/v1/chat/completions"),
                            ("POST", "https://restapi.amap.com/v5/place/text")):
            upstream = _CaptureUpstream()
            with self.subTest(url=url), self.assertRaisesRegex(ControlledError, "unplanned"):
                asyncio.run(upstream.handle(httpx.Request(method, url, content=canonical(payload()))))
            self.assertIsNone(upstream.summary)

    def test_single_mock_receive_returns_rejection_and_repeat_is_rejected(self):
        async def run():
            upstream = _CaptureUpstream()
            request = httpx.Request("POST", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                                   content=canonical(payload()))
            first = await upstream.handle(request)
            self.assertEqual(first.status_code, 503)
            self.assertEqual(upstream.attempts, 1)
            with self.assertRaisesRegex(ControlledError, "unplanned"):
                await upstream.handle(request)
        asyncio.run(run())

    def test_output_is_exclusive_private_and_refuses_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            target = parent / "capture.json"
            descriptor = _output(target)
            import os
            os.close(descriptor)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            with self.assertRaisesRegex(ControlledError, "must_be_new"):
                _output(target)
            link = parent / "alias.json"
            link.symlink_to(target)
            with self.assertRaisesRegex(ControlledError, "private_external"):
                _output(link)

    def test_world_readable_parent_rejected_before_output_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            parent.chmod(0o755)
            target = parent / "capture.json"
            with self.assertRaisesRegex(ControlledError, "private_external"):
                _output(target)
            self.assertFalse(target.exists())

    def test_other_database_is_rejected_before_any_query(self):
        from sqlalchemy import create_engine
        engine = create_engine("sqlite:///:memory:")
        try:
            with self.assertRaisesRegex(ControlledError, "independent_empty_mysql"):
                _fixture(engine, Path("/not/read/anchor"))
        finally:
            engine.dispose()


class CaptureActualMySQLTests(unittest.TestCase):
    def setUp(self):
        if any(importlib.util.find_spec(name) is None for name in ("jsonschema", "prometheus_client")):
            self.skipTest("requires explicit echomind optional dependencies")
        from tests.mysql_support import temporary_mysql
        self.engine, cleanup = temporary_mysql()
        self.addCleanup(cleanup)
        self.parent = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="echo-capture-"))).resolve()
        self.anchor = self.parent / "usage.identity"
        self.anchor.write_text(uuid4().hex + "\n", encoding="ascii")
        self.anchor.chmod(0o600)
        initialize(self.engine, self.anchor)

    def test_actual_cookie_csrf_cas_builder_exact_client_and_failed_receipt(self):
        target = self.parent / "capture.json"
        from core.llm_utils import strict_json
        from tools.echomind_travel_trial_transport import _payload
        from tools.echomind_travel_trial_capture import _payload_summary

        def checked_summary(raw):
            # Check the complete real 16-tool builder packet against the new
            # hook's bounded/no-reference contract, not merely a one-tool fake.
            _payload(strict_json(raw))
            return _payload_summary(raw)

        with patch("tools.echomind_travel_trial_capture._payload_summary", side_effect=checked_summary):
            summary = capture_initial_request(target, engine=self.engine, anchor=self.anchor)
        self.assertEqual(json.loads(target.read_text()), summary)
        self.assertEqual(summary["mode"], "controlled_capture")
        self.assertEqual(summary["operation_id"], "m01")
        self.assertEqual(summary["asgi_status"], 502)
        self.assertEqual(summary["program_status"], "failed")
        self.assertEqual(summary["captured_request_count"], 1)
        self.assertEqual(summary["upstream_sends"], 0)
        self.assertEqual(summary["real_model_calls"], 0)
        self.assertEqual(summary["real_amap_calls"], 0)
        self.assertFalse(summary["full_live_payload_frozen"])
        self.assertEqual(summary["product_mock_usage_attempts"], 1)
        self.assertEqual(summary["parameters"]["max_tokens"], 900)
        self.assertEqual(summary["request"]["tool_count"], 16)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertEqual(summary["binding"]["initial_revision"], 0)
        raw = target.read_text()
        for secret in (M01, "controlled-not-real", "csrf_token", "authorization", "database_url", "Bearer"):
            self.assertNotIn(secret, raw)
        # A repeat neither overwrites evidence nor reuses the populated fixture.
        before = target.read_bytes()
        with self.assertRaisesRegex(ControlledError, "must_be_new"):
            capture_initial_request(target, engine=self.engine, anchor=self.anchor)
        self.assertEqual(target.read_bytes(), before)
        other = self.parent / "another-capture.json"
        with self.assertRaisesRegex(ControlledError, "independent_empty_mysql"):
            capture_initial_request(other, engine=self.engine, anchor=self.anchor)
        self.assertEqual(json.loads(other.read_text())["state"], "capture_failed_preserve_fixture")

"""Historical original-loop control, not a current-candidate quality assertion.

Responses below reproduce only the saved valid selection sequence. They are
controlled envelopes, not new model outputs or byte-for-byte native replays.
The actual paid outputs and their independent scores remain outside Git.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

if any(importlib.util.find_spec(name) is None for name in ("jsonschema", "prometheus_client")):
    raise unittest.SkipTest("requires explicit optional candidate dependencies")

from fastapi.testclient import TestClient
from tests.mysql_support import temporary_mysql
from tools.echomind_travel_controlled import (
    ControlledUpstream, controlled_app, initialize, native_text, native_tool,
)
from tools.echomind_travel_live_runner import NativeSession
from echomind_core.travel_engine import _TravelAgent
from echomind_core.upstream.agents.agent_orchestrator import BaseAgent


@patch.object(_TravelAgent, "_call_llm", BaseAgent._call_llm)
class HistoricalClarificationTerminationDiagnosisTests(unittest.TestCase):
    # Preserve the old failure/control assertions explicitly against the
    # source-preserved loop. test_echomind_travel_terminal covers the repaired
    # candidate. No old real output, score or paused journal is rewritten.
    def setUp(self) -> None:
        self.engine, cleanup = temporary_mysql()
        self.addCleanup(cleanup)
        folder = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="travel-stop-diagnosis-"))).resolve()
        anchor = folder / "usage.identity"
        anchor.write_text(uuid4().hex + "\n", encoding="ascii")
        anchor.chmod(0o600)
        initialize(self.engine, anchor)
        self.upstream = ControlledUpstream()
        app, _ = self.enterContext(controlled_app(self.engine, anchor, origin="http://127.0.0.1:3000", upstream=self.upstream))
        client = self.enterContext(TestClient(app))
        self.runner = NativeSession(client, self.engine, origin="http://127.0.0.1:3000")

    def test_original_three_valid_clarifications_are_lost_after_bounded_loop(self) -> None:
        self.upstream.responses = []
        for number, reason in enumerate(("destination_missing", "date_missing", "travelers_missing"), 1):
            response = native_tool("travel_clarify", {"reason": reason})
            # Native call IDs are distinct across rounds in the actual outputs;
            # the generic one-response fixture otherwise reuses its constant ID.
            response["choices"][0]["message"]["tool_calls"][0]["id"] = f"diagnostic_call_{number}"
            self.upstream.responses.append(response)
        result = self.runner.submit("m01")
        self.assertEqual(result["http_status"], 502)
        self.assertEqual(result["response"]["detail"]["code"], "assistant_agent_failed")
        self.assertEqual(result["readback"]["last_turn"]["state"], "failed")
        self.assertEqual(result["task_state"]["task"]["model_calls"], 3)
        self.assertEqual(result["task_state"]["task"]["tool_calls"], 3)
        self.assertEqual(len(self.upstream.events), 3)  # No fourth send or hidden retry.
        self.assertIsNone(result["proposal"]["action"])
        self.assertEqual(result["readback"]["messages"][-1]["role"], "user")
        self.assertIsNone(result["readback"]["requirements"]["destination_id"])
        self.assertIsNone(result["readback"]["confirmed_revision"])

    def test_original_success_depends_on_followup_model_text_stop(self) -> None:
        self.upstream.responses = [native_tool("travel_clarify", {"reason": "destination_missing"}), native_text()]
        result = self.runner.submit("m01")
        self.assertEqual(result["http_status"], 200)
        self.assertEqual(result["readback"]["last_turn"]["state"], "succeeded")
        self.assertEqual(result["task_state"]["task"]["model_calls"], 2)
        self.assertEqual(result["task_state"]["task"]["tool_calls"], 1)
        self.assertEqual(len(self.upstream.events), 2)
        self.assertEqual(result["readback"]["messages"][-1]["role"], "assistant")
        self.assertNotIn("CONTROLLED_FREE_PROSE", result["readback"]["messages"][-1]["text"])
        self.assertIsNone(result["readback"]["confirmed_revision"])


if __name__ == "__main__":
    unittest.main()

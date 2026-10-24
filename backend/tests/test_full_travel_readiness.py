"""No-network contract probes report blockers, not a fake product pass."""
from __future__ import annotations

from contextlib import ExitStack, redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

if importlib.util.find_spec("jsonschema") is None:
    raise unittest.SkipTest("Full budget probe requires the existing echomind extra")

from tools import full_travel_readiness as readiness


class FullTravelReadinessTests(unittest.TestCase):
    def setUp(self):
        self.frontend = readiness.WaitInspection(readiness.FRONTEND_SOURCE, "a" * 64, 65_000)
        self.proxy = readiness.WaitInspection(readiness.PROXY_SOURCE, "b" * 64, 75_000)

    def test_current_runtime_is_not_registered_without_reading_configuration(self):
        from core.config import Settings
        from typing import get_args
        with patch.object(Settings, "__init__", side_effect=AssertionError("do not discover settings")):
            value = readiness.configuration_probe()
        allowed = list(get_args(Settings.model_fields["assistant_runtime"].annotation))
        accepted = readiness.PROPOSED_RUNTIME in allowed
        self.assertEqual(value["registered_values"], allowed)
        self.assertEqual(value["contract_accepts_proposed_runtime"], accepted)
        self.assertEqual(value["proposed_runtime_is_existing_configuration"], accepted)
        self.assertEqual(value["status"], "registered-admission-unverified" if accepted else "blocked")
        self.assertIn("not-executed", value["admission_check"])
        self.assertIn("alone does not prove", value["limitation"])

    def test_actual_projector_accepts_each_read_but_rejects_the_pair(self):
        value = readiness.browser_projection_probe()
        self.assertEqual(value["individual_read_projections_valid"], [True, True])
        self.assertFalse(value["pair_projection_valid"])
        self.assertEqual(value["safe_error_code"], "dialogue_receipt_invalid")
        self.assertEqual(value["status"], "blocked")
        self.assertFalse(value["browser_execution_verified"])

    def test_real_reservations_are_not_relabelled_as_real_dispatches(self):
        value = readiness.reservation_projection_probe()
        self.assertEqual(value["status"], "blocked")
        self.assertEqual(value["count_semantics"], "conservative_reservations_not_actual_dispatches")
        self.assertEqual(value["trace_case"], "aggregate-reservations-without-per-step-events-not-a-live-turn")
        self.assertEqual([row["reservation_counts"] for row in value["tiers"]], [
            {"model_calls": 6, "tool_calls": 8}, {"model_calls": 10, "tool_calls": 12},
            {"model_calls": 16, "tool_calls": 16}])
        self.assertTrue(all(row["displayed_from_current_trace"] == {"model_calls": 3, "tool_calls": 4}
                            for row in value["tiers"]))
        self.assertTrue(all(row["reservation_display_matches"] is False for row in value["tiers"]))
        self.assertEqual([row["task_view_accepts_full_counts"] for row in value["tiers"]], [True, False, False])

    def test_source_inspected_waits_do_not_cover_full_deadline(self):
        value = readiness.wait_probe(self.frontend, self.proxy)
        self.assertEqual(value["status"], "blocked")
        self.assertFalse(value["strict_wait_order_covers_task"])
        self.assertEqual(value["frontend"]["sha256"], "a" * 64)
        self.assertEqual(value["proxy"]["sha256"], "b" * 64)
        self.assertFalse(value["browser_or_proxy_runtime_verified"])

    def test_equal_deadline_or_proxy_before_frontend_is_not_sufficient(self):
        for front, proxy in ((90_000, 100_000), (95_000, 95_000), (100_000, 95_000)):
            with self.subTest(front=front, proxy=proxy):
                left = readiness.WaitInspection(readiness.FRONTEND_SOURCE, "a" * 64, front)
                right = readiness.WaitInspection(readiness.PROXY_SOURCE, "b" * 64, proxy)
                self.assertEqual(readiness.wait_probe(left, right)["status"], "blocked")

    def test_source_capacity_not_a_claim_of_live_runtime_success(self):
        left = readiness.WaitInspection(readiness.FRONTEND_SOURCE, "a" * 64, 100_000)
        right = readiness.WaitInspection(readiness.PROXY_SOURCE, "b" * 64, 110_000)
        value = readiness.wait_probe(left, right)
        self.assertEqual(value["status"], "ready")
        self.assertFalse(value["browser_or_proxy_runtime_verified"])
        self.assertIn("source", value["reason"])

    def test_explicit_source_hash_matches_actual_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / readiness.FRONTEND_SOURCE
            source.parent.mkdir(parents=True)
            source.write_bytes(b"// controlled source, no live measurement\n")
            value = readiness.inspect_wait_source(root, readiness.FRONTEND_SOURCE, 65_000)
            self.assertEqual(value.sha256, hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(value.milliseconds, 65_000)

    def test_source_input_links_roles_and_invalid_metadata_are_rejected(self):
        with self.assertRaises(readiness.ProbeError):
            readiness.wait_probe(self.proxy, self.frontend)
        for milliseconds in (True, 0, -1, 300_001, "65000"):
            with self.subTest(milliseconds=milliseconds), self.assertRaises(readiness.ProbeError):
                readiness.WaitInspection(readiness.FRONTEND_SOURCE, "a" * 64, milliseconds).validate()
        for path, digest in (("/tmp/private", "a" * 64), (readiness.FRONTEND_SOURCE, "bad")):
            with self.subTest(path=path), self.assertRaises(readiness.ProbeError):
                readiness.WaitInspection(path, digest, 65_000).validate()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / readiness.FRONTEND_SOURCE
            source.parent.mkdir(parents=True)
            target = root / "unrelated"
            target.write_bytes(b"not the allowed source")
            source.symlink_to(target)
            with self.assertRaises(readiness.ProbeError):
                readiness.inspect_wait_source(root, readiness.FRONTEND_SOURCE, 65_000)

    def test_complete_probe_is_blocked_and_never_uses_network_database_or_browser(self):
        import sqlalchemy
        with ExitStack() as stack:
            stack.enter_context(patch.object(socket, "create_connection", side_effect=AssertionError("no network")))
            stack.enter_context(patch.object(socket.socket, "connect", side_effect=AssertionError("no network")))
            stack.enter_context(patch.object(sqlalchemy.engine.Engine, "connect", side_effect=AssertionError("no database")))
            value = readiness.probe(self.frontend, self.proxy, surface="legacy")
        self.assertEqual(value["status"], "blocked")
        self.assertTrue(all(row["status"] == "blocked" for row in value["checks"][1:]))
        self.assertEqual(value["real_model_calls"], 0)
        self.assertEqual(value["business_external_calls"], 0)
        self.assertEqual(value["database_operations"], 0)
        self.assertEqual(value["browser_operations"], 0)
        serialized = json.dumps(value)
        self.assertNotIn("password", serialized)
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("input_value", serialized)

    def test_new_surface_executes_new_contracts_without_reinterpreting_old_limits(self):
        value = readiness.probe(self.frontend, self.proxy)
        self.assertEqual(value["target_surface"], "dialogue-v3")
        self.assertEqual(value["status"], "blocked")  # no admission/browser execution in this probe
        projection = value["checks"][1]
        self.assertEqual(projection["status"], "ready")
        self.assertTrue(projection["pair_projection_valid"])
        self.assertFalse(projection["legacy_compatibility"]["pair_projection_valid"])
        self.assertFalse(projection["browser_execution_verified"])
        for tier in value["checks"][2]["tiers"]:
            self.assertEqual(tier["status"], "ready")
            self.assertEqual(tier["projected_reservation_counts"], tier["reservation_counts"])
            self.assertEqual(tier["dispatch_status"], "unknown")
            self.assertFalse(tier["legacy_compatibility"]["reservation_display_matches"])

    def test_cli_exits_blocked_not_success_when_contracts_are_blocked(self):
        output = io.StringIO()
        with redirect_stdout(output):
            status = readiness.main(["--frontend-wait-ms", "65000", "--proxy-wait-ms", "75000"])
        self.assertEqual(status, 2)
        value = json.loads(output.getvalue())
        self.assertEqual(value["status"], "blocked")
        root = Path(__file__).resolve().parents[2]
        for key, path in (("frontend", readiness.FRONTEND_SOURCE), ("proxy", readiness.PROXY_SOURCE)):
            self.assertEqual(value["checks"][-1][key]["sha256"], hashlib.sha256((root / path).read_bytes()).hexdigest())

    def test_bad_cli_value_returns_only_safe_code(self):
        output = io.StringIO()
        with redirect_stdout(output):
            status = readiness.main(["--frontend-wait-ms", "0", "--proxy-wait-ms", "75000"])
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(output.getvalue()), {"schema_version": readiness.VERSION,
            "status": "blocked", "code": "wait_inspection_invalid"})


if __name__ == "__main__":
    unittest.main()

"""Real private files/process locks, synthetic metadata, and zero providers."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import fcntl
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from tools.echomind_travel_trial_ledger import Ledger, LedgerError, MAX_BYTES, MAX_LINE, _canonical


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def binding() -> dict:
    return {"plan_protocol_sha256": digest(b"frozen-plan"),
            "execution_sha256": digest(b"controlled-execution"),
            "prior_ledger_hashes": {"old_closed": digest(b"never-reuse")}}


def _competing_claim(path: str, start, result) -> None:
    start.wait(5)
    try:
        value = Ledger(path).claim("m01", digest(b"payload"), binding_sha256=digest(b"workspace-request"))
    except LedgerError:
        value = "blocked"
    result.put(value)


class TravelTrialLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        self.path = self.directory / "controlled.jsonl"
        self.ledger = Ledger.create(self.path, binding=binding())
        self.bound = digest(b"workspace-request")

    def claim(self, op: str = "m01", payload: bytes = b"initial") -> str:
        return self.ledger.claim(op, digest(payload), binding_sha256=self.bound)

    def test_creation_is_private_exclusive_and_not_real_authorization(self) -> None:
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        result = self.ledger.verify(binding=binding())
        self.assertEqual((result["mode"], result["real_authorized_calls"], result["real_calls"]), ("controlled", 0, 0))
        self.assertEqual((result["limit"], result["controlled_attempts"]), (21, 0))
        original = self.path.read_bytes()
        with self.assertRaisesRegex(LedgerError, "already_exists"):
            Ledger.create(self.path, binding=binding())
        self.assertEqual(original, self.path.read_bytes())
        with self.assertRaisesRegex(LedgerError, "live_admission_not_implemented"):
            Ledger.create(self.directory / "live.jsonl", binding=binding(), mode="live")
        self.assertFalse((self.directory / "live.jsonl").exists())

    def test_durable_claim_is_seen_before_any_sender_and_restart_does_not_refund(self) -> None:
        self.ledger.start_operation("m01", self.bound)
        attempt = self.claim()
        reloaded = Ledger(self.path)
        result = reloaded.summary()
        self.assertEqual(result["controlled_attempts"], 1)
        self.assertTrue(result["pending"])
        for action in (lambda: reloaded.start_operation("m02", self.bound),
                       lambda: reloaded.claim("m01", digest(b"feedback"), binding_sha256=self.bound),
                       lambda: reloaded.seal_operation("m01")):
            with self.assertRaises(LedgerError):
                action()
        reloaded.finish(attempt, "received", {"status_code": 200, "input_tokens": None})
        self.assertEqual(reloaded.summary()["controlled_attempts"], 1)

    def test_three_sends_share_one_operation_and_all_seven_never_exceed_21(self) -> None:
        for number in range(1, 8):
            op = f"m{number:02}"
            self.ledger.start_operation(op, self.bound)
            for index in range(1, 4):
                attempt = self.claim(op, f"payload-{number}-{index}".encode())
                self.assertEqual(attempt, f"{op}-a{index:02}")
                self.ledger.finish(attempt, "received")
            with self.assertRaises(LedgerError):
                self.claim(op, b"fourth-forbidden")
            self.ledger.seal_operation(op)
        result = Ledger(self.path).summary()
        self.assertEqual(result["controlled_attempts"], 21)
        self.assertEqual(result["counts"]["received"], 21)
        self.assertEqual(result["real_calls"], 0)
        for number in range(1, 8):
            with self.assertRaises(LedgerError):
                self.ledger.start_operation(f"m{number:02}", self.bound)

    def test_lower_total_limit_is_hard_and_cannot_be_raised_after_restart(self) -> None:
        path = self.directory / "one.jsonl"
        limited = Ledger.create(path, binding=binding(), limit=1)
        limited.start_operation("m01", self.bound)
        attempt = limited.claim("m01", digest(b"a"), binding_sha256=self.bound)
        limited.finish(attempt, "received")
        limited.seal_operation("m01")
        reopened = Ledger(path)
        reopened.start_operation("m02", self.bound)
        with self.assertRaisesRegex(LedgerError, "budget_exhausted"):
            reopened.claim("m02", digest(b"b"), binding_sha256=self.bound)
        with self.assertRaises(LedgerError):
            Ledger.create(path, binding=binding(), limit=21)

    def test_failed_cancelled_unknown_pause_all_operations_without_refund(self) -> None:
        for state in ("failed", "cancelled", "unknown"):
            path = self.directory / f"{state}.jsonl"
            ledger = Ledger.create(path, binding=binding())
            ledger.start_operation("m01", self.bound)
            attempt = ledger.claim("m01", digest(b"p"), binding_sha256=self.bound)
            ledger.finish(attempt, state, {"error_code": "controlled_failure"})
            reopened = Ledger(path)
            summary = reopened.verify()
            self.assertTrue(summary["paused"])
            self.assertEqual(summary["controlled_attempts"], 1)
            self.assertEqual(summary["counts"][state], 1)
            for action in (lambda: reopened.finish(attempt, "received"),
                           lambda: reopened.start_operation("m02", self.bound),
                           lambda: reopened.claim("m01", digest(b"b"), binding_sha256=self.bound),
                           lambda: reopened.seal_operation("m01")):
                with self.subTest(state=state), self.assertRaises(LedgerError):
                    action()

    def test_explicit_execution_pause_is_durable_without_any_claim(self) -> None:
        self.ledger.start_operation("m01", self.bound)
        original = self.path.read_bytes()
        self.ledger.pause()
        paused = self.path.read_bytes()
        self.assertTrue(paused.startswith(original))
        reopened = Ledger(self.path)
        result = reopened.verify()
        self.assertTrue(result["paused"])
        self.assertEqual((result["controlled_attempts"], result["real_calls"]), (0, 0))
        reopened.pause()
        self.assertEqual(paused, self.path.read_bytes())
        for action in (lambda: reopened.start_operation("m02", self.bound),
                       lambda: reopened.claim("m01", digest(b"payload"), binding_sha256=self.bound),
                       lambda: reopened.seal_operation("m01")):
            with self.assertRaisesRegex(LedgerError, "^ledger_paused$"):
                action()

    def test_execution_pause_never_refunds_or_resolves_pending_attempt(self) -> None:
        self.ledger.start_operation("m01", self.bound)
        self.claim()
        self.ledger.pause()
        result = Ledger(self.path).summary()
        self.assertTrue(result["paused"])
        self.assertTrue(result["pending"])
        self.assertEqual(result["controlled_attempts"], 1)
        with self.assertRaises(TypeError):
            self.ledger.pause("PRIVATE_REASON")

    def test_paused_pending_attempt_can_finish_without_clearing_the_stop(self) -> None:
        self.ledger.start_operation("m01", self.bound)
        attempt = self.claim()
        self.ledger.pause()
        Ledger(self.path).finish(attempt, "received", {"status_code": 200})
        reopened = Ledger(self.path)
        result = reopened.summary()
        self.assertTrue(result["paused"])
        self.assertFalse(result["pending"])
        self.assertEqual((result["controlled_attempts"], result["counts"]["received"]), (1, 1))
        for action in (lambda: reopened.start_operation("m02", self.bound),
                       lambda: reopened.claim("m01", digest(b"feedback"), binding_sha256=self.bound),
                       lambda: reopened.seal_operation("m01")):
            with self.assertRaisesRegex(LedgerError, "^ledger_paused$"):
                action()
        with self.assertRaisesRegex(LedgerError, "outcome_not_pending"):
            reopened.finish(attempt, "received")

    def test_operation_cannot_reopen_even_with_different_identity(self) -> None:
        self.ledger.start_operation("m01", self.bound)
        attempt = self.claim()
        self.ledger.finish(attempt, "received")
        self.ledger.seal_operation("m01")
        for identity in (self.bound, digest(b"other-workspace-request")):
            with self.assertRaises(LedgerError):
                Ledger(self.path).start_operation("m01", identity)

    def test_fixed_operation_order_and_repeated_wire_payload_cannot_be_retried(self) -> None:
        with self.assertRaisesRegex(LedgerError, "operation_not_startable"):
            self.ledger.start_operation("m07", self.bound)
        self.ledger.start_operation("m01", self.bound)
        attempt = self.claim()
        self.ledger.finish(attempt, "received")
        with self.assertRaisesRegex(LedgerError, "payload_already_attempted"):
            Ledger(self.path).claim("m01", digest(b"initial"), binding_sha256=self.bound)
        next_attempt = self.claim(payload=b"actual-feedback")
        self.assertEqual(next_attempt, "m01-a02")

    def test_request_and_execution_binding_changes_fail_without_writing(self) -> None:
        self.ledger.start_operation("m01", self.bound)
        original = self.path.read_bytes()
        with self.assertRaisesRegex(LedgerError, "binding_changed"):
            self.ledger.claim("m01", digest(b"payload"), binding_sha256=digest(b"new-workspace"))
        for key in ("plan_protocol_sha256", "execution_sha256"):
            changed = binding()
            changed[key] = digest(b"other")
            with self.assertRaisesRegex(LedgerError, "execution_binding_changed"):
                self.ledger.verify(binding=changed)
        self.assertEqual(self.path.read_bytes(), original)

    def test_semantic_failure_is_not_an_execution_failure_or_new_attempt(self) -> None:
        self.ledger.start_operation("m01", self.bound)
        attempt = self.claim()
        self.ledger.finish(attempt, "received", {"status_code": 200, "native_tool_calls": 0,
                                                "response_sha256": digest(b"bad-schema")})
        self.ledger.seal_operation("m01")
        self.assertFalse(self.ledger.summary()["paused"])
        self.ledger.start_operation("m02", self.bound)
        self.assertEqual(self.ledger.summary()["controlled_attempts"], 1)

    def test_append_prefix_is_unchanged_and_verification_is_read_only(self) -> None:
        before = self.path.read_bytes()
        self.ledger.start_operation("m01", self.bound)
        self.assertTrue(self.path.read_bytes().startswith(before))
        self.claim()
        now = self.path.read_bytes()
        self.ledger.verify()
        self.assertEqual(now, self.path.read_bytes())
        self.assertEqual(self.ledger.summary()["ledger_sha256"], digest(now))

    def test_competing_threads_only_one_pending_claim_succeeds(self) -> None:
        self.ledger.start_operation("m01", self.bound)
        def claim_once(_: int) -> str:
            try:
                return Ledger(self.path).claim("m01", digest(b"same"), binding_sha256=self.bound)
            except LedgerError:
                return "blocked"
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(claim_once, range(16)))
        self.assertEqual(results.count("m01-a01"), 1)
        self.assertEqual(self.ledger.summary()["controlled_attempts"], 1)

    def test_busy_lock_has_bounded_wait_and_does_not_write(self) -> None:
        original = self.path.read_bytes()
        descriptor = os.open(self.path, os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            with self.assertRaisesRegex(LedgerError, "^ledger_lock_busy$"):
                self.ledger.verify()
        finally:
            os.close(descriptor)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.ledger.verify()["controlled_attempts"], 0)

    def test_independent_processes_share_the_same_persistent_lock(self) -> None:
        self.ledger.start_operation("m01", self.bound)
        context = multiprocessing.get_context("spawn")
        start, result = context.Event(), context.Queue()
        self.addCleanup(result.close)
        children = [context.Process(target=_competing_claim, args=(str(self.path), start, result)) for _ in range(3)]
        for child in children:
            child.start()
        start.set()
        outputs = [result.get(timeout=10) for _ in children]
        for child in children:
            child.join(timeout=10)
            if child.is_alive():
                child.terminate()
                child.join(timeout=5)
                self.fail("controlled child did not exit")
            self.assertEqual(child.exitcode, 0)
        self.assertEqual(outputs.count("m01-a01"), 1)
        self.assertEqual(self.ledger.summary()["controlled_attempts"], 1)

    def test_invalid_modes_limits_and_binding_fields_do_not_create_files(self) -> None:
        for limit in (0, 22, True, 21.0):
            with self.assertRaises(LedgerError):
                Ledger.create(self.directory / "invalid.jsonl", binding=binding(), limit=limit)
        for value in ({}, {**binding(), "secret": "PRIVATE"},
                      {**binding(), "prior_ledger_hashes": {}},
                      {**binding(), "prior_ledger_hashes": {"PRIVATE\n": digest(b"x")}}):
            with self.assertRaises(LedgerError) as caught:
                Ledger.create(self.directory / "invalid.jsonl", binding=value)
            self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertFalse((self.directory / "invalid.jsonl").exists())

    def test_unknown_operation_hash_outcome_and_metadata_are_not_logged(self) -> None:
        for operation in ("m00", "m08", "api01", "../m01", None):
            with self.assertRaises(LedgerError):
                self.ledger.start_operation(operation, self.bound)
        self.ledger.start_operation("m01", self.bound)
        attempt = self.claim()
        original = self.path.read_bytes()
        for metadata in ({"raw_response": "PRIVATE"}, {"error_code": "PRIVATE"},
                         {"status_code": True}, {"native_tool_calls": 5},
                         {"upstream_model": "PRIVATE"}, {"response_sha256": "PRIVATE"}):
            with self.assertRaises(LedgerError) as caught:
                self.ledger.finish(attempt, "received", metadata)
            self.assertNotIn("PRIVATE", str(caught.exception))
        for outcome in ("not_sent", "success", True, None):
            with self.assertRaises(LedgerError):
                self.ledger.finish(attempt, outcome)
        self.assertEqual(original, self.path.read_bytes())
        self.assertNotIn(b"PRIVATE", original)

    def test_symlink_hardlink_permissions_parent_and_special_file_are_rejected(self) -> None:
        link = self.directory / "symlink.jsonl"
        link.symlink_to(self.path)
        with self.assertRaises(LedgerError):
            Ledger(link).verify()
        hardlink = self.directory / "hardlink.jsonl"
        os.link(self.path, hardlink)
        try:
            with self.assertRaises(LedgerError):
                self.ledger.verify()
        finally:
            hardlink.unlink()
        self.path.chmod(0o640)
        try:
            with self.assertRaises(LedgerError):
                self.ledger.verify()
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o640)
        finally:
            self.path.chmod(0o600)
        public = self.directory / "public"
        public.mkdir(mode=0o755)
        with self.assertRaises(LedgerError):
            Ledger.create(public / "forbidden.jsonl", binding=binding())
        fifo = self.directory / "fifo"
        os.mkfifo(fifo, 0o600)
        with self.assertRaises(LedgerError):
            Ledger(fifo).verify()

    def test_truncation_corruption_duplicates_and_oversized_files_fail_closed(self) -> None:
        original = self.path.read_bytes()
        changes = (original[:-1], original.replace(b'"controlled"', b'"live"'),
                   b'{"x":1,"x":2}\n', b'NaN\n', b"a" * (MAX_LINE + 1) + b"\n",
                   b"a" * (MAX_BYTES + 1))
        for changed in changes:
            self.path.write_bytes(changed)
            with self.subTest(length=len(changed)), self.assertRaises(LedgerError):
                Ledger(self.path).verify()
        self.path.write_bytes(original)
        self.assertEqual(self.ledger.verify()["controlled_attempts"], 0)

    def test_internally_rehashed_but_invalid_state_is_rejected(self) -> None:
        self.ledger.start_operation("m01", self.bound)
        self.claim()
        rows = [json.loads(line) for line in self.path.read_bytes().splitlines()]
        for mutate in (lambda row: row["body"].update(send_index=2),
                       lambda row: row["body"].update(binding_sha256=digest(b"other")),
                       lambda row: row["body"].update(extra="PRIVATE")):
            changed = copy.deepcopy(rows)
            mutate(changed[-1])
            unsigned = {key: value for key, value in changed[-1].items() if key != "sha256"}
            changed[-1]["sha256"] = digest(_canonical(unsigned))
            self.path.write_bytes(b"".join(_canonical(row) + b"\n" for row in changed))
            with self.assertRaises(LedgerError) as caught:
                Ledger(self.path).verify()
            self.assertNotIn("PRIVATE", str(caught.exception))

    def test_partial_write_and_fsync_failure_never_return_admission(self) -> None:
        self.ledger.start_operation("m01", self.bound)
        real_write = os.write
        first = True
        def partial(descriptor: int, data: bytes) -> int:
            nonlocal first
            if first:
                first = False
                return real_write(descriptor, data[:12])
            raise OSError("PRIVATE")
        with patch("tools.echomind_travel_trial_ledger.os.write", side_effect=partial):
            with self.assertRaisesRegex(LedgerError, "^ledger_storage_failure$"):
                self.claim()
        with self.assertRaises(LedgerError):
            Ledger(self.path).verify()
        path = self.directory / "sync.jsonl"
        ledger = Ledger.create(path, binding=binding())
        ledger.start_operation("m01", self.bound)
        with patch("tools.echomind_travel_trial_ledger.os.fsync", side_effect=OSError("PRIVATE")):
            with self.assertRaisesRegex(LedgerError, "^ledger_storage_failure$"):
                ledger.claim("m01", digest(b"p"), binding_sha256=self.bound)
        result = Ledger(path).summary()
        self.assertEqual(result["controlled_attempts"], 1)
        self.assertTrue(result["paused"])
        with self.assertRaises(LedgerError):
            ledger.verify()

    def test_finish_sync_failure_leaves_a_sticky_stop_and_consumed_attempt(self) -> None:
        self.ledger.start_operation("m01", self.bound)
        attempt = self.claim()
        with patch("tools.echomind_travel_trial_ledger.os.fsync", side_effect=OSError("PRIVATE")):
            with self.assertRaises(LedgerError):
                self.ledger.finish(attempt, "received")
        result = Ledger(self.path).summary()
        self.assertEqual(result["controlled_attempts"], 1)
        self.assertTrue(result["paused"])

    def test_invalid_event_does_not_change_any_prefix(self) -> None:
        self.ledger.start_operation("m01", self.bound)
        original = self.path.read_bytes()
        for action in (lambda: self.ledger.start_operation("m02", self.bound),
                       lambda: self.ledger.finish("m01-a01", "received"),
                       lambda: self.ledger.claim("m01", "bad", binding_sha256=self.bound)):
            with self.assertRaises(LedgerError):
                action()
            self.assertEqual(original, self.path.read_bytes())


if __name__ == "__main__":
    unittest.main()

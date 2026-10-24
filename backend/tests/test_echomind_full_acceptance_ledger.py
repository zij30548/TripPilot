"""Real files/locks and synthetic receipts; no model or business transports."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from tools.echomind_full_acceptance_ledger import (
    FILENAME, LIMIT, PLAN, FullAcceptanceLedger, LedgerError, _canonical,
)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def binding() -> dict:
    return {"grant_sha256": digest(b"new-finite-grant"),
        "protocol_sha256": digest(b"new-protocol"), "code_freeze_sha256": digest(b"new-code"),
        "prior_ledger_hashes": {"original_paused": digest(b"original-byte-prefix")}}


def _process_claim(path: str, bound: str, start, result) -> None:
    start.wait(5)
    try:
        ledger = FullAcceptanceLedger(path)
        ledger.start_operation("prep01", bound)
        result.put(ledger.claim("prep01", "embedding.templates", digest(b"batch"), binding_sha256=bound))
    except LedgerError:
        result.put("blocked")


def _rehash(rows: list[dict]) -> bytes:
    previous = "0" * 64
    for row in rows:
        row["previous"] = previous
        row["sha256"] = digest(_canonical({key: value for key, value in row.items() if key != "sha256"}))
        previous = row["sha256"]
    return b"".join(_canonical(row) + b"\n" for row in rows)


class FullAcceptanceLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.root.chmod(0o700)
        self.path = self.root / FILENAME
        self.ledger = FullAcceptanceLedger.create(self.path, binding=binding())
        self.bound = digest(b"one-owner-version-input")

    def claim(self, op: str, index: int = 0, *, stage: str = "agent") -> str:
        return self.ledger.claim(op, stage, digest(f"{op}-{index}".encode()), binding_sha256=self.bound)

    def complete_prefix(self) -> None:
        for op, _ in PLAN[:4]:
            self.ledger.start_operation(op, self.bound)
            self.ledger.finish(self.claim(op), "received")
            self.ledger.seal_operation(op)

    def test_fixed_plan_private_exclusive_and_old_filename_refused(self) -> None:
        result = self.ledger.verify(binding=binding())
        self.assertEqual((LIMIT, result["limit"], result["reserved_attempts"]), (66, 66, 0))
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(LedgerError, "already_exists"):
            FullAcceptanceLedger.create(self.path, binding=binding())
        self.assertEqual(before, self.path.read_bytes())
        with self.assertRaisesRegex(LedgerError, "canonical_filename"):
            FullAcceptanceLedger(self.root / "echomind-travel-live.jsonl")
        self.assertFalse((self.root / "echomind-travel-live.jsonl").exists())

    def test_operation_order_binding_and_closed_operation_never_reopen(self) -> None:
        for op in ("f01", "m01", "prep02", "index02"):
            with self.subTest(op=op), self.assertRaises(LedgerError):
                self.ledger.start_operation(op, self.bound)
        self.ledger.start_operation("prep01", self.bound)
        with self.assertRaisesRegex(LedgerError, "binding_or_writer_changed"):
            self.ledger.claim("prep01", "embedding", digest(b"body"), binding_sha256=digest(b"new-version"))
        identifier = self.claim("prep01")
        self.ledger.finish(identifier, "received")
        self.ledger.seal_operation("prep01")
        for operation in (lambda: self.ledger.start_operation("prep01", self.bound),
                          lambda: self.claim("prep01"),
                          lambda: self.ledger.finish(identifier, "received"),
                          lambda: self.ledger.seal_operation("prep01")):
            with self.assertRaises(LedgerError):
                operation()
        self.assertEqual(self.ledger.verify()["reserved_attempts"], 1)

    def test_66_upper_bound_all_tiers_and_more_than_old_128_records(self) -> None:
        for op, maximum in PLAN:
            self.ledger.start_operation(op, self.bound)
            for index in range(maximum):
                identifier = self.claim(op, index)
                self.assertEqual(identifier, f"{op}-a{index + 1:02}")
                self.ledger.finish(identifier, "received")
            with self.assertRaises(LedgerError):
                self.claim(op, maximum)
            self.ledger.seal_operation(op)
        result = FullAcceptanceLedger(self.path).verify(binding=binding())
        self.assertEqual((result["reserved_attempts"], result["remaining_attempts"]), (66, 0))
        self.assertEqual(result["counts"]["received"], 66)
        self.assertGreater(result["records"], 128)
        self.assertFalse(result["paused"])
        with self.assertRaises(LedgerError):
            self.ledger.start_operation("f08", self.bound)

    def test_parallel_sibling_claims_share_six_not_six_each(self) -> None:
        self.complete_prefix()
        self.ledger.start_operation("f01", self.bound)
        def compete(index):
            try:
                return self.claim("f01", index, stage=f"specialist.{index}")
            except LedgerError:
                return "blocked"
        with ThreadPoolExecutor(max_workers=12) as workers:
            results = list(workers.map(compete, range(12)))
        accepted = [value for value in results if value != "blocked"]
        self.assertEqual((len(accepted), len(set(accepted))), (6, 6))
        self.assertEqual(self.ledger.verify()["counts"]["pending"], 6)
        with self.assertRaisesRegex(LedgerError, "not_sealable"):
            self.ledger.seal_operation("f01")
        for identifier in reversed(accepted):
            self.ledger.finish(identifier, "received")
        self.ledger.seal_operation("f01")

    def test_same_payload_cannot_retry_same_stage_but_distinct_stages_are_independent(self) -> None:
        self.complete_prefix()
        self.ledger.start_operation("f01", self.bound)
        payload = digest(b"same-query-two-real-stages")
        first = self.ledger.claim("f01", "memory.embedding", payload, binding_sha256=self.bound)
        second = self.ledger.claim("f01", "intent.embedding", payload, binding_sha256=self.bound)
        self.ledger.finish(first, "received")
        self.ledger.finish(second, "received")
        with self.assertRaisesRegex(LedgerError, "already_attempted"):
            self.ledger.claim("f01", "memory.embedding", payload, binding_sha256=self.bound)

    def test_restart_with_pending_or_unsealed_received_requires_audit(self) -> None:
        self.ledger.start_operation("prep01", self.bound)
        identifier = self.claim("prep01")
        for state in ("pending", "received"):
            if state == "received":
                self.ledger.finish(identifier, "received")
            restarted = FullAcceptanceLedger(self.path)
            result = restarted.verify()
            self.assertTrue(result["recovery_required"])
            self.assertEqual(result["reserved_attempts"], 1)
            for operation in (lambda: restarted.claim("prep01", "later", digest(b"next"), binding_sha256=self.bound),
                              lambda: restarted.finish(identifier, "received"),
                              lambda: restarted.seal_operation("prep01"),
                              lambda: restarted.start_operation("prep02", self.bound)):
                with self.assertRaises(LedgerError):
                    operation()
        self.ledger.seal_operation("prep01")
        restarted = FullAcceptanceLedger(self.path)
        self.assertFalse(restarted.verify()["recovery_required"])
        restarted.start_operation("prep02", self.bound)
        self.assertEqual(restarted.claim("prep02", "embedding", digest(b"second"), binding_sha256=self.bound), "prep02-a01")

    def test_two_processes_cannot_both_start_or_send_first_operation(self) -> None:
        context = multiprocessing.get_context("spawn")
        start, result = context.Event(), context.Queue()
        workers = [context.Process(target=_process_claim, args=(str(self.path), self.bound, start, result)) for _ in range(2)]
        for worker in workers:
            worker.start()
        start.set()
        results = [result.get(timeout=10) for _ in workers]
        for worker in workers:
            worker.join(10)
            self.assertEqual(worker.exitcode, 0)
        self.assertEqual(sorted(results), ["blocked", "prep01-a01"])
        result.close()
        self.assertEqual(self.ledger.verify()["reserved_attempts"], 1)
        self.assertTrue(self.ledger.verify()["recovery_required"])

    def test_execution_failure_pauses_but_inflight_sibling_can_record_truthful_completion(self) -> None:
        self.complete_prefix()
        self.ledger.start_operation("f01", self.bound)
        first, second = self.claim("f01", 0), self.claim("f01", 1)
        self.ledger.finish(first, "unknown", {"error_code": "unknown"})
        self.ledger.finish(second, "received", {"status_code": 200})
        result = self.ledger.verify()
        self.assertTrue(result["paused"])
        self.assertEqual((result["counts"]["unknown"], result["reserved_attempts"]), (1, 6))
        for operation in (lambda: self.claim("f01", 2), lambda: self.ledger.seal_operation("f01"),
                          lambda: self.ledger.start_operation("f02", self.bound)):
            with self.assertRaisesRegex(LedgerError, "paused"):
                operation()
        self.assertTrue(FullAcceptanceLedger(self.path).verify()["paused"])

    def test_complete_http_semantic_failure_can_seal_failed_and_continue(self) -> None:
        self.ledger.start_operation("prep01", self.bound)
        self.ledger.finish(self.claim("prep01"), "received", {"status_code": 200,
            "error_code": "structure", "response_sha256": digest(b"invalid-model-structure")})
        self.ledger.seal_operation("prep01", business_outcome="failed")
        self.ledger.start_operation("prep02", self.bound)
        self.assertFalse(self.ledger.verify()["paused"])
        self.assertEqual(self.ledger.verify()["operations"]["prep01"]["business_outcome"], "failed")

    def test_cancel_failed_unknown_no_refund_and_repeated_pause_is_idempotent(self) -> None:
        self.ledger.start_operation("prep01", self.bound)
        self.ledger.finish(self.claim("prep01"), "cancelled", {"error_code": "cancelled"})
        self.ledger.pause("cancelled")
        first = self.path.read_bytes()
        self.ledger.pause("cancelled")
        self.assertEqual(first, self.path.read_bytes())
        self.assertEqual(self.ledger.verify()["remaining_attempts"], 65)
        with self.assertRaises(LedgerError):
            self.ledger.pause("private freeform diagnostics")

    def test_each_nonreceived_outcome_is_sticky_and_unknown_cannot_be_rewritten(self) -> None:
        for outcome in ("failed", "cancelled", "unknown"):
            folder = self.root / outcome
            folder.mkdir(mode=0o700)
            ledger = FullAcceptanceLedger.create(folder / FILENAME, binding=binding())
            ledger.start_operation("prep01", self.bound)
            identifier = ledger.claim("prep01", "embedding", digest(b"fixed"), binding_sha256=self.bound)
            ledger.finish(identifier, outcome)
            self.assertTrue(ledger.summary()["paused"])
            self.assertEqual(ledger.summary()["reserved_attempts"], 1)
            with self.assertRaises(LedgerError):
                ledger.finish(identifier, "received")
            with self.assertRaises(LedgerError):
                ledger.start_operation("prep02", self.bound)

    def test_safe_bounded_metadata_and_unknown_usage_preserved(self) -> None:
        self.ledger.start_operation("prep01", self.bound)
        identifier = self.claim("prep01")
        wanted = {"status_code": 200, "duration_ms": 100, "input_tokens": None,
            "output_tokens": 0, "total_tokens": None, "native_tool_calls": 0,
            "request_model": "text-embedding-v4", "upstream_model": "unknown", "request_id": "req_123-456",
            "response_sha256": digest(b"bounded-response")}
        invalid = [{"authorization": "secret"}, {"input_tokens": True}, {"duration_ms": 180001},
            {"request_model": []}, {"request_model": "arbitrary"}, {"request_id": "private line\nheader"},
            {"upstream_model": "x" * 129}, {"response_sha256": "bad"}, {"error_code": "user input"}]
        for value in invalid:
            with self.subTest(value=list(value)), self.assertRaises(LedgerError):
                self.ledger.finish(identifier, "received", value)
        self.ledger.finish(identifier, "received", wanted)
        wanted["status_code"] = 500
        actual = self.ledger.verify()["attempts"][identifier]["metadata"]
        self.assertEqual(actual["status_code"], 200)
        self.assertIsNone(actual["input_tokens"])

    def test_binding_drift_and_hashchain_json_type_or_extra_field_tamper_rejected(self) -> None:
        changed = binding()
        changed["code_freeze_sha256"] = digest(b"different-code")
        with self.assertRaisesRegex(LedgerError, "binding_changed"):
            self.ledger.verify(binding=changed)
        original = self.path.read_bytes()
        for mutate in (lambda row: row["body"].__setitem__("limit", 66.0),
                       lambda row: row["body"].__setitem__("extra", "untrusted"),
                       lambda row: row.__setitem__("seq", True)):
            rows = [json.loads(line) for line in original.splitlines()]
            mutate(rows[0])
            self.path.write_bytes(_rehash(rows))
            with self.assertRaises(LedgerError):
                FullAcceptanceLedger(self.path).verify()
        self.path.write_bytes(original.replace(b'"mode":"live"', b'"mode":"live","mode":"live"'))
        with self.assertRaises(LedgerError):
            FullAcceptanceLedger(self.path).verify()

    def test_private_parent_file_links_permissions_and_hardlinks_rejected(self) -> None:
        self.path.chmod(0o640)
        with self.assertRaises(LedgerError):
            self.ledger.verify()
        self.path.chmod(0o600)
        linked = self.root / "hardlink"
        os.link(self.path, linked)
        with self.assertRaises(LedgerError):
            self.ledger.verify()
        linked.unlink()
        real = self.root / "original"
        self.path.rename(real)
        self.path.symlink_to(real)
        with self.assertRaises(LedgerError):
            self.ledger.verify()
        self.path.unlink()
        real.rename(self.path)
        self.root.chmod(0o750)
        with self.assertRaises(LedgerError):
            self.ledger.verify()
        self.root.chmod(0o700)

    def test_partial_append_and_uncertain_fsync_preserve_occupied_prefix_never_retry(self) -> None:
        self.ledger.start_operation("prep01", self.bound)
        write = os.write
        calls = 0
        def broken(descriptor, raw):
            nonlocal calls
            calls += 1
            if calls == 1:
                return write(descriptor, raw[:7])
            raise OSError("controlled private error")
        before = self.path.read_bytes()
        with patch("tools.echomind_full_acceptance_ledger.os.write", side_effect=broken):
            with self.assertRaisesRegex(LedgerError, "storage_failure"):
                self.claim("prep01")
        self.assertTrue(self.path.read_bytes().startswith(before))
        self.assertGreater(len(self.path.read_bytes()), len(before))
        with self.assertRaises(LedgerError):
            FullAcceptanceLedger(self.path).verify()

    def test_complete_claim_with_lost_fsync_ack_is_not_available_after_restart(self) -> None:
        self.ledger.start_operation("prep01", self.bound)
        with patch("tools.echomind_full_acceptance_ledger.os.fsync", side_effect=OSError("unknown ack")):
            with self.assertRaisesRegex(LedgerError, "storage_failure"):
                self.claim("prep01")
        reloaded = FullAcceptanceLedger(self.path)
        self.assertEqual(reloaded.verify()["reserved_attempts"], 1)
        self.assertTrue(reloaded.verify()["recovery_required"])
        with self.assertRaises(LedgerError):
            self.claim("prep01")
        with self.assertRaises(LedgerError):
            reloaded.claim("prep01", "retry", digest(b"retry"), binding_sha256=self.bound)


if __name__ == "__main__":
    unittest.main()

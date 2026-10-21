"""Offline continuation safety checks; no credentials, DB or upstream sends."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tests import test_chat_acceptance as fixtures
from tools.chat_acceptance import (AcceptanceStopped, BATCH, CONTINUATION_NAME,
    CONTINUATION_VERSION, IDS, LIMIT, MODEL, ChatAcceptanceLedger, canonical,
    create_continuation, digest, freeze)


class ContinuationTests(unittest.TestCase):
    def setUp(self) -> None:
        fixtures.ChatAcceptanceTests.setUp(self)
        freeze(self.directory, self.protocol, stage_root=self.stage, repository_root=self.repo)
        initial = self.open_original()
        initial.reserve("api01", self.wires["api01"])
        initial.complete("api01", outcome="success")
        self.prefix = (self.directory / "calls.jsonl").read_bytes()
        self.original_protocol = (self.directory / "protocol.json").read_bytes()
        self.path = self.directory / CONTINUATION_NAME
        self.stop = self.stage / ".runtime-20261021-12" / "stop.json"
        self.stop.parent.mkdir(mode=0o700)
        self.stop.write_bytes(b'{"code":"test_session_expired_or_changed"}')
        self.source.write_bytes(b"repaired exact version")
        self.artifact = self.directory / "continuation-requests.json"
        self.artifact.write_bytes(b"controlled frozen request records")
        self.payloads = {identifier: value | {"repaired_schema": "exact scoped difference"}
                         for identifier, value in self.payloads.items()}
        self.wires = {identifier: json.dumps(value, ensure_ascii=False).encode()
                      for identifier, value in self.payloads.items()}
        self.manifest = {"schema_version": CONTINUATION_VERSION, "batch": BATCH,
            "limit": LIMIT, "model": MODEL, "original_protocol_sha256": digest(self.original_protocol),
            "journal_prefix_sha256": digest(self.prefix), "journal_prefix_bytes": len(self.prefix),
            "consumed": 1, "source_commit": "b" * 40,
            "files": {"frozen.py": digest(self.source.read_bytes())},
            "cases": [case | {"payload_sha256": digest(self.wires[case["id"]]),
                                "canonical_payload_sha256": digest(canonical(self.payloads[case["id"]]))}
                      for case in self.protocol["cases"][1:]],
            "artifacts": {str(self.artifact): digest(self.artifact.read_bytes())},
            "prior_runtime_stop_sha256": digest(self.stop.read_bytes()),
            "change_description": "Bound repaired schema and actual authentication contract; no repeat or new allowance."}

    def tearDown(self) -> None:
        fixtures.ChatAcceptanceTests.tearDown(self)

    def open_original(self) -> ChatAcceptanceLedger:
        return ChatAcceptanceLedger(self.directory, stage_root=self.stage, repository_root=self.repo)

    def open_continuation(self) -> ChatAcceptanceLedger:
        return ChatAcceptanceLedger(self.directory, continuation=self.path,
                                    stage_root=self.stage, repository_root=self.repo)

    def create(self, manifest: dict | None = None, path: Path | None = None) -> None:
        create_continuation(self.directory, manifest or self.manifest,
                            continuation=path or self.path, stage_root=self.stage, repository_root=self.repo)

    def test_original_verification_still_rejects_changed_source(self) -> None:
        with self.assertRaisesRegex(AcceptanceStopped, "frozen_file_changed"):
            self.open_original()
        self.assertEqual((self.directory / "calls.jsonl").read_bytes(), self.prefix)
        self.assertFalse(self.path.exists())

    def test_same_twelve_call_ledger_only_eleven_remaining_and_restart(self) -> None:
        old = self.old.read_bytes()
        self.create()
        identifier = digest(self.path.read_bytes())
        self.assertEqual(self.open_continuation().verify(), {"batch": BATCH, "consumed": 1,
            "remaining": 11, "paused": False, "continuation_sha256": identifier})
        for ordinal, operation in enumerate(IDS[1:], 2):
            ledger = self.open_continuation()
            row = ledger.reserve(operation, self.wires[operation])
            self.assertEqual(row["ordinal"], ordinal)
            self.assertEqual(row["continuation_sha256"], identifier)
            ledger.complete(operation, outcome="semantic_failure" if operation == "api02" else "success")
            with self.assertRaises(AcceptanceStopped):
                self.open_continuation().reserve(operation, self.wires[operation])
        final = self.open_continuation().verify()
        self.assertEqual((final["consumed"], final["remaining"]), (12, 0))
        with self.assertRaises(AcceptanceStopped):
            self.open_continuation().reserve("chrome04", self.wires["chrome04"])
        journal = (self.directory / "calls.jsonl").read_bytes()
        self.assertEqual(journal[:len(self.prefix)], self.prefix)
        self.assertEqual(len(journal.splitlines()), 25)
        self.assertEqual((self.directory / "protocol.json").read_bytes(), self.original_protocol)
        self.assertEqual(self.old.read_bytes(), old)

    def test_old_operation_wrong_order_and_original_payload_cannot_send(self) -> None:
        self.create()
        ledger = self.open_continuation()
        for operation, raw in (("api01", self.wires["api01"]), ("api03", self.wires["api03"]),
                               ("api02", b"{}")):
            with self.assertRaises(AcceptanceStopped):
                ledger.reserve(operation, raw)
        self.assertEqual(ledger.verify()["consumed"], 1)

    def test_continuation_cannot_be_reinitialized_or_moved(self) -> None:
        with self.assertRaisesRegex(AcceptanceStopped, "fixed_continuation_path_required"):
            self.create(path=self.directory / "another-version.json")
        self.create()
        manifest = self.path.read_bytes()
        claim = (self.open_continuation().registry / CONTINUATION_NAME).read_bytes()
        with self.assertRaises(AcceptanceStopped):
            self.create()
        self.assertEqual(self.path.read_bytes(), manifest)
        self.assertEqual((self.open_continuation().registry / CONTINUATION_NAME).read_bytes(), claim)

    def test_no_new_allowance_operation_or_omitted_original_source(self) -> None:
        changes = [{"limit": 13}, {"consumed": 0}, {"consumed": 2},
                   {"original_protocol_sha256": "a" * 64}, {"files": {}},
                   {"cases": self.manifest["cases"][:-1]}, {"unknown": True}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(AcceptanceStopped):
                self.create(self.manifest | change)
            self.assertFalse(self.path.exists())
        for field, value in (("id", "api01"), ("feature", "places"), ("channel", "chrome")):
            cases = [self.manifest["cases"][0] | {field: value}, *self.manifest["cases"][1:]]
            with self.subTest(field=field), self.assertRaises(AcceptanceStopped):
                self.create(self.manifest | {"cases": cases})
            self.assertFalse(self.path.exists())

    def test_prefix_change_and_uncertain_or_pausing_attempt_prevent_creation(self) -> None:
        for raw in (self.prefix[:-1], self.prefix + b" ", self.prefix.replace(b"success", b"timeout")):
            (self.directory / "calls.jsonl").write_bytes(raw)
            with self.assertRaises(AcceptanceStopped):
                self.create()
            self.assertFalse(self.path.exists())
        (self.directory / "calls.jsonl").write_bytes(self.prefix)
        with self.assertRaisesRegex(AcceptanceStopped, "continuation_requires_original_completed_prefix"):
            self.create(self.manifest | {"journal_prefix_sha256": "0" * 64})

    def test_valid_original_unknown_or_execution_failure_is_never_resumable(self) -> None:
        rows = [json.loads(line) for line in self.prefix.splitlines()]
        variants = [rows[:2], [*rows[:2], rows[2] | {"outcome": "timeout", "pause": True}]]
        for variant in variants:
            raw = b"".join(canonical(row) + b"\n" for row in variant)
            (self.directory / "calls.jsonl").write_bytes(raw)
            manifest = self.manifest | {"journal_prefix_sha256": digest(raw), "journal_prefix_bytes": len(raw)}
            with self.subTest(last=variant[-1]["event"]), self.assertRaisesRegex(
                    AcceptanceStopped, "continuation_requires_original_completed_prefix"):
                self.create(manifest)
            self.assertFalse(self.path.exists())

    def test_original_prefix_remains_exact_after_continuation_and_cannot_be_reformatted(self) -> None:
        self.create()
        ledger = self.open_continuation()
        ledger.reserve("api02", self.wires["api02"])
        ledger.complete("api02", outcome="success")
        journal = (self.directory / "calls.jsonl").read_bytes()
        first, rest = journal.split(b"\n", 1)
        (self.directory / "calls.jsonl").write_bytes(first + b" \n" + rest)
        with self.assertRaisesRegex(AcceptanceStopped, "original_journal_prefix_changed"):
            self.open_continuation()

    def test_new_drift_stop_and_historical_ledger_stop_before_reservation(self) -> None:
        self.create()
        ledger = self.open_continuation()
        for path in (self.source, self.old, self.artifact, self.path, self.stop,
                     self.directory / "protocol.json"):
            original = path.read_bytes()
            path.write_bytes(original + b" ")
            with self.subTest(path=path.name), self.assertRaises(AcceptanceStopped):
                ledger.reserve("api02", self.wires["api02"])
            path.write_bytes(original)
        self.assertEqual(ledger.verify()["consumed"], 1)

    def test_new_uncertain_or_cancelled_attempt_occupies_and_never_auto_resumes(self) -> None:
        self.create()
        ledger = self.open_continuation()
        ledger.reserve("api02", self.wires["api02"])
        self.assertEqual(self.open_continuation().verify()["remaining"], 10)
        self.assertTrue(self.open_continuation().verify()["paused"])
        with self.assertRaises(AcceptanceStopped):
            self.open_continuation().reserve("api03", self.wires["api03"])
        ledger.complete("api02", outcome="cancelled")
        self.assertTrue(self.open_continuation().verify()["paused"])
        with self.assertRaises(AcceptanceStopped):
            self.open_continuation().reserve("api03", self.wires["api03"])

    def test_concurrent_next_operation_only_one_can_commit(self) -> None:
        self.create()
        def send(_: int) -> bool:
            try:
                self.open_continuation().reserve("api02", self.wires["api02"])
                return True
            except AcceptanceStopped:
                return False
        with ThreadPoolExecutor(max_workers=6) as pool:
            self.assertEqual(sum(pool.map(send, range(6))), 1)
        self.assertEqual(self.open_continuation().verify()["consumed"], 2)

    def test_failed_continuation_claim_is_not_removed_or_retried(self) -> None:
        original = __import__("tools.chat_acceptance", fromlist=["_new"])._new
        def fail_registry(path: Path, raw: bytes) -> None:
            if path.parent.name == ".authorized-20261021-12":
                raise OSError("private detail")
            original(path, raw)
        with patch("tools.chat_acceptance._new", side_effect=fail_registry):
            with self.assertRaisesRegex(AcceptanceStopped, "continuation_not_committed_do_not_send"):
                self.create()
        self.assertTrue(self.path.exists())
        with self.assertRaises(AcceptanceStopped):
            self.open_continuation()
        with self.assertRaises(AcceptanceStopped):
            self.create()
        self.assertEqual((self.directory / "calls.jsonl").read_bytes(), self.prefix)


if __name__ == "__main__":
    unittest.main()

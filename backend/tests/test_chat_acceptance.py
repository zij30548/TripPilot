"""Offline only: no config, database, HTTP or paid acceptance entry points."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools.chat_acceptance import (AcceptanceStopped, BATCH, IDS, LIMIT, MODEL, ChatAcceptanceLedger,
    canonical, checked_metadata, digest, freeze, payload_digest, safe_upstream_metadata)


class ChatAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.source = self.repo / "frozen.py"
        self.source.write_text("unchanged implementation")
        self.old = self.root / "old-calls.jsonl"
        self.old.write_bytes(b'{"event":"old-attempt","consumed":20}\n')
        self.stage = self.root / "stage"
        self.directory = self.stage / "batch"
        self.payloads = {identifier: {"model": MODEL, "messages": [{"role": "user", "content": f"controlled-{identifier}"}]}
                         for identifier in IDS}
        self.wires = {identifier: json.dumps(value, ensure_ascii=False).encode() for identifier, value in self.payloads.items()}
        self.protocol = {"schema_version": "chat-acceptance-v1", "batch": BATCH, "limit": LIMIT,
            "model": MODEL, "source_commit": "a" * 40,
            "files": {"frozen.py": digest(self.source.read_bytes())},
            "old_ledgers": {str(self.old): digest(self.old.read_bytes())},
            "cases": [{"id": identifier, "channel": "api" if identifier.startswith("api") else "chrome",
                       "feature": "intent" if index < 4 or identifier == "chrome01" else "places", "payload_hash_kind": "wire_bytes",
                       "payload_sha256": digest(self.wires[identifier]),
                       "canonical_payload_sha256": digest(canonical(self.payloads[identifier]))}
                      for index, identifier in enumerate(IDS)]}

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def initialize(self) -> ChatAcceptanceLedger:
        freeze(self.directory, self.protocol, stage_root=self.stage, repository_root=self.repo)
        return self.reopen()

    def reopen(self) -> ChatAcceptanceLedger:
        return ChatAcceptanceLedger(self.directory, stage_root=self.stage, repository_root=self.repo)

    def test_fixed_twelve_operations_shared_api_chrome_and_restart(self) -> None:
        ledger = self.initialize()
        old_bytes = self.old.read_bytes()
        for ordinal, identifier in enumerate(IDS, 1):
            ledger = self.reopen()
            token = ledger.reserve(identifier, self.wires[identifier])
            self.assertEqual(token["ordinal"], ordinal)
            self.assertEqual(ledger.verify()["consumed"], ordinal)
            ledger.complete(identifier, outcome="success", metadata={"elapsed_ms": 19, "input_tokens": None})
            with self.assertRaises(AcceptanceStopped):
                ledger.reserve(identifier, self.wires[identifier])
        self.assertEqual(ledger.verify(), {"batch": BATCH, "consumed": 12, "remaining": 0, "paused": False})
        self.assertEqual(old_bytes, self.old.read_bytes())
        with self.assertRaises(AcceptanceStopped):
            ledger.reserve("chrome05", self.wires["chrome04"])

    def test_reinitialization_new_directory_cannot_get_new_allowance(self) -> None:
        self.initialize()
        original = (self.directory / "protocol.json").read_bytes()
        for directory in (self.directory, self.stage / "another-batch"):
            with self.assertRaises((AcceptanceStopped, FileExistsError)):
                freeze(directory, self.protocol, stage_root=self.stage, repository_root=self.repo)
        self.assertEqual((self.directory / "protocol.json").read_bytes(), original)
        self.assertFalse((self.stage / "another-batch").exists())

    def test_only_next_fixed_operation_and_exact_wire_and_canonical_payload(self) -> None:
        ledger = self.initialize()
        for operation, payload, model in [("api02", self.wires["api02"], MODEL),
                ("api01", b"{}", MODEL), ("api01", self.wires["api01"], "qwen3.7-plus")]:
            with self.assertRaises(AcceptanceStopped):
                ledger.reserve(operation, payload, model=model)
        self.assertEqual(ledger.verify()["consumed"], 0)
        ledger.reserve("api01", self.wires["api01"])
        with self.assertRaises(AcceptanceStopped):
            ledger.reserve("api02", self.wires["api02"])

    def test_frozen_version_old_ledger_and_protocol_changes_stop_before_send(self) -> None:
        ledger = self.initialize()
        source, old = self.source.read_bytes(), self.old.read_bytes()
        for path in (self.source, self.old, self.directory / "protocol.json"):
            original = path.read_bytes()
            path.write_bytes(original + b" ")
            with self.assertRaises(AcceptanceStopped):
                ledger.reserve("api01", self.wires["api01"])
            path.write_bytes(original)
        self.assertEqual(self.source.read_bytes(), source)
        self.assertEqual(self.old.read_bytes(), old)
        self.assertEqual(ledger.verify()["consumed"], 0)

    def test_uncertain_dispatch_or_sent_cancel_keeps_count_and_stops(self) -> None:
        ledger = self.initialize()
        ledger.reserve("api01", self.wires["api01"])
        self.assertEqual(self.reopen().verify()["remaining"], 11)
        self.assertTrue(self.reopen().verify()["paused"])
        with self.assertRaises(AcceptanceStopped):
            self.reopen().reserve("api02", self.wires["api02"])
        ledger.complete("api01", outcome="cancelled", metadata={"elapsed_ms": 22})
        self.assertTrue(self.reopen().verify()["paused"])
        with self.assertRaises(AcceptanceStopped):
            self.reopen().reserve("api02", self.wires["api02"])

    def test_structure_failure_is_retained_counted_and_allows_next_fixed_case(self) -> None:
        ledger = self.initialize()
        ledger.reserve("api01", self.wires["api01"])
        ledger.complete("api01", outcome="structure", metadata={"response_sha256": "b" * 64})
        self.reopen().reserve("api02", self.wires["api02"])
        rows = [json.loads(line) for line in (self.directory / "calls.jsonl").read_bytes().splitlines()]
        self.assertEqual(rows[2]["outcome"], "structure")
        self.assertEqual(self.reopen().verify()["consumed"], 2)

    def test_concurrent_reservation_only_one_can_send(self) -> None:
        self.initialize()
        def reserve() -> bool:
            try:
                self.reopen().reserve("api01", self.wires["api01"])
                return True
            except AcceptanceStopped:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(lambda _: reserve(), range(8))), 1)
        self.assertEqual(self.reopen().verify()["consumed"], 1)

    def test_independent_processes_share_one_flock_reservation(self) -> None:
        self.initialize()
        code = """
import sys
from pathlib import Path
from tools.chat_acceptance import AcceptanceStopped, ChatAcceptanceLedger
try:
    ledger = ChatAcceptanceLedger(Path(sys.argv[1]), stage_root=Path(sys.argv[2]), repository_root=Path(sys.argv[3]))
    ledger.reserve('api01', bytes.fromhex(sys.argv[4]))
    print('reserved')
except AcceptanceStopped:
    print('blocked')
"""
        processes = [subprocess.Popen([sys.executable, "-B", "-c", code, str(self.directory), str(self.stage),
            str(self.repo), self.wires["api01"].hex()], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for _ in range(4)]
        results = [process.communicate(timeout=10) for process in processes]
        self.assertTrue(all(process.returncode == 0 for process in processes))
        self.assertEqual(sum(output == b"reserved\n" for output, _ in results), 1)
        self.assertTrue(all(not error for _, error in results))
        self.assertEqual(self.reopen().verify()["consumed"], 1)

    def test_deleting_or_truncating_journal_cannot_restore_allowance(self) -> None:
        ledger = self.initialize()
        init = (self.directory / "calls.jsonl").read_bytes()
        ledger.reserve("api01", self.wires["api01"])
        ledger.complete("api01", outcome="success")
        full = (self.directory / "calls.jsonl").read_bytes()
        for raw in (init, b"", full[:-1], init + b'{"event":"attempt"}'):
            (self.directory / "calls.jsonl").write_bytes(raw)
            with self.assertRaises(AcceptanceStopped):
                self.reopen()
        (self.directory / "calls.jsonl").unlink()
        with self.assertRaises(AcceptanceStopped):
            self.reopen()

    def test_orphan_marker_after_failed_persistence_blocks_not_refunds(self) -> None:
        ledger = self.initialize()
        with patch("tools.chat_acceptance.os.fsync", side_effect=OSError("private unsafe detail")):
            with self.assertRaises(AcceptanceStopped) as caught:
                ledger.reserve("api01", self.wires["api01"])
        self.assertNotIn("private", str(caught.exception))
        self.assertTrue((ledger.registry / "api01.json").exists())
        with self.assertRaises(AcceptanceStopped):
            self.reopen()

    def test_completion_whitelist_no_prompt_headers_or_arbitrary_error(self) -> None:
        ledger = self.initialize()
        ledger.reserve("api01", self.wires["api01"])
        for metadata in ({"Authorization": "secret"}, {"content": "raw model"}, {"upstream_model": "<script>"},
                         {"elapsed_ms": 1.5}, {"input_tokens": True}, {"response_sha256": "bad"}):
            with self.assertRaises(AcceptanceStopped):
                ledger.complete("api01", outcome="success", metadata=metadata)
        ledger.complete("api01", outcome="success", metadata={"upstream_request_id": "chatcmpl-123",
            "upstream_model": None, "input_tokens": 12, "output_tokens": 3, "http_status": 200})
        journal = (self.directory / "calls.jsonl").read_text()
        for private in ("Authorization", "raw model", "controlled-api01", "messages"):
            self.assertNotIn(private, journal)
        with self.assertRaises(AcceptanceStopped):
            ledger.complete("api01", outcome="success")

    def test_safe_upstream_metadata_missing_is_unknown_and_text_not_copied(self) -> None:
        self.assertEqual(safe_upstream_metadata(None), {"upstream_request_id": None, "upstream_model": None,
            "input_tokens": None, "output_tokens": None, "total_tokens": None})
        value = safe_upstream_metadata({"id": "x\nsecret", "model": "qwen3.7-flash", "content": "never",
            "usage": {"prompt_tokens": 5, "completion_tokens": -1, "total_tokens": True}})
        self.assertIsNone(value["upstream_request_id"])
        self.assertEqual(value["input_tokens"], 5)
        self.assertIsNone(value["output_tokens"])
        self.assertIsNone(value["total_tokens"])
        self.assertNotIn("never", str(value))
        checked_metadata(value)

    def test_private_file_symlink_hardlink_and_permissions_rejected(self) -> None:
        ledger = self.initialize()
        lock = ledger.registry / "gate.lock"
        lock.unlink()
        lock.symlink_to(self.old)
        with self.assertRaises(AcceptanceStopped):
            self.reopen()
        lock.unlink()
        os.link(self.directory / "calls.jsonl", lock)
        with self.assertRaises(AcceptanceStopped):
            self.reopen()
        lock.unlink()
        lock.write_bytes(b"")
        os.chmod(lock, 0o600)
        os.chmod(self.directory / "protocol.json", 0o644)
        with self.assertRaises(AcceptanceStopped):
            self.reopen()

    def test_limits_and_invalid_protocol_never_create_authorization(self) -> None:
        for changes in ({"limit": 13}, {"model": "other"}, {"cases": self.protocol["cases"][:-1]},
                        {"unexpected": "field"}, {"files": {"../escape": "a" * 64}}):
            with self.assertRaises(AcceptanceStopped):
                freeze(self.directory, self.protocol | changes, stage_root=self.stage, repository_root=self.repo)
        self.assertFalse(self.stage.exists())
        with self.assertRaises(AcceptanceStopped):
            payload_digest(b"x" * 48_001, "wire_bytes")
        with self.assertRaises(AcceptanceStopped):
            payload_digest(self.payloads["api01"], "wire_bytes")
        self.assertNotEqual(payload_digest(self.wires["api01"], "wire_bytes"),
                            payload_digest(self.payloads["api01"], "canonical_json"))


if __name__ == "__main__":
    unittest.main()

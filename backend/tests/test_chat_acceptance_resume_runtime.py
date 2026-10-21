"""Offline repaired-version wiring: synthetic local files, never paid sends."""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from app.schemas.chat import Intent
from tools import chat_acceptance_runtime as runtime
from tools.chat_acceptance import (AcceptanceStopped, BATCH, IDS, MODEL,
    ChatAcceptanceLedger, _new, canonical, create_continuation, digest, freeze)


TODAY = datetime(2026, 10, 21, 8, tzinfo=timezone.utc)
PATTERNS = (("accommodation_hint", "anyOf", 0), ("required_place_hints", "items"), ("preferences", "items"))


class FrozenClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return TODAY.astimezone(tz) if tz else TODAY.replace(tzinfo=None)


def change_patterns(payload: dict, value: str) -> None:
    properties = payload["response_format"]["json_schema"]["schema"]["$defs"]["Intent"]["properties"]
    for path in PATTERNS:
        node = properties
        for key in path:
            node = node[key]
        node["pattern"] = value


def controlled_bindings(evaluation: dict) -> dict:
    bindings = {"cases": {}}
    for case in evaluation["cases"]:
        identifier = str(uuid4())
        conversation = {"schema_version": "chat-session-v1", "id": identifier, "title": "受控离线会话",
            "revision": 0, "requirements": Intent().model_dump(), "confirmed_revision": None,
            "sources": [], "issues": [], "messages": [], "last_turn": None,
            "created_at": "2026-10-21T00:00:00+00:00", "updated_at": "2026-10-21T00:00:00+00:00"}
        places = {"schema_version": "chat-places-v1", "conversation_id": identifier, "intent_revision": 0,
            "revision": 0, "city_id": None, "accommodation": None, "required_places": [], "batch": None,
            "last_operation": None, "clarification": None, "proposed_city_id": None,
            "request_text": None, "stale": False}
        bindings["cases"][case["id"]] = {"conversation": conversation, "places": places}
    bindings["cases"]["chrome"] = deepcopy(bindings["cases"]["chrome01"])
    return bindings


class ResumePreparationTests(unittest.TestCase):
    def setUp(self):
        self.parent = Path(self.enterContext(TemporaryDirectory()))
        self.repo = self.parent / "repo"
        self.repo.mkdir()
        self.source = self.repo / "source.py"
        self.source.write_bytes(b"original controlled source")
        self.extra = self.repo / "new-test.py"
        self.extra.write_bytes(b"controlled new offline check")
        self.old_ledger = self.parent / "historical.jsonl"
        self.old_ledger.write_bytes(b'{"historical_consumed":20}\n')
        self.stage, self.directory = self.parent / "stage", self.parent / "stage/batch"
        self.evaluation = json.loads(runtime.MANIFEST.read_bytes())
        self.bindings = controlled_bindings(self.evaluation)
        _, requests = runtime.frozen_payloads(self.bindings, self.evaluation, now=TODAY)
        self.original_requests = deepcopy(requests)
        for case in self.evaluation["cases"]:
            if case["kind"] == "intent":
                change_patterns(self.original_requests[case["id"]]["payload"], r"\S")
        cases = [{"id": case["id"], "channel": case["channel"],
                  "feature": "intent" if case["kind"] == "intent" else "places",
                  "payload_hash_kind": "wire_bytes",
                  "payload_sha256": digest(runtime.wire(self.original_requests[case["id"]]["payload"])),
                  "canonical_payload_sha256": digest(canonical(self.original_requests[case["id"]]["payload"]))}
                 for case in self.evaluation["cases"]]
        self.protocol = {"schema_version": "chat-acceptance-v1", "batch": BATCH, "limit": 12,
            "model": MODEL, "source_commit": "a" * 40,
            "files": {"source.py": digest(self.source.read_bytes())},
            "old_ledgers": {str(self.old_ledger): digest(self.old_ledger.read_bytes())}, "cases": cases}
        freeze(self.directory, self.protocol, stage_root=self.stage, repository_root=self.repo)
        ledger = self.original_ledger()
        ledger.reserve("api01", runtime.wire(self.original_requests["api01"]["payload"]))
        ledger.complete("api01", outcome="success")
        self.prefix = (self.directory / "calls.jsonl").read_bytes()
        for name, value in (("requests.json", self.original_requests), ("rubric.json", self.evaluation),
                            ("bindings.json", self.bindings), ("protected-before.json", {"controlled": "untouched"})):
            _new(self.directory / name, canonical(value))
        self.runtime_dir = self.stage / ".runtime-20261021-12"
        self.runtime_dir.mkdir(mode=0o700)
        self.stop = self.runtime_dir / "stop.json"
        self.stop_raw = canonical({"code": "test_session_expired_or_changed", "at": "2026-10-21T00:00:00+00:00"})
        _new(self.stop, self.stop_raw)
        self.identity = {"batch": BATCH, "protocol_sha256": digest((self.directory / "protocol.json").read_bytes()),
            "directory": str(self.directory.resolve()), "amap_logical_limit": 6, "amap_physical_limit": 12,
            "artifacts": {name: digest((self.directory / name).read_bytes())
                          for name in ("requests.json", "rubric.json", "bindings.json", "protected-before.json")}}
        _new(self.runtime_dir / "identity.json", canonical(self.identity))
        _new(self.runtime_dir / "gate.lock", b"")
        _new(self.runtime_dir / "amap.jsonl", canonical({"event": "initialized",
             "protocol_sha256": self.identity["protocol_sha256"]}) + b"\n")
        self.source.write_bytes(b"repaired controlled source bytes")
        self.files = {p.name: digest(p.read_bytes()) for p in (self.source, self.extra)}
        self.enterContext(patch.object(runtime, "ROOT", self.repo))
        self.enterContext(patch.object(runtime, "frozen_files", return_value=self.files))
        self.enterContext(patch.object(runtime, "datetime", FrozenClock))
        self.enterContext(patch.object(runtime.subprocess, "check_output", return_value="b" * 40 + "\n"))
        self.enterContext(patch.object(runtime, "create_continuation", side_effect=lambda directory, manifest, **kwargs:
            create_continuation(directory, manifest, stage_root=self.stage, repository_root=self.repo, **kwargs)))
        self.enterContext(patch.object(runtime, "ChatAcceptanceLedger", side_effect=lambda directory, **kwargs:
            ChatAcceptanceLedger(directory, stage_root=self.stage, repository_root=self.repo, **kwargs)))

    def original_ledger(self):
        return ChatAcceptanceLedger(self.directory, stage_root=self.stage, repository_root=self.repo)

    def prepare(self):
        with redirect_stdout(StringIO()):
            runtime.prepare_continuation(self.directory)

    def test_freeze_excludes_consumed_case_preserves_all_eleven_bodies_and_three_patterns_only(self):
        self.prepare()
        manifest = runtime._read_json(self.directory / "continuation-v1.json")
        requests = runtime._read_json(self.directory / "continuation-v1/requests.json")
        self.assertEqual([case["id"] for case in manifest["cases"]], list(IDS[1:]))
        self.assertEqual(set(requests), set(IDS[1:]))
        self.assertNotIn("api01", requests)
        for identifier, current in requests.items():
            original = self.original_requests[identifier]
            self.assertEqual(current["body"], original["body"])
            self.assertEqual(current["path"], original["path"])
            restored = deepcopy(current["payload"])
            if next(c for c in manifest["cases"] if c["id"] == identifier)["feature"] == "intent":
                change_patterns(restored, r"\S")
                self.assertNotEqual(current["payload"], original["payload"])
            self.assertEqual(restored, original["payload"])
        self.assertEqual((self.directory / "calls.jsonl").read_bytes(), self.prefix)
        self.assertEqual(self.stop.read_bytes(), self.stop_raw)

    def test_snapshots_hold_exact_source_bytes_and_manifest_records_every_snapshot(self):
        self.prepare()
        manifest = runtime._read_json(self.directory / "continuation-v1.json")
        for relative, sha in self.files.items():
            target = self.directory / "continuation-v1/sources" / relative
            self.assertEqual(target.read_bytes(), (self.repo / relative).read_bytes())
            self.assertEqual(manifest["artifacts"][str(target.resolve())], sha)
        target = self.directory / "continuation-v1/sources/source.py"
        target.write_bytes(b"changed byte snapshot")
        with self.assertRaisesRegex(AcceptanceStopped, "continuation_artifact_changed"):
            runtime.LiveGate(self.directory, continuation=self.directory / "continuation-v1.json")

    def test_non_pattern_payload_change_stops_before_any_continuation_output(self):
        original = runtime.frozen_payloads
        def bad_payloads(*args, **kwargs):
            cases, requests = original(*args, **kwargs)
            requests["api02"]["payload"]["max_tokens"] = 2048
            return cases, requests
        with patch.object(runtime, "frozen_payloads", side_effect=bad_payloads):
            with self.assertRaisesRegex(AcceptanceStopped, "unexpected_continuation_payload_change"):
                self.prepare()
        self.assertFalse((self.directory / "continuation-v1").exists())
        self.assertEqual((self.directory / "calls.jsonl").read_bytes(), self.prefix)

    def test_midnight_before_freeze_stops_without_new_files_or_count(self):
        tomorrow = datetime(2026, 10, 22, 1, tzinfo=timezone.utc)
        with patch(__name__ + ".TODAY", tomorrow):
            with self.assertRaisesRegex(AcceptanceStopped, "frozen_calendar_changed"):
                self.prepare()
        self.assertFalse((self.directory / "continuation-v1").exists())
        self.assertEqual((self.directory / "calls.jsonl").read_bytes(), self.prefix)

    def test_prior_stop_is_retained_and_new_stop_permanently_blocks_resumed_gate(self):
        self.prepare()
        path = self.directory / "continuation-v1.json"
        gate = runtime.LiveGate(self.directory, continuation=path)
        self.assertEqual(gate.verify()["remaining"], 11)
        self.assertEqual(gate.stop_path, self.runtime_dir / "continuation-stop-v1.json")
        gate.stop("controlled_execution_failure")
        first = gate.stop_path.read_bytes()
        gate.stop("second_failure_must_not_overwrite")
        self.assertEqual(gate.stop_path.read_bytes(), first)
        self.assertEqual(self.stop.read_bytes(), self.stop_raw)
        with self.assertRaisesRegex(AcceptanceStopped, "acceptance_paused_or_exhausted"):
            runtime.LiveGate(self.directory, continuation=path)

    def test_midnight_after_freeze_stops_even_dateless_places_payload_before_send(self):
        self.prepare()
        gate = runtime.LiveGate(self.directory, continuation=self.directory / "continuation-v1.json")
        tomorrow = datetime(2026, 10, 22, 1, tzinfo=timezone.utc)
        with patch(__name__ + ".TODAY", tomorrow):
            with self.assertRaisesRegex(AcceptanceStopped, "frozen_calendar_changed"):
                gate.verify()
        self.assertEqual(gate.ledger.verify()["consumed"], 1)
        self.assertEqual(self.stop.read_bytes(), self.stop_raw)


class ContinuationSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_session_file_does_not_read_or_replace_old_private_session(self):
        parent = Path(self.enterContext(TemporaryDirectory()))
        directory, private = parent / "batch", parent / "private"
        directory.mkdir(mode=0o700)
        private.mkdir(mode=0o700)
        _new(private / "account-a.json", canonical({"username": "controlled-account", "password": "controlled-only"}))
        original = b"original opaque session must not be read"
        _new(private / "api-session.json", original)
        observed = []
        def handler(request):
            observed.append(request.url.path)
            if request.url.path == "/auth/session":
                return httpx.Response(200, json={"authenticated": False, "csrf_token": "csrf-before"})
            if request.url.path == "/auth/login":
                return httpx.Response(200, json={"authenticated": True, "csrf_token": "csrf-after",
                    "session_id": "controlled-session", "user": {"username": "controlled-account"}},
                    headers={"set-cookie": "controlled_cookie=controlled_session; Path=/; HttpOnly"})
            return httpx.Response(200, json={"controlled": "no upstream"})
        real_client = httpx.AsyncClient
        gate = SimpleNamespace(requests={"api02": {"path": "/controlled-operation", "body": {"controlled": True}}},
            verify=lambda: {"consumed": 1}, ledger=SimpleNamespace(verify=lambda: {"consumed": 1}),
            stop=lambda code: self.fail("unexpected stop: " + code))
        with patch.object(runtime, "LiveGate", return_value=gate) as ctor, patch.object(runtime.httpx,
                "AsyncClient", side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs)), redirect_stdout(StringIO()):
            continuation = directory / "continuation-v1.json"
            await runtime.api_run(directory, private, "api02", continuation=continuation)
        ctor.assert_called_once_with(directory, continuation=continuation)
        self.assertEqual((private / "api-session.json").read_bytes(), original)
        new = runtime._read_json(private / "api-session-continuation-v1.json")
        self.assertEqual(new["session_id"], "controlled-session")
        self.assertEqual(observed, ["/auth/session", "/auth/login", "/controlled-operation"])


if __name__ == "__main__":
    unittest.main()

"""Offline freeze and durable send gate for one authorized chat acceptance.

This module has no app/config/network imports. Production startup never imports
it. The dedicated acceptance transport calls reserve immediately before its
single physical send and complete afterwards; API and Chrome share this gate.
Deleting evidence is not a reset: the independent authorization registry must
also match. An orphan reservation is deliberately never retried automatically.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Iterator


ROOT = Path(__file__).resolve().parents[2]
STAGE_ROOT = ROOT.parent / "trippilot-evidence" / "chat-acceptance"
BATCH = "chat1-chat2-real-20261021-authorized12-v1"
MODEL = "qwen3.7-flash-2026-07-15"
LIMIT = 12
IDS = tuple([f"api{i:02}" for i in range(1, 9)] + [f"chrome{i:02}" for i in range(1, 5)])
MAX_PROTOCOL = 512 * 1024
MAX_JOURNAL = 256 * 1024
MAX_PAYLOAD = 48_000
CONTINUATION_VERSION = "chat-acceptance-continuation-v1"
CONTINUATION_NAME = "continuation-v1.json"
PAUSE = frozenset({"configuration", "authentication", "quota", "model_unavailable", "rate_limit",
                   "timeout", "network", "cancelled", "execution", "ledger"})
OUTCOMES = PAUSE | {"success", "structure", "references", "empty", "truncated", "refused", "semantic_failure"}
HASH = re.compile(r"[0-9a-f]{64}")
SAFE_ID = re.compile(r"[A-Za-z0-9_.:/-]{1,160}")


class AcceptanceStopped(Exception):
    """Fixed public code only; never wrap raw filesystem/upstream exceptions."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _json(raw: bytes) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict:
        result: dict = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    def invalid(_: str) -> None:
        raise ValueError()
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise AcceptanceStopped("invalid_evidence_json") from None


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _safe_directory(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise AcceptanceStopped("private_directory_required")


def _read(path: Path, limit: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or info.st_size > limit):
            raise AcceptanceStopped("private_evidence_required")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise AcceptanceStopped("evidence_limit")
        return raw
    finally:
        os.close(fd)


def _new(path: Path, raw: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)
    _sync_directory(path.parent)


def _hash_file(path: Path) -> str:
    # Frozen repository/source inputs need not be private; they may not be links.
    if path.is_symlink() or not path.is_file():
        raise AcceptanceStopped("frozen_file_changed")
    return digest(path.read_bytes())


def _validate_protocol(protocol: object) -> dict:
    keys = {"schema_version", "batch", "limit", "model", "source_commit", "files", "old_ledgers", "cases"}
    if (not isinstance(protocol, dict) or set(protocol) != keys
            or protocol["schema_version"] != "chat-acceptance-v1" or protocol["batch"] != BATCH
            or type(protocol["limit"]) is not int or protocol["limit"] != LIMIT or protocol["model"] != MODEL
            or not isinstance(protocol["source_commit"], str)
            or re.fullmatch(r"[0-9a-f]{40}", protocol["source_commit"]) is None):
        raise AcceptanceStopped("invalid_protocol")
    for field in ("files", "old_ledgers"):
        values = protocol[field]
        if not isinstance(values, dict) or not values or len(values) > 1000:
            raise AcceptanceStopped("invalid_protocol")
        for path, sha in values.items():
            p = Path(path) if isinstance(path, str) else Path("..")
            if (not isinstance(path, str) or len(path) > 1000 or ".." in p.parts
                    or p.is_absolute() != (field == "old_ledgers")
                    or not isinstance(sha, str) or HASH.fullmatch(sha) is None):
                raise AcceptanceStopped("invalid_protocol")
    cases = protocol["cases"]
    if not isinstance(cases, list) or len(cases) != LIMIT:
        raise AcceptanceStopped("invalid_protocol")
    for index, (identifier, case) in enumerate(zip(IDS, cases, strict=True)):
        base_keys = {"id", "channel", "feature", "payload_sha256", "payload_hash_kind"}
        if (not isinstance(case, dict) or set(case) not in (base_keys, base_keys | {"canonical_payload_sha256"})
                or case["id"] != identifier or case["channel"] != ("api" if identifier.startswith("api") else "chrome")
                or case["feature"] != ("intent" if index < 4 or identifier == "chrome01" else "places")
                or case["payload_hash_kind"] not in {"canonical_json", "wire_bytes"}
                or not isinstance(case["payload_sha256"], str) or HASH.fullmatch(case["payload_sha256"]) is None):
            raise AcceptanceStopped("invalid_protocol")
        if "canonical_payload_sha256" in case and (not isinstance(case["canonical_payload_sha256"], str)
                or HASH.fullmatch(case["canonical_payload_sha256"]) is None):
            raise AcceptanceStopped("invalid_protocol")
    return protocol


def payload_digest(payload: dict | bytes, kind: str) -> str:
    if (kind == "canonical_json" and not isinstance(payload, dict)
            or kind == "wire_bytes" and not isinstance(payload, bytes)):
        raise AcceptanceStopped("payload_mismatch")
    raw = canonical(payload) if kind == "canonical_json" else payload
    if not isinstance(raw, bytes) or kind not in {"canonical_json", "wire_bytes"} or len(raw) > MAX_PAYLOAD:
        raise AcceptanceStopped("payload_mismatch")
    return digest(raw)


def freeze(directory: Path, protocol: dict, *, stage_root: Path = STAGE_ROOT, repository_root: Path = ROOT) -> None:
    """Exclusive one-time initialization. No allowance/reset/force argument."""
    protocol = _validate_protocol(protocol)
    if (not directory.is_absolute() or directory.parent.resolve() != stage_root.resolve()
            or directory.name.startswith(".") or directory.exists()):
        raise AcceptanceStopped("new_batch_directory_required")
    stage_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    _safe_directory(stage_root)
    registry = stage_root / ".authorized-20261021-12"
    # mkdir is the cross-process one-time authorization claim. A partial freeze
    # leaves it behind and fails closed; it is never auto-deleted on failure.
    registry.mkdir(mode=0o700)
    _sync_directory(stage_root)
    directory.mkdir(mode=0o700)
    _sync_directory(stage_root)
    raw = canonical(protocol)
    if len(raw) > MAX_PROTOCOL:
        raise AcceptanceStopped("evidence_limit")
    _new(directory / "protocol.json", raw)
    identity = {"batch": BATCH, "limit": LIMIT, "directory": str(directory.resolve()), "protocol_sha256": digest(raw)}
    _new(registry / "identity.json", canonical(identity))
    _new(registry / "gate.lock", b"")
    init = {"event": "initialized", "batch": BATCH, "protocol_sha256": digest(raw)}
    _new(directory / "calls.jsonl", canonical(init) + b"\n")
    ChatAcceptanceLedger(directory, stage_root=stage_root, repository_root=repository_root).verify()


def _validate_continuation(value: object, protocol: dict, protocol_hash: str) -> dict:
    fields = {"schema_version", "batch", "limit", "model", "original_protocol_sha256", "journal_prefix_sha256",
              "journal_prefix_bytes", "consumed", "source_commit", "files", "cases", "artifacts",
              "prior_runtime_stop_sha256", "change_description"}
    if (not isinstance(value, dict) or set(value) != fields or value["schema_version"] != CONTINUATION_VERSION
            or value["batch"] != BATCH or value["limit"] != LIMIT or type(value["limit"]) is not int
            or value["model"] != MODEL or value["original_protocol_sha256"] != protocol_hash
            or type(value["consumed"]) is not int or value["consumed"] != 1
            or type(value["journal_prefix_bytes"]) is not int or not 1 <= value["journal_prefix_bytes"] <= MAX_JOURNAL
            or not isinstance(value["source_commit"], str) or re.fullmatch(r"[0-9a-f]{40}", value["source_commit"]) is None
            or not isinstance(value["change_description"], str) or not 1 <= len(value["change_description"]) <= 1000
            or any(ord(c) < 32 or ord(c) == 127 for c in value["change_description"])):
        raise AcceptanceStopped("invalid_continuation")
    for key in ("journal_prefix_sha256", "prior_runtime_stop_sha256"):
        if not isinstance(value[key], str) or HASH.fullmatch(value[key]) is None:
            raise AcceptanceStopped("invalid_continuation")
    # Reuse the original strict case validator; only the eleven still-unused
    # payload identities may differ. No new operation, model or allowance exists.
    cases = value["cases"]
    if not isinstance(cases, list) or len(cases) != LIMIT - 1:
        raise AcceptanceStopped("invalid_continuation")
    _validate_protocol(protocol | {"cases": [protocol["cases"][0], *cases], "files": value["files"]})
    if not set(protocol["files"]).issubset(value["files"]):
        raise AcceptanceStopped("invalid_continuation")
    for original, current in zip(protocol["cases"][1:], cases, strict=True):
        if (set(original) != set(current)
                or {k: v for k, v in original.items() if k not in {"payload_sha256", "canonical_payload_sha256"}}
                != {k: v for k, v in current.items() if k not in {"payload_sha256", "canonical_payload_sha256"}}):
            raise AcceptanceStopped("invalid_continuation")
    artifacts = value["artifacts"]
    if not isinstance(artifacts, dict) or not artifacts or len(artifacts) > 1000:
        raise AcceptanceStopped("invalid_continuation")
    for path, sha in artifacts.items():
        if (not isinstance(path, str) or not Path(path).is_absolute() or ".." in Path(path).parts
                or len(path) > 1000 or not isinstance(sha, str) or HASH.fullmatch(sha) is None):
            raise AcceptanceStopped("invalid_continuation")
    return value


def create_continuation(directory: Path, manifest: dict, *, continuation: Path,
                        stage_root: Path = STAGE_ROOT, repository_root: Path = ROOT) -> None:
    """Bind this one repaired version to the original one-call prefix, once.

    This is deliberately not a reset/resume-anything switch. The historical
    operation must be completed, certain and non-pausing; the original journal,
    registry, twelve-operation order and total allowance are retained.
    """
    try:
        ledger = object.__new__(ChatAcceptanceLedger)
        ledger._initialize(directory, stage_root, repository_root)
        if continuation != directory / CONTINUATION_NAME:
            raise AcceptanceStopped("fixed_continuation_path_required")
        manifest = _validate_continuation(manifest, ledger.protocol, ledger.protocol_hash)
        ledger.continuation = None
        ledger.continuation_hash = None
        ledger.cases = ledger.protocol["cases"]
        with ledger._locked():
            ledger._verify_identity()
            ledger._verify_historical_ledgers()
            rows = ledger._verify_journal()
            raw = _read(directory / "calls.jsonl", MAX_JOURNAL)
            if (len(rows) != 3 or rows[1]["operation"] != "api01" or rows[2]["event"] != "completed"
                    or rows[2]["pause"] or len(raw) != manifest["journal_prefix_bytes"]
                    or digest(raw) != manifest["journal_prefix_sha256"]):
                raise AcceptanceStopped("continuation_requires_original_completed_prefix")
            ledger._verify_continuation_files(manifest)
            encoded = canonical(manifest)
            if len(encoded) > MAX_PROTOCOL:
                raise AcceptanceStopped("evidence_limit")
            # Both files are exclusive. A partially persisted claim is never
            # removed or silently recovered; it deliberately blocks all sends.
            _new(continuation, encoded)
            _new(ledger.registry / CONTINUATION_NAME, canonical({"manifest": str(continuation.resolve()),
                "sha256": digest(encoded), "protocol_sha256": ledger.protocol_hash}))
        ChatAcceptanceLedger(directory, continuation=continuation,
                             stage_root=stage_root, repository_root=repository_root).verify()
    except (OSError, ValueError, TypeError, KeyError):
        raise AcceptanceStopped("continuation_not_committed_do_not_send") from None


class ChatAcceptanceLedger:
    def _initialize(self, directory: Path, stage_root: Path, repository_root: Path) -> None:
        self.directory, self.stage_root, self.repository_root = directory, stage_root, repository_root
        self.registry = stage_root / ".authorized-20261021-12"
        if directory.parent.resolve() != stage_root.resolve():
            raise AcceptanceStopped("batch_identity_mismatch")
        for path in (stage_root, directory, self.registry):
            _safe_directory(path)
        raw = _read(directory / "protocol.json", MAX_PROTOCOL)
        self.protocol = _validate_protocol(_json(raw))
        self.protocol_hash = digest(raw)

    def __init__(self, directory: Path, *, continuation: Path | None = None,
                 stage_root: Path = STAGE_ROOT, repository_root: Path = ROOT) -> None:
        try:
            self._initialize(directory, stage_root, repository_root)
            self.continuation = continuation
            self.continuation_hash: str | None = None
            self.cases = self.protocol["cases"]
            if continuation is not None:
                if continuation != directory / CONTINUATION_NAME:
                    raise AcceptanceStopped("fixed_continuation_path_required")
                raw = _read(continuation, MAX_PROTOCOL)
                self.continuation_hash = digest(raw)
                self.manifest = _validate_continuation(_json(raw), self.protocol, self.protocol_hash)
                self.cases = [self.protocol["cases"][0], *self.manifest["cases"]]
            self.verify()
        except (OSError, ValueError, TypeError, KeyError):
            raise AcceptanceStopped("evidence_unavailable") from None

    @contextmanager
    def _locked(self) -> Iterator[None]:
        fd = os.open(self.registry / "gate.lock", os.O_RDWR | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise AcceptanceStopped("private_evidence_required")
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _verify(self) -> list[dict]:
        self._verify_identity()
        if self.continuation is not None:
            raw = _read(self.continuation, MAX_PROTOCOL)
            if digest(raw) != self.continuation_hash:
                raise AcceptanceStopped("continuation_changed")
            expected = {"manifest": str(self.continuation.resolve()), "sha256": self.continuation_hash,
                        "protocol_sha256": self.protocol_hash}
            if _json(_read(self.registry / CONTINUATION_NAME, 4096)) != expected:
                raise AcceptanceStopped("continuation_identity_mismatch")
            self._verify_continuation_files(self.manifest)
        else:
            for path, sha in self.protocol["files"].items():
                resolved = self.repository_root / path
                if not resolved.resolve().is_relative_to(self.repository_root.resolve()) or _hash_file(resolved) != sha:
                    raise AcceptanceStopped("frozen_file_changed")
        self._verify_historical_ledgers()
        return self._verify_journal()

    def _verify_identity(self) -> None:
        if digest(_read(self.directory / "protocol.json", MAX_PROTOCOL)) != self.protocol_hash:
            raise AcceptanceStopped("frozen_protocol_changed")
        expected = {"batch": BATCH, "limit": LIMIT, "directory": str(self.directory.resolve()),
                    "protocol_sha256": self.protocol_hash}
        if _json(_read(self.registry / "identity.json", 4096)) != expected:
            raise AcceptanceStopped("batch_identity_mismatch")
    def _verify_continuation_files(self, manifest: dict) -> None:
        for path, sha in manifest["files"].items():
            resolved = self.repository_root / path
            if not resolved.resolve().is_relative_to(self.repository_root.resolve()) or _hash_file(resolved) != sha:
                raise AcceptanceStopped("frozen_file_changed")
        for path, sha in manifest["artifacts"].items():
            if _hash_file(Path(path)) != sha:
                raise AcceptanceStopped("continuation_artifact_changed")
        stop = self.stage_root / ".runtime-20261021-12" / "stop.json"
        if _hash_file(stop) != manifest["prior_runtime_stop_sha256"]:
            raise AcceptanceStopped("original_runtime_stop_changed")

    def _verify_historical_ledgers(self) -> None:
        for path, sha in self.protocol["old_ledgers"].items():
            if _hash_file(Path(path)) != sha:
                raise AcceptanceStopped("historical_ledger_changed")

    def _verify_journal(self) -> list[dict]:
        raw = _read(self.directory / "calls.jsonl", MAX_JOURNAL)
        if self.continuation is not None:
            length = self.manifest["journal_prefix_bytes"]
            if len(raw) < length or digest(raw[:length]) != self.manifest["journal_prefix_sha256"]:
                raise AcceptanceStopped("original_journal_prefix_changed")
        if not raw.endswith(b"\n"):
            raise AcceptanceStopped("incomplete_journal")
        rows = [_json(line) for line in raw.splitlines()]
        if not rows or len(rows) > 1 + 2 * LIMIT or rows[0] != {"event": "initialized", "batch": BATCH, "protocol_sha256": self.protocol_hash}:
            raise AcceptanceStopped("invalid_journal")
        previous = digest(canonical(rows[0]))
        attempts = []
        active: dict | None = None
        paused = False
        for row in rows[1:]:
            if (not isinstance(row, dict) or row.get("previous_sha256") != previous
                    or row.get("protocol_sha256") != self.protocol_hash or row.get("batch") != BATCH):
                raise AcceptanceStopped("invalid_journal")
            if row.get("event") == "attempt":
                extra = {"continuation_sha256"} if self.continuation is not None and len(attempts) >= 1 else set()
                if (set(row) != {"event", "operation", "ordinal", "feature", "model", "payload_sha256", "batch", "protocol_sha256", "previous_sha256", "at"} | extra
                        or active is not None or paused or len(attempts) >= LIMIT or row.get("operation") != IDS[len(attempts)]):
                    raise AcceptanceStopped("invalid_journal")
                if extra and row["continuation_sha256"] != self.continuation_hash:
                    raise AcceptanceStopped("continuation_identity_mismatch")
                case = self.cases[len(attempts)]
                if (row.get("ordinal") != len(attempts) + 1 or row.get("payload_sha256") != case["payload_sha256"]
                        or row.get("model") != MODEL or row.get("feature") != case["feature"]):
                    raise AcceptanceStopped("invalid_journal")
                marker = _json(_read(self.registry / f"{row['operation']}.json", 4096))
                if marker != row:
                    raise AcceptanceStopped("reservation_marker_mismatch")
                attempts.append(row)
                active = row
            elif row.get("event") == "completed":
                extra = {"continuation_sha256"} if self.continuation is not None and len(attempts) >= 2 else set()
                if (set(row) != {"event", "operation", "outcome", "pause", "metadata", "batch", "protocol_sha256", "previous_sha256", "at"} | extra
                        or active is None or row.get("operation") != active["operation"] or row.get("outcome") not in OUTCOMES
                        or row.get("pause") != (row["outcome"] in PAUSE)):
                    raise AcceptanceStopped("invalid_journal")
                if extra and row["continuation_sha256"] != self.continuation_hash:
                    raise AcceptanceStopped("continuation_identity_mismatch")
                checked_metadata(row["metadata"])
                paused = row["pause"]
                active = None
            else:
                raise AcceptanceStopped("invalid_journal")
            if not isinstance(row["at"], str) or len(row["at"]) > 40:
                raise AcceptanceStopped("invalid_journal")
            try:
                timestamp = datetime.fromisoformat(row["at"])
                if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                    raise ValueError()
            except ValueError:
                raise AcceptanceStopped("invalid_journal") from None
            previous = digest(canonical(row))
        registry_extra = [CONTINUATION_NAME] if self.continuation is not None else []
        if sorted(p.name for p in self.registry.iterdir()) != sorted(["identity.json", "gate.lock", *registry_extra] + [f"{r['operation']}.json" for r in attempts]):
            raise AcceptanceStopped("reservation_marker_mismatch")
        return rows

    def verify(self) -> dict:
        try:
            with self._locked():
                rows = self._verify()
                consumed = sum(row["event"] == "attempt" for row in rows)
                status = {"batch": BATCH, "consumed": consumed, "remaining": LIMIT - consumed,
                          "paused": rows[-1]["event"] == "attempt" or any(r.get("pause") for r in rows)}
                if self.continuation is not None:
                    status["continuation_sha256"] = self.continuation_hash
                return status
        except (OSError, ValueError, TypeError, KeyError):
            raise AcceptanceStopped("evidence_unavailable") from None

    def _append(self, rows: list[dict], row: dict, *, reservation: bool = False) -> dict:
        row = {**row, "batch": BATCH, "protocol_sha256": self.protocol_hash,
               "previous_sha256": digest(canonical(rows[-1])), "at": datetime.now(timezone.utc).isoformat()}
        if self.continuation is not None:
            row["continuation_sha256"] = self.continuation_hash
        if reservation:
            _new(self.registry / f"{row['operation']}.json", canonical(row))
        fd = os.open(self.directory / "calls.jsonl", os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(canonical(row) + b"\n")
                stream.flush()
                os.fsync(fd)
        finally:
            os.close(fd)
        return row

    def reserve(self, operation: str, payload: dict | bytes, *, model: str = MODEL) -> dict:
        """Must return successfully BEFORE any upstream send. No refund exists."""
        try:
            with self._locked():
                rows = self._verify()
                consumed = sum(row["event"] == "attempt" for row in rows)
                if (consumed >= LIMIT or rows[-1]["event"] == "attempt" or any(r.get("pause") for r in rows)):
                    raise AcceptanceStopped("acceptance_paused_or_exhausted")
                if operation != IDS[consumed]:
                    raise AcceptanceStopped("operation_not_next_or_already_used")
                case = self.cases[consumed]
                if model != MODEL or payload_digest(payload, case["payload_hash_kind"]) != case["payload_sha256"]:
                    raise AcceptanceStopped("payload_mismatch")
                if "canonical_payload_sha256" in case:
                    value = _json(payload) if isinstance(payload, bytes) else payload
                    if not isinstance(value, dict) or digest(canonical(value)) != case["canonical_payload_sha256"]:
                        raise AcceptanceStopped("payload_mismatch")
                return self._append(rows, {"event": "attempt", "operation": operation, "ordinal": consumed + 1,
                    "feature": case["feature"], "model": model, "payload_sha256": case["payload_sha256"]}, reservation=True)
        except (OSError, ValueError, TypeError, KeyError):
            raise AcceptanceStopped("reservation_not_committed_do_not_send") from None

    def complete(self, operation: str, *, outcome: str, metadata: dict | None = None) -> None:
        if outcome not in OUTCOMES:
            raise AcceptanceStopped("invalid_outcome")
        safe = checked_metadata(metadata or {})
        try:
            with self._locked():
                rows = self._verify()
                if rows[-1]["event"] != "attempt" or rows[-1]["operation"] != operation:
                    raise AcceptanceStopped("completion_not_active")
                self._append(rows, {"event": "completed", "operation": operation,
                    "outcome": outcome, "pause": outcome in PAUSE, "metadata": safe})
        except (OSError, ValueError, TypeError, KeyError):
            raise AcceptanceStopped("completion_uncertain_stop") from None


def checked_metadata(value: object) -> dict:
    """Only known scalar metadata, never raw message/content/exception input."""
    fields = {"upstream_request_id", "upstream_model", "http_status", "input_tokens", "output_tokens", "total_tokens", "elapsed_ms", "response_sha256"}
    if not isinstance(value, dict) or not set(value).issubset(fields):
        raise AcceptanceStopped("unsafe_metadata")
    for key, item in value.items():
        if item is None:
            continue
        if key in {"upstream_request_id", "upstream_model"}:
            valid = isinstance(item, str) and SAFE_ID.fullmatch(item) is not None
        elif key == "response_sha256":
            valid = isinstance(item, str) and HASH.fullmatch(item) is not None
        else:
            valid = type(item) is int and 0 <= item <= 10_000_000
            if key == "http_status":
                valid = valid and 100 <= item <= 599
        if not valid:
            raise AcceptanceStopped("unsafe_metadata")
    return dict(value)


def safe_upstream_metadata(data: object) -> dict:
    raw = data if isinstance(data, dict) else {}
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    result: dict = {}
    for target, field in (("upstream_request_id", "id"), ("upstream_model", "model")):
        value = raw.get(field)
        result[target] = value if isinstance(value, str) and SAFE_ID.fullmatch(value) else None
    for target, field in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens"), ("total_tokens", "total_tokens")):
        value = usage.get(field)
        result[target] = value if type(value) is int and 0 <= value <= 10_000_000 else None
    return result

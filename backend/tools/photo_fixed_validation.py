"""Opt-in execution of the immutable photo-review-v1 eight-image protocol.

No product hooks, arbitrary directory/model, automatic resume, or retry. The
external reservation registry survives deletion/truncation of the batch ledger.
All network sends use the existing bounded vision transport after durable reserve.
"""
import argparse
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import time

import httpx
from pydantic import ValidationError

from core.config import get_settings
from tooling.providers import photo_diagnostics as diagnostics
from tooling.providers.qwen import QwenError, beijing_endpoint, parse_content, strict_json
from tooling.providers.qwen_vision import MODEL, PROMPT_VERSION, QwenVisionClient
from core.schemas.photo import PhotoSelection
from tools import photo_evaluation_manifest as preparation
from tools.itinerary_query_acceptance import Ledger, ROOT

canonical, digest = preparation.canonical, preparation.digest
EVIDENCE = ROOT.parent / "trippilot-evidence/media-1"
STAGE = EVIDENCE / "fixed-validation-20261016"
REGISTRY = EVIDENCE / "fixed-validation-20261016-reservations"
FROZEN = EVIDENCE / "standards-freeze-20261016/protocol.json"
FROZEN_HASH = "c803d2f2b53174c25e7367a279f5906dbcc55ff54b9671dad18eeb2833204bfa"
MANIFEST = ROOT / "backend/evaluations/photo-review-v1.json"
BATCH = "media-photo-review-v1-execution-20261016"
IDS = [f"s{i:02}" for i in range(1, 9)]
FILES = ["backend/tools/photo_fixed_validation.py", "backend/tests/test_photo_fixed_validation.py",
         "backend/tools/itinerary_query_acceptance.py", "backend/app/integrations/qwen.py", "backend/uv.lock"]
OUTPUT_FAILURES = {"structure", "truncated", "refused", "empty"}


def write_new(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    # Persist directory entry, not just the contents of a new reservation file.
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def check_preparation() -> dict:
    if digest(FROZEN.read_bytes()) != FROZEN_HASH:
        raise QwenError("configuration")
    preparation.verify(MANIFEST, EVIDENCE, FROZEN)
    return strict_json(FROZEN.read_bytes())


def initialize() -> None:
    frozen = check_preparation()
    if STAGE.exists() or REGISTRY.exists():
        raise ValueError("Execution already exists; never reinitialize an allowance")
    files = {p: digest((ROOT / p).read_bytes()) for p in FILES}
    history = EVIDENCE.parent
    protocol = {
        "version": BATCH, "limit": 8, "authorization": "Explicit user authorization: s01-s08 once each, no retries or probes",
        "created_at": datetime.now(timezone.utc).isoformat(), "frozen_protocol_hash": FROZEN_HASH,
        "code_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "files": files, "code_version": digest(canonical(files)),
        "old_ledgers": {str(p.relative_to(history)): digest(p.read_bytes()) for p in sorted(history.rglob("*.jsonl"))},
        "model": MODEL, "prompt_version": PROMPT_VERSION, "cases": frozen["future_operations"],
        "parameters": {"stream": False, "enable_thinking": False, "max_tokens": 1024,
                       "response_format": {"type": "json_object"}, "deadline_seconds": 20,
                       "max_response_bytes": 32768, "max_payload_bytes": 7340032, "retries": 0},
        "stop": "Execution/version/accounting/config/auth/quota/rate/network/cancel errors pause; output failures retained without repair",
    }
    protected = list(EVIDENCE.rglob("*")) + list((ROOT / ".playwright-mcp").rglob("*"))
    protected += list((ROOT / "backend/data").rglob("*"))
    protected += [ROOT / path for path in (
        "backend/app/__pycache__/config.cpython-313.pyc",
        "backend/app/integrations/__pycache__/amap.cpython-313.pyc",
        "backend/app/__pycache__/logging_filters.cpython-313.pyc",
        "backend/app/api/__pycache__/amap_proxy.cpython-313.pyc",
        "backend/app/integrations/__pycache__/amap_proxy.cpython-313.pyc")]
    before = {str(p): digest(p.read_bytes()) for p in sorted(set(protected)) if p.is_file()}
    # Exclusive registry first: any partial initialization fails closed.
    REGISTRY.mkdir()
    STAGE.mkdir()
    write_new(STAGE / "protocol.json", protocol)
    protocol_hash = digest((STAGE / "protocol.json").read_bytes())
    write_new(REGISTRY / "identity.json", {"batch": BATCH, "protocol_hash": protocol_hash, "limit": 8})
    write_new(STAGE / "calls.jsonl", {"event": "initialized", "protocol_hash": protocol_hash, "batch": BATCH})
    write_new(STAGE / "protected-before.json", before)


class FixedLedger(Ledger):
    def check_rows(self, raw: bytes) -> list[dict]:
        if not raw.endswith(b"\n"):
            raise QwenError("configuration")
        rows = [strict_json(line) for line in raw.splitlines()]
        if not rows or rows[0] != {"event": "initialized", "protocol_hash": self.protocol_hash, "batch": BATCH}:
            raise QwenError("configuration")
        for index, row in enumerate(rows[1:]):
            if (index // 2 >= 8 or row.get("event") != ("attempt" if index % 2 == 0 else "completed")
                    or row.get("operation") != IDS[index // 2] or row.get("batch") != BATCH
                    or row.get("protocol_hash") != self.protocol_hash
                    or row.get("code_version") != self.protocol["code_version"]):
                raise QwenError("configuration")
        attempts = [r for r in rows if r["event"] == "attempt"]
        markers = sorted(p.name for p in REGISTRY.iterdir())
        if markers != sorted(["identity.json"] + [f"{r['operation']}.json" for r in attempts]):
            raise QwenError("configuration")
        for index, row in enumerate(attempts):
            expected = self.reservation(IDS[index])
            if (row.get("ordinal") != index + 1 or any(row.get(k) != v for k, v in expected.items())
                    or strict_json((REGISTRY / f"{IDS[index]}.json").read_bytes()) != expected):
                raise QwenError("configuration")
        return rows

    def verify(self) -> None:
        super().verify()
        frozen = check_preparation()
        p = self.protocol
        if (self.directory.resolve() != STAGE.resolve() or p["version"] != BATCH or p["limit"] != 8
                or p["frozen_protocol_hash"] != FROZEN_HASH or p["cases"] != frozen["future_operations"]
                or p["model"] != MODEL or p["prompt_version"] != PROMPT_VERSION
                or set(p["files"]) != set(FILES) or p["code_version"] != digest(canonical(p["files"]))
                or strict_json((REGISTRY / "identity.json").read_bytes()) != {
                    "batch": BATCH, "protocol_hash": self.protocol_hash, "limit": 8}):
            raise QwenError("configuration")

    def reservation(self, identifier: str) -> dict:
        case = self.protocol["cases"][IDS.index(identifier)]
        return {"operation": identifier, "request_id": f"{BATCH}-{identifier}", "batch": BATCH,
                "protocol_hash": self.protocol_hash, "code_version": self.protocol["code_version"],
                "model": MODEL, "prompt_version": PROMPT_VERSION, **{k: v for k, v in case.items() if k != "id"}}

    def guard_append(self, row: dict, raw: bytes, reserve: bool) -> dict:
        self.verify()
        rows = self.check_rows(raw)
        attempts = [r for r in rows if r["event"] == "attempt"]
        if reserve:
            if (len(attempts) >= 8 or rows[-1]["event"] == "attempt" or any(r.get("pause") for r in rows)):
                raise QwenError("quota")
            expected = self.reservation(IDS[len(attempts)])
            if row != {"event": "attempt", **expected}:
                raise QwenError("configuration")
            # Under the base Ledger flock, BEFORE journal append or network send.
            # An orphan marker after a crash blocks, never authorizes a resend.
            write_new(REGISTRY / f"{expected['operation']}.json", expected)
        elif (rows[-1]["event"] != "attempt" or row.get("event") != "completed"
              or row.get("operation") != rows[-1]["operation"]):
            raise QwenError("configuration")
        return {**row, "batch": BATCH, "code_version": self.protocol["code_version"]}


class SafeDiagnostics(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.items: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        # Only the existing grammar-owned diagnostic logger, never general logs.
        if record.msg == "photo_structure_rejected %s" and len(self.items) < 4:
            self.items.append(json.loads(record.args[0]))


@contextmanager
def capture_diagnostics():
    handler = SafeDiagnostics()
    diagnostics.logger.addHandler(handler)
    try:
        yield handler.items
    finally:
        diagnostics.logger.removeHandler(handler)


def safe_metadata(data: object) -> dict:
    raw = data if isinstance(data, dict) else {}
    result: dict = {}
    for key in ("id", "model"):
        value = raw.get(key)
        result["upstream_" + key] = value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,160}", value) else None
    usage = raw.get("usage")
    result["usage"] = {key: value if type(value := usage.get(key) if isinstance(usage, dict) else None) is int
                       and 0 <= value <= 1_000_000 else None
                       for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
    return result


async def execute_one(ledger: FixedLedger, identifier: str, client: QwenVisionClient) -> dict:
    ledger.verify()
    if identifier not in IDS or client.settings.qwen_vision_model != MODEL:
        raise QwenError("configuration")
    beijing_endpoint(client.settings)  # Local only; never a connectivity probe.
    manifest = preparation.Manifest.model_validate(strict_json(MANIFEST.read_bytes()))
    sample = manifest.samples[IDS.index(identifier)]
    payload = preparation.model_input(preparation.checked_file(EVIDENCE, sample.input))
    row = ledger.reservation(identifier)
    if digest(canonical(payload)) != row["payload_sha256"]:
        raise QwenError("configuration")
    ledger.append({"event": "attempt", **row}, reserve=True)
    started = time.monotonic()
    result = {"event": "completed", "operation": identifier, "request_id": row["request_id"],
              "pause": False, "upstream_id": None, "upstream_model": None,
              "usage": safe_metadata(None)["usage"]}
    with capture_diagnostics() as issues:
        try:
            data = await client._request(payload)  # Same one-attempt, bounded production transport.
            result.update(safe_metadata(data))
            try:
                value, _ = parse_content(data)
                PhotoSelection.model_validate(value)
                result.update(category="validated_structure", selection=value)
            except ValidationError as error:
                diagnostics.record_validation(error)
                raise QwenError("structure") from None
            except QwenError as error:
                if error.code == "structure":
                    diagnostics.record_content_rejection(data)
                raise
        except QwenError as error:
            result.update(category="error", error=error.code, pause=error.code not in OUTPUT_FAILURES)
        except BaseException:
            result.update(category="error", error="cancelled_or_execution", pause=True)
            raise
        finally:
            result.update(elapsed_seconds=round(time.monotonic() - started, 3), diagnostics=issues)
            # Persistence failure leaves the premark/registry occupied and stops.
            ledger.append(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["initialize", "verify", "run"])
    parser.add_argument("operation", nargs="?", choices=IDS)
    args = parser.parse_args()
    if args.command == "initialize":
        initialize()
        print("Execution frozen; model calls 0")
        return
    ledger = FixedLedger(STAGE)
    ledger.verify()
    ledger.check_rows((STAGE / "calls.jsonl").read_bytes())
    if args.command == "verify":
        print("Frozen preparation and execution verified; no model request")
        return
    if not args.operation:
        parser.error("run requires exactly one frozen operation")

    async def run() -> dict:
        async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(retries=0), follow_redirects=False) as client:
            return await execute_one(ledger, args.operation, QwenVisionClient(get_settings(), client))
    result = asyncio.run(run())
    print(json.dumps(result, ensure_ascii=False))
    if result["pause"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

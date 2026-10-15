"""New, explicit 12-call authorization; old media-1 ledger is immutable.

Only this fixed directory and frozen 10 API + 2 Chrome operations may send.
Uses the existing one-attempt transport and flock/fsync pre-send counter.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

import httpx
import uvicorn
from app.config import get_settings
from app.integrations.qwen import QwenError, strict_json
from app.integrations.qwen_vision import MODEL, PROMPT_VERSION, vision_payload
from app.services.photo import normalize_photo
from tools import photo_acceptance as old
from tools.itinerary_query_acceptance import Ledger, ROOT, canonical, digest

STAGE = ROOT.parent / "trippilot-evidence/media-1/conservative-validation"
BATCH = "media-1-conservative-v2-20261016"
IDS = [f"api{i:02}" for i in range(1, 11)] + ["chrome01", "chrome02"]
FILES = list(dict.fromkeys(old.FILES + [
    "backend/app/integrations/photo_diagnostics.py", "backend/tests/test_photo_diagnostics.py",
    "backend/tests/test_photo_lifecycle.py", "backend/tools/photo_conservative_acceptance.py",
    "backend/tests/test_photo_conservative_acceptance.py", "frontend/src/lib/photo-api.test.ts",
    "frontend/src/components/places/photo-place-picker.test.tsx",
]))


def write_new(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as target:
        json.dump(value, target, ensure_ascii=False, indent=2)
        target.flush(); os.fsync(target.fileno())


def freeze() -> None:
    if (STAGE / "protocol.json").exists() or (STAGE / "calls.jsonl").exists():
        raise ValueError("Already frozen: cannot initialize another allowance")
    manifest = strict_json((STAGE / "samples.json").read_bytes())
    cases = manifest["cases"]
    if [c["id"] for c in cases] != IDS:
        raise ValueError("Freeze ten API and two Chrome operations")
    original = strict_json((STAGE.parent / "protocol.json").read_bytes())
    for index, case in enumerate(cases):
        image = (STAGE / case["file"]).resolve()
        if not image.is_relative_to(STAGE.parent.resolve()):
            raise ValueError("Only fixed media-1 assets")
        raw = image.read_bytes()
        case["file_hash"] = digest(raw)
        if index < 8 and (case["id"] != original["cases"][index]["id"]
                         or case["file_hash"] != original["cases"][index]["file_hash"]
                         or case["destination_id"] != original["cases"][index]["destination_id"]):
            raise ValueError("Original API identity/image/city changed")
        normalized = normalize_photo(raw)
        case["normalized_hash"] = digest(normalized)
        case["payload_hash"] = digest(canonical(vision_payload(normalized)))
    history = ROOT.parent / "trippilot-evidence"
    files = {p: digest((ROOT / p).read_bytes()) for p in FILES}
    protocol = {
        "version": BATCH, "limit": 12, "model": MODEL, "prompt_version": PROMPT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "code_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "code_version": digest(canonical(files)), "files": files,
        "old_ledgers": {str(p.relative_to(history)): digest(p.read_bytes()) for p in sorted(history.rglob("*.jsonl")) if STAGE not in p.parents},
        "old_protocol_hash": digest((STAGE.parent / "protocol.json").read_bytes()),
        "manifest_hash": digest((STAGE / "samples.json").read_bytes()), "cases": cases,
        "criteria": manifest["criteria"], "parameters": {"stream": False, "enable_thinking": False, "max_tokens": 1024,
            "deadline_seconds": 20, "response_format": "json_object", "retry": 0},
        "stop": "Execution/configuration/auth/quota/rate_limit/transport/cancel/version/accounting errors pause. Semantic/structure errors retained; no retries.",
    }
    write_new(STAGE / "protocol.json", protocol)
    # If a crash occurs between these writes, freeze refuses to resume or reset.
    with (STAGE / "calls.jsonl").open("x", encoding="utf-8") as target:
        target.write(json.dumps({"event": "initialized", "batch": BATCH,
            "protocol_hash": digest((STAGE / "protocol.json").read_bytes())}) + "\n")
        target.flush(); os.fsync(target.fileno())


class ConservativeLedger(old.PhotoLedger):
    def verify(self) -> None:
        Ledger.verify(self)
        p = self.protocol
        if (self.directory.resolve() != STAGE.resolve() or p["version"] != BATCH
                or p["limit"] != 12 or p["model"] != MODEL or p["prompt_version"] != PROMPT_VERSION
                or set(p["files"]) != set(FILES) or p["code_version"] != digest(canonical(p["files"]))
                or [c["id"] for c in p["cases"]] != IDS
                or digest((self.directory / "samples.json").read_bytes()) != p["manifest_hash"]
                or digest((self.directory.parent / "protocol.json").read_bytes()) != p["old_protocol_hash"]):
            raise QwenError("configuration")
        for case in p["cases"]:
            if digest((self.directory / case["file"]).read_bytes()) != case["file_hash"]:
                raise QwenError("configuration")
        self.check_rows((self.directory / "calls.jsonl").read_bytes())

    def check_rows(self, raw: bytes) -> list[dict]:
        if not raw.endswith(b"\n"):
            raise QwenError("configuration")
        rows = [strict_json(line) for line in raw.splitlines()]
        if not rows or rows[0] != {"event": "initialized", "batch": BATCH, "protocol_hash": self.protocol_hash}:
            raise QwenError("configuration")
        for index, row in enumerate(rows[1:]):
            if (index // 2 >= 12 or row.get("operation") != IDS[index // 2]
                    or row.get("event") != ("attempt" if index % 2 == 0 else "completed")
                    or row.get("batch") != BATCH or row.get("protocol_hash") != self.protocol_hash
                    or row.get("code_version") != self.protocol["code_version"]):
                raise QwenError("configuration")
        return rows

    def guard_append(self, row: dict, raw: bytes, reserve: bool) -> dict:
        self.verify()
        rows = self.check_rows(raw)
        attempts = [r for r in rows if r["event"] == "attempt"]
        if reserve:
            if len(attempts) >= 12:
                raise QwenError("quota")
            case = self.protocol["cases"][len(attempts)]
            if row["event"] != "attempt" or row["operation"] != case["id"] or row["payload_hash"] != case["payload_hash"]:
                raise QwenError("configuration")
        elif rows[-1].get("event") != "attempt" or row.get("event") != "completed" or row.get("operation") != rows[-1]["operation"]:
            raise QwenError("configuration")
        return {**row, "batch": BATCH, "code_version": self.protocol["code_version"]}


async def api_run() -> None:
    ledger = ConservativeLedger(STAGE); ledger.verify()
    async with httpx.AsyncClient(timeout=35) as client:
        for case in ledger.protocol["cases"][:10]:
            rows = ledger.check_rows((STAGE / "calls.jsonl").read_bytes())
            if any(r.get("operation") == case["id"] for r in rows):
                raise ValueError("Already attempted: no automatic resume or resend")
            response = await client.post("http://127.0.0.1:8000/media/landmark",
                params={"destination_id": case["destination_id"], "request_id": case["id"]},
                content=(STAGE / case["file"]).read_bytes(), headers={"Content-Type": "application/octet-stream"})
            write_new(STAGE / f"{case['id']}.json", {"id": case["id"], "http_status": response.status_code, "response": response.json()})
            print(case["id"], response.status_code, flush=True)
            if response.status_code != 200 and response.json().get("detail", {}).get("code") not in {"structure", "truncated", "refused", "empty"}:
                break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("command", choices=["freeze", "serve", "api"])
    command = parser.parse_args().command
    if command == "freeze": freeze()
    elif command == "api": asyncio.run(api_run())
    else:
        if get_settings().qwen_vision_model != MODEL: raise ValueError("Frozen model mismatch")
        ledger = ConservativeLedger(STAGE); ledger.verify(); old.install(ledger)
        uvicorn.run(old.app, host="127.0.0.1", port=8000, access_log=False)

"""Opt-in media-2B: one immutable eight-operation ledger, no product changes.

Real extraction is offline during freeze; model requests use the existing
counted one-attempt vision transport. Chrome operations require explicit arming.
"""
import argparse
import asyncio
import base64
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
from app.services.video import extract_frames, executables, validate_container
from tools import photo_acceptance as photo
from tools.itinerary_query_acceptance import Ledger, ROOT, canonical, digest

STAGE = ROOT.parent / "trippilot-evidence/media-2b/batch-20261016"
BATCH = "media-2b-selected-frame-v1"
IDS = [f"api{i:02}" for i in range(1, 5)] + [f"chrome{i:02}" for i in range(1, 5)]
FILES = list(dict.fromkeys(photo.FILES + [
    "backend/app/services/video.py", "backend/app/api/video.py", "backend/app/schemas/video.py",
    "backend/app/integrations/photo_diagnostics.py", "backend/tools/video_photo_acceptance.py",
    "backend/tests/test_video_photo_acceptance.py", "frontend/src/lib/video-api.ts",
    "frontend/src/lib/photo-city.ts", "frontend/src/components/places/video-frame-picker.tsx",
    "frontend/src/components/places/requirement-place-picker.tsx", "frontend/src/components/places/video-landmark.test.tsx",
    "frontend/src/components/trip/formal-planning.integration.test.tsx",
]))


def write_new(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush(); os.fsync(stream.fileno())


def asset(name: str) -> Path:
    path = (STAGE / name).resolve()
    if not path.is_relative_to(STAGE.resolve()):
        raise QwenError("configuration")
    return path


async def freeze() -> None:
    if any(STAGE.parent.rglob("protocol.json")) or any(STAGE.parent.rglob("calls.jsonl")):
        raise ValueError("Already frozen; no new directory or allowance")
    manifest = strict_json((STAGE / "samples.json").read_bytes())
    cases = manifest["cases"]
    if [c["id"] for c in cases] != IDS or not manifest.get("criteria"):
        raise ValueError("Exactly four API and four Chrome operations required")
    for case in cases:
        for key in ("source", "expected", "visual_review", "related_samples"):
            if key not in case:
                raise ValueError("Source and pre-output review are mandatory")
        video = asset(case["video"]); raw = video.read_bytes()
        validate_container(raw)
        result = await extract_frames(video, "offline-freeze", executables())
        selected = next(f for f in result.frames if f.index == case["frame_index"])
        image = base64.b64decode(selected.jpeg_base64)
        if image != asset(case["file"]).read_bytes() or selected.target_seconds != case["target_seconds"]:
            raise ValueError("Frozen frame does not match real decoder output")
        case.update(video_hash=digest(raw), file_hash=digest(image),
                    normalized_hash=digest(normalize_photo(image)),
                    payload_hash=digest(canonical(vision_payload(normalize_photo(image)))))
    files = {p: digest((ROOT / p).read_bytes()) for p in FILES}
    history = ROOT.parent / "trippilot-evidence"
    protocol = {"version": BATCH, "limit": 8, "model": MODEL, "prompt_version": PROMPT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "files": files, "code_version": digest(canonical(files)),
        "old_ledgers": {str(p.relative_to(history)): digest(p.read_bytes()) for p in sorted(history.rglob("*.jsonl")) if STAGE not in p.parents},
        "manifest_hash": digest((STAGE / "samples.json").read_bytes()), "cases": cases,
        "parameters": {"model": MODEL, "stream": False, "enable_thinking": False, "max_tokens": 1024,
                       "response_format": "json_object", "deadline_seconds": 20, "retry": 0},
        "criteria": manifest["criteria"], "stop": "Execution/version/accounting/auth/quota/network/cancel pause; semantic/structure failures retained, never resent."}
    write_new(STAGE / "protocol.json", protocol)
    with (STAGE / "calls.jsonl").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": "initialized", "batch": BATCH,
            "protocol_hash": digest((STAGE / "protocol.json").read_bytes())}) + "\n")
        stream.flush(); os.fsync(stream.fileno())


class VideoPhotoLedger(Ledger):
    def verify(self) -> None:
        super().verify()
        p = self.protocol
        if (self.directory.resolve() != STAGE.resolve() or p["version"] != BATCH or p["limit"] != 8
                or p["model"] != MODEL or p["prompt_version"] != PROMPT_VERSION or set(p["files"]) != set(FILES)
                or p["code_version"] != digest(canonical(p["files"])) or [c["id"] for c in p["cases"]] != IDS
                or digest((STAGE / "samples.json").read_bytes()) != p["manifest_hash"]):
            raise QwenError("configuration")
        for c in p["cases"]:
            if digest(asset(c["video"]).read_bytes()) != c["video_hash"] or digest(asset(c["file"]).read_bytes()) != c["file_hash"]:
                raise QwenError("configuration")
        self.rows((STAGE / "calls.jsonl").read_bytes())

    def rows(self, raw: bytes) -> list[dict]:
        if not raw.endswith(b"\n"):
            raise QwenError("configuration")
        rows = [strict_json(line) for line in raw.splitlines()]
        if not rows or rows[0] != {"event": "initialized", "batch": BATCH, "protocol_hash": self.protocol_hash}:
            raise QwenError("configuration")
        for i, r in enumerate(rows[1:]):
            if (i // 2 >= 8 or r.get("operation") != IDS[i // 2] or r.get("event") != ("attempt" if i % 2 == 0 else "completed")
                    or r.get("batch") != BATCH or r.get("protocol_hash") != self.protocol_hash or r.get("code_version") != self.protocol["code_version"]):
                raise QwenError("configuration")
        return rows

    def next_case(self, payload: dict, ctx: dict) -> dict:
        self.verify()
        rows = self.rows((STAGE / "calls.jsonl").read_bytes())
        count = sum(r["event"] == "attempt" for r in rows)
        if count >= 8 or rows[-1]["event"] == "attempt" or any(r.get("pause") for r in rows):
            raise QwenError("quota")
        case = self.protocol["cases"][count]
        if (digest(canonical(payload)) != case["payload_hash"] or ctx.get("destination_id") != case["destination_id"]
                or not ctx.get("request_id") or case["id"].startswith("api") and ctx["request_id"] != case["id"]):
            raise QwenError("configuration")
        if case["id"].startswith("chrome"):
            expected = {"operation": case["id"], "protocol_hash": self.protocol_hash}
            path = STAGE / f"armed-{case['id']}.json"
            if not path.exists() or strict_json(path.read_bytes()) != expected:
                raise QwenError("configuration")
        return case

    def guard_append(self, row: dict, raw: bytes, reserve: bool) -> dict:
        self.verify(); rows = self.rows(raw)
        count = sum(r["event"] == "attempt" for r in rows)
        if reserve:
            if count >= 8: raise QwenError("quota")
            case = self.protocol["cases"][count]
            if row.get("event") != "attempt" or row.get("operation") != case["id"] or row.get("payload_hash") != case["payload_hash"]:
                raise QwenError("configuration")
        elif rows[-1]["event"] != "attempt" or row.get("event") != "completed" or row.get("operation") != rows[-1]["operation"]:
            raise QwenError("configuration")
        return {**row, "batch": BATCH, "code_version": self.protocol["code_version"]}


def arm(operation: str) -> None:
    ledger = VideoPhotoLedger(STAGE); ledger.verify()
    rows = ledger.rows((STAGE / "calls.jsonl").read_bytes())
    count = sum(r["event"] == "attempt" for r in rows)
    if count >= 8 or operation != IDS[count] or not operation.startswith("chrome") or rows[-1]["event"] == "attempt" or any(r.get("pause") for r in rows):
        raise QwenError("quota")
    write_new(STAGE / f"armed-{operation}.json", {"operation": operation, "protocol_hash": ledger.protocol_hash})


async def run_api() -> None:
    ledger = VideoPhotoLedger(STAGE); ledger.verify()
    async with httpx.AsyncClient(timeout=35) as client:
        for case in ledger.protocol["cases"][:4]:
            if any(r.get("operation") == case["id"] for r in ledger.rows((STAGE / "calls.jsonl").read_bytes())):
                raise ValueError("Already attempted; no automatic resume or resend")
            response = await client.post("http://127.0.0.1:8000/media/landmark", params={"destination_id": case["destination_id"], "request_id": case["id"]},
                content=asset(case["file"]).read_bytes(), headers={"Content-Type": "application/octet-stream"})
            write_new(STAGE / f"{case['id']}.json", {"http_status": response.status_code, "response": response.json()})
            print(case["id"], response.status_code, flush=True)
            if response.status_code != 200 and response.json().get("detail", {}).get("code") not in {"structure", "truncated", "refused", "empty"}: break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("command", choices=["freeze", "verify", "serve", "api", "arm"]); parser.add_argument("operation", nargs="?")
    args = parser.parse_args()
    if args.command == "freeze": asyncio.run(freeze())
    elif args.command == "arm": arm(args.operation)
    elif args.command == "api": asyncio.run(run_api())
    else:
        ledger = VideoPhotoLedger(STAGE); ledger.verify()
        if args.command == "serve":
            if get_settings().qwen_vision_model != MODEL: raise ValueError("Frozen model mismatch")
            photo.install(ledger)
            uvicorn.run(photo.app, host="127.0.0.1", port=8000, access_log=False)
        else: print("Frozen protocol and ledger verified; no model request")

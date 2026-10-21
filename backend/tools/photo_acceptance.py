"""Opt-in media-1 8 API + 4 Chrome acceptance. No images/payloads in logs.

Reuses the existing append-only, flock/fsync pre-send accounting implementation.
One stage protocol; creating another directory does not create another quota.
"""
import argparse
import asyncio
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time

import httpx
import uvicorn
from core.config import get_settings
from tooling.providers.qwen import QwenError, strict_json, parse_content
from tooling.providers.qwen_vision import MODEL, PROMPT_VERSION, QwenVisionClient, vision_payload
from core.schemas.photo import PhotoSelection
from tooling.travel.photo import normalize_photo
from tools.itinerary_query_acceptance import Ledger, digest, canonical, ROOT
from api.explanation import get_qwen_http_client
from main import app

STAGE = ROOT.parent / "trippilot-evidence/media-1"
FILES = ["backend/app/api/photo.py", "backend/app/integrations/qwen_vision.py", "backend/app/services/photo.py",
         "backend/app/schemas/photo.py", "backend/app/integrations/qwen.py", "backend/app/config.py",
         "backend/tools/photo_acceptance.py", "backend/tools/itinerary_query_acceptance.py", "backend/uv.lock",
         "backend/main.py", "backend/tests/test_photo.py", "backend/tests/test_photo_acceptance.py",
         "frontend/src/lib/photo-api.ts", "frontend/src/components/places/photo-place-picker.tsx",
         "frontend/src/components/trip-request-form.tsx"]
IDS = [f"api{i:02}" for i in range(1,9)] + [f"chrome{i:02}" for i in range(1,5)]
context: ContextVar[dict] = ContextVar("photo_acceptance_context", default={})
permitted: ContextVar[bool] = ContextVar("photo_acceptance_permitted", default=False)


def freeze() -> None:
    if any(STAGE.rglob("protocol.json")) or any(STAGE.rglob("calls.jsonl")):
        raise ValueError("media-1 already frozen; no quota reset")
    manifest = strict_json((STAGE / "samples.json").read_bytes())
    cases = manifest["cases"]
    if [c["id"] for c in cases] != IDS:
        raise ValueError("Freeze all 8+4 operations first")
    for case in cases:
        path = STAGE / case["file"]
        raw = path.read_bytes()
        case["file_hash"] = digest(raw)
        normalized = normalize_photo(raw)
        case["normalized_hash"] = digest(normalized)
        case["payload_hash"] = digest(canonical(vision_payload(normalized)))
    protocol = {"version":"media-1-acceptance-v1", "created_at":datetime.now(timezone.utc).isoformat(),
        "limit":12, "model":MODEL, "prompt_version":PROMPT_VERSION,
        "code_head":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
        "files":{p:digest((ROOT/p).read_bytes()) for p in FILES},
        "old_ledgers":{str(p.relative_to(STAGE.parent)):digest(p.read_bytes()) for p in sorted(STAGE.parent.rglob("*.jsonl")) if STAGE not in p.parents},
        "manifest_hash":digest((STAGE/"samples.json").read_bytes()), "cases":cases,
        "grading":"clue identity, city, uncertainty/refusal, instruction isolation separately; structure/AMap homonym not proof of photo match; Chrome requires explicit POI confirmation and transactional state",
        "stop":"execution/configuration/auth/quota/rate_limit/timeout/cancel/version/accounting pause; semantic/structure/refusal failures retained, no retries"}
    with (STAGE/"protocol.json").open("x") as f:
        json.dump(protocol,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())


class PhotoLedger(Ledger):
    def verify(self) -> None:
        super().verify()
        if (self.protocol["limit"] != 12 or self.protocol["model"] != MODEL
                or [c["id"] for c in self.protocol["cases"]] != IDS
                or digest((self.directory/"samples.json").read_bytes()) != self.protocol["manifest_hash"]):
            raise QwenError("configuration")
        for case in self.protocol["cases"]:
            if digest((self.directory/case["file"]).read_bytes()) != case["file_hash"]:
                raise QwenError("configuration")

    def guard_append(self, row: dict, raw: bytes, reserve: bool) -> dict:
        self.verify()
        rows = [strict_json(line) for line in raw.splitlines()]
        attempts = [r for r in rows if r["event"] == "attempt"]
        if reserve:
            if len(attempts) >= 12: raise QwenError("quota")
            case = self.protocol["cases"][len(attempts)]
            if row["operation"] != case["id"] or row["payload_hash"] != case["payload_hash"]:
                raise QwenError("configuration")
        return row

    def next_case(self, payload: dict, ctx: dict) -> dict:
        self.verify()
        path=self.directory/"calls.jsonl"
        rows=[strict_json(line) for line in path.read_bytes().splitlines()] if path.exists() else []
        count=sum(r["event"]=="attempt" for r in rows)
        if count >= 12: raise QwenError("quota")
        case=self.protocol["cases"][count]
        if (digest(canonical(payload)) != case["payload_hash"] or ctx.get("destination_id") != case["destination_id"]
                or (case["id"].startswith("api") and ctx.get("request_id") != case["id"])
                or (case["id"].startswith("chrome") and not ctx.get("request_id"))):
            raise QwenError("configuration")
        return case


def install(ledger: PhotoLedger) -> None:
    original=QwenVisionClient._request
    @app.middleware("http")
    async def capture(request, call_next):
        token=context.set(dict(request.query_params) if request.url.path=="/media/landmark" else {})
        try: return await call_next(request)
        finally: context.reset(token)

    async def counted(self, payload):
        case=ledger.next_case(payload,context.get())
        row={"event":"attempt","operation":case["id"],"model":MODEL,"payload_hash":case["payload_hash"],
             "request_id":context.get()["request_id"],"prompt_version":PROMPT_VERSION}
        ledger.append(row,reserve=True)
        started=time.monotonic(); result={"event":"completed","operation":case["id"],"pause":False}
        token=permitted.set(True)
        try:
            raw=await original(self,payload)
            # Keep only recognized clues + bounded identifiers/usage, never raw image or prompt.
            if isinstance(raw,dict):
                for key in ("id","model"):
                    value=raw.get(key);result["upstream_"+key]=value[:160] if isinstance(value,str) else None
            try:
                value,usage=parse_content(raw);result["usage"]=usage.model_dump()
                result["selection"]=PhotoSelection.model_validate(value).model_dump()
                result["category"]="validated_structure" # quality still requires visual review
            except (QwenError,ValueError):
                result["category"]="invalid_output"
            return raw
        except QwenError as error:
            result.update(error=error.code,pause=error.code not in {"structure","truncated","refused","empty"});raise
        except BaseException:
            result.update(error="cancelled_or_execution",pause=True);raise
        finally:
            permitted.reset(token);result["elapsed_seconds"]=round(time.monotonic()-started,3);ledger.append(result)
    QwenVisionClient._request=counted

    class OnlyCountedVision(httpx.AsyncHTTPTransport):
        async def handle_async_request(self, request):
            if not permitted.get(): raise QwenError("quota")
            return await super().handle_async_request(request)
    async def client():
        async with httpx.AsyncClient(transport=OnlyCountedVision(retries=0),follow_redirects=False) as c: yield c
    app.dependency_overrides[get_qwen_http_client]=client


async def api_run() -> None:
    ledger=PhotoLedger(STAGE);ledger.verify()
    async with httpx.AsyncClient(timeout=35) as c:
        for case in ledger.protocol["cases"][:8]:
            if (STAGE/f"{case['id']}.json").exists(): raise ValueError("Do not resend a completed case")
            response=await c.post("http://127.0.0.1:8000/media/landmark",params={"destination_id":case["destination_id"],"request_id":case["id"]},
                                  content=(STAGE/case["file"]).read_bytes(),headers={"Content-Type":"application/octet-stream"})
            with (STAGE/f"{case['id']}.json").open("x") as f:
                json.dump({"id":case["id"],"http_status":response.status_code,"response":response.json()},f,ensure_ascii=False,indent=2)
            print(case["id"],response.status_code,flush=True)
            if response.status_code!=200 and response.json().get("detail",{}).get("code") not in {"structure","truncated","refused","empty"}: break


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("command",choices=["freeze","serve","api"]);args=parser.parse_args()
    if args.command=="freeze":freeze()
    elif args.command=="api":asyncio.run(api_run())
    else:
        if get_settings().qwen_vision_model!=MODEL: raise ValueError("Frozen model mismatch")
        ledger=PhotoLedger(STAGE);ledger.verify();install(ledger)
        uvicorn.run(app,host="127.0.0.1",port=8000,access_log=False)

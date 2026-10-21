"""Explicitly authorized 6D-2 fix acceptance; all phases share ONE 16-attempt ledger.

Run with -B. Other model interfaces are blocked by the common acceptance gate.
No product quota/config/secrets are changed. A final run pins sources before use.
"""
import argparse
import hashlib
import json
from pathlib import Path
import uuid

import uvicorn

from core.config import get_settings
from core.schemas.knowledge_answer import PROMPT_VERSION
from tooling.travel.knowledge import build_index, fingerprint
from tooling.travel.knowledge_answer import SYSTEM
from tools.knowledge_acceptance import EVIDENCE, ROOT, Ledger, RecordedApp, app, install

FIX = EVIDENCE / "fix"
LIMIT = 16  # This authorization only; NOT a permanent product limit.


def snapshot() -> dict:
    repo = ROOT.parent
    files = sorted({*repo.glob("backend/app/**/*.py"), *repo.glob("backend/tools/knowledge*acceptance.py"),
                    *repo.glob("frontend/src/**/*.ts"), *repo.glob("frontend/src/**/*.tsx"),
                    ROOT / "main.py", ROOT / "knowledge/corpus.json", ROOT / "knowledge/answer-evaluation.json"})
    return {"prompt_version": PROMPT_VERSION, "prompt_hash": fingerprint(SYSTEM),
            "model": get_settings().qwen_model,
            "corpus_version": build_index().corpus.corpus_version, "corpus_hash": build_index().hash,
            "files": {str(f.relative_to(repo)): hashlib.sha256(f.read_bytes()).hexdigest() for f in files}}


def pin_final(directory: Path, frozen: dict) -> None:
    file = directory / "final-manifest.json"
    if file.exists():
        if json.loads(file.read_text()) != frozen:
            raise RuntimeError("Final version changed; do not mix runs or overwrite the manifest")
    else:
        with file.open("x", encoding="utf-8") as stream:
            json.dump(frozen, stream, ensure_ascii=False, indent=2)


class FrozenApp:
    def __init__(self, application, frozen: dict):
        self.application, self.frozen = application, frozen

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope.get("method") == "POST" and scope.get("path") == "/knowledge/answer":
            if snapshot() != self.frozen:
                await send({"type": "http.response.start", "status": 503, "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": b'{"detail":"Acceptance version changed; stopped before upstream"}'})
                return
        await self.application(scope, receive, send)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["targeted", "repair", "final"], required=True)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    FIX.mkdir(parents=True, exist_ok=True)
    frozen = snapshot()
    if args.phase == "final": pin_final(FIX, frozen)
    run_id = uuid.uuid4().hex
    with (FIX / f"run-{run_id}.json").open("x", encoding="utf-8") as stream:
        json.dump({"phase": args.phase, **frozen}, stream, ensure_ascii=False, indent=2)
    ledger = Ledger(FIX / "calls.jsonl", LIMIT, stage="6D-2-fix")
    install(ledger)
    recorded = RecordedApp(app, FIX / "responses", ledger, {"phase": args.phase, "run_id": run_id})
    uvicorn.run(FrozenApp(recorded, frozen), host="127.0.0.1", port=args.port, access_log=False)

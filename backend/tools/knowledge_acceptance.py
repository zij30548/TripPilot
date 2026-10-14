"""Opt-in local acceptance server with a durable, cross-process attempt ceiling.

This is NOT a product-wide quota. Run with -B; no credentials or HTTP headers
are persisted. Only explicitly triggered requests use the upstream. No retries.
"""
import argparse
import asyncio
from contextvars import ContextVar
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

import httpx
import uvicorn

from app.api.explanation import get_qwen_http_client
from app.integrations.qwen import QwenClient, QwenError
from app.schemas.knowledge_answer import PROMPT_VERSION
from app.services.knowledge import build_index, fingerprint
from app.services.knowledge_answer import SYSTEM
from main import app

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT.parent.parent / "trippilot-evidence" / "6d-2"
context: ContextVar[dict | None] = ContextVar("acceptance", default=None)


class Ledger:
    def __init__(self, path: Path, limit: int):
        self.path, self.limit = path, limit
        path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict, reserve: bool = False) -> dict:
        with self.path.open("a+", encoding="utf-8") as stream:
            os.chmod(self.path, 0o600)
            fcntl.flock(stream, fcntl.LOCK_EX)
            stream.seek(0)
            rows = [json.loads(line) for line in stream if line.strip()]
            reservations = [r for r in rows if r["event"] == "attempt"]
            if reserve and len(reservations) >= self.limit:
                raise QwenError("quota")
            if reserve: record = {**record, "ordinal": len(reservations)+1}
            record = {**record, "time": datetime.now(timezone.utc).isoformat(), "stage": "6D-2"}
            stream.seek(0, 2); stream.write(json.dumps(record, ensure_ascii=False)+"\n")
            stream.flush(); os.fsync(stream.fileno())
            return record


class CountedTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport, ledger: Ledger):
        self.inner, self.ledger = inner, ledger

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        state = context.get()
        if state is None or state.get("attempt"):
            # Accept only one explicitly attributed operation; no accidental other model UI.
            raise QwenError("quota")
        row = self.ledger.append({"event": "attempt", "operation": state["operation"],
                                  "request_id": state.get("request_id"), "payload_hash": hashlib.sha256(request.content).hexdigest()}, reserve=True)
        state["attempt"] = row["ordinal"]
        return await self.inner.handle_async_request(request)

    async def aclose(self): await self.inner.aclose()


def install(ledger: Ledger):
    original = QwenClient.generate
    async def generate(self, payload):
        state = context.get()
        if state is None: raise QwenError("quota")
        state.update(model=self.settings.qwen_model, prompt_version=PROMPT_VERSION,
                     prompt_hash=fingerprint(payload["messages"][0]["content"]),
                     model_input=json.loads(payload["messages"][1]["content"]))
        try:
            content, usage = await original(self, payload)
            state.update(model_output=content, usage=usage.model_dump())
            return content, usage
        except QwenError as error:
            state["model_error"] = error.code; raise
        except asyncio.CancelledError:
            state["model_error"] = "cancelled"; raise
    QwenClient.generate = generate
    async def client():
        async with httpx.AsyncClient(transport=CountedTransport(httpx.AsyncHTTPTransport(retries=0), ledger), follow_redirects=False, timeout=20) as value:
            yield value
    app.dependency_overrides[get_qwen_http_client] = client


class RecordedApp:
    def __init__(self, application, directory: Path, ledger: Ledger):
        self.application, self.directory, self.ledger = application, directory, ledger

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") != "/knowledge/answer" or scope.get("method") != "POST":
            return await self.application(scope, receive, send)
        state = {"operation": uuid.uuid4().hex, "evidence_type": "real_official_corpus_and_real_qwen_or_local_no_match",
                 "corpus_version": build_index().corpus.corpus_version, "corpus_hash": build_index().hash}
        token = context.set(state); incoming = bytearray(); outgoing = bytearray(); start = time.monotonic()
        async def read():
            message = await receive()
            if message["type"] == "http.request":
                incoming.extend(message.get("body", b""))
                if len(incoming) <= 4096 and not message.get("more_body"):
                    try:
                        q = json.loads(incoming)
                        state["request_id"] = q.get("request_id"); state["question"] = q.get("question"); state["venue_id"] = q.get("venue_id")
                    except (ValueError, AttributeError): pass
            return message
        async def write(message):
            if message["type"] == "http.response.start": state["http_status"] = message["status"]
            if message["type"] == "http.response.body" and len(outgoing) <= 65536: outgoing.extend(message.get("body", b""))
            await send(message)
        try: await self.application(scope, read, write)
        finally:
            state["elapsed_seconds"] = round(time.monotonic()-start, 3)
            try: state["response"] = json.loads(outgoing)
            except ValueError: state["response"] = None
            self.directory.mkdir(parents=True, exist_ok=True)
            with (self.directory / f"{state['operation']}.json").open("x", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2); f.flush(); os.fsync(f.fileno())
            self.ledger.append({"event": "completed", "operation": state["operation"], "attempt": state.get("attempt"),
                                "http_status": state.get("http_status"), "model_error": state.get("model_error"),
                                "elapsed_seconds": state["elapsed_seconds"]})
            context.reset(token)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, required=True)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.limit <= 20: parser.error("6D-2 authorization allows at most 20")
    ledger = Ledger(EVIDENCE / "calls.jsonl", args.limit)
    install(ledger)
    uvicorn.run(RecordedApp(app, EVIDENCE / "responses", ledger), host="127.0.0.1", port=args.port, access_log=False)

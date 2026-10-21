"""One authorized, fixed-directory A/B experiment. Not a production endpoint.

init freezes inputs without sending; next performs at most ONE operation. No
resume override, retries, alternative directory or allowance argument exists.
Run with -B. JSON evidence contains public corpus/model selections, never headers.
"""
import argparse
import asyncio
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Iterator

import httpx
from pydantic import ValidationError

from core.config import Settings, get_settings
from tooling.providers.qwen import MODEL, QwenClient, QwenError, configured_endpoint, strict_json
from core.schemas.knowledge_answer import AnswerResponse, PROMPT_VERSION
from tooling.travel.knowledge import fingerprint
from tooling.travel.knowledge_answer import render_answer
from tools.knowledge_acceptance import CountedTransport, Ledger, context
from tools.knowledge_evidence_trial import EVIDENCE, VERSION, audit, prepare, render_selection, sha

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = EVIDENCE / "ab-trial"
BATCH = "6d2-paired-20261014-authorized20-v1"
LIMIT = 20
PROTOCOL = ROOT / "docs/KNOWLEDGE_6D2_AB_PROTOCOL.md"
QUALITY_ERRORS = {"structure", "references", "empty", "truncated", "refused"}


class TrialStopped(Exception):
    """Fixed safe messages only; never propagate upstream exceptions to logs."""


def wire_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@contextmanager
def locked(path: Path) -> Iterator[None]:
    with path.open("a+", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def create_json(path: Path, data: object) -> None:
    # Exclusive creation: never overwrite a frozen protocol or recorded result.
    with path.open("x", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())


def build_frozen() -> dict:
    checked, saved = audit()
    settings = get_settings()
    if settings.qwen_model != MODEL:
        raise TrialStopped("Configured model differs from fixed experiment")
    endpoint_hash = fingerprint(configured_endpoint(settings))
    operations = []
    for index, (case, record) in enumerate(saved.items()):
        packet = prepare(record)
        response = record["response"]
        for arm in (["A", "B"] if index % 2 == 0 else ["B", "A"]):
            payload = packet["arms"].get("baseline" if arm == "A" else "selection")
            operations.append({
                "operation": f"{BATCH}:{case}:{arm}", "case": case, "arm": arm,
                "request_id": f"ab-{case}-{arm}", "question": response["request"]["question"],
                "venue_id": response["request"]["venue_id"], "requires_model": packet["requires_model"],
                "prompt_version": PROMPT_VERSION if arm == "A" else VERSION,
                "prompt_hash": fingerprint(payload["messages"][0]["content"]) if payload else None,
                "contract_hash": fingerprint(payload["response_format"]) if payload else None,
                "payload_hash": wire_hash(payload) if payload else None,
                "retrieval_hash": packet["retrieval_hash"], "payload": payload,
                "context": response,
            })
    files = [*ROOT.glob("backend/app/**/*.py"), *ROOT.glob("backend/tools/knowledge*.py"),
             ROOT / "backend/tests/test_knowledge_ab_trial.py", PROTOCOL,
             ROOT / "backend/knowledge/corpus.json", ROOT / "backend/knowledge/answer-evaluation.json"]
    return {"batch": BATCH, "limit": LIMIT,
            "code_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "files": {str(p.relative_to(ROOT)): sha(p) for p in sorted(files)},
            "old_ledgers": checked["ledgers"], "spec_sha256": checked["spec_sha256"],
            "model": MODEL, "endpoint_hash": endpoint_hash,
            "corpus_hash": checked["corpus_hash"], "corpus_version": "2026-10-14.1",
            "settings": {"sampling": "provider defaults, identical; no temperature/top_p/seed override",
                         "max_tokens": 1024, "deadline_seconds": 20, "max_response_bytes": 32768,
                         "max_content_bytes": 8192, "stream": False, "enable_thinking": False, "retries": 0},
            "operations": operations}


class TrialLedger(Ledger):
    """Reuse fsync/ceiling transport ledger; add durable identity under a gate lock."""
    def __init__(self, directory: Path, frozen: dict) -> None:
        super().__init__(directory / "calls.jsonl", LIMIT, BATCH)
        self.directory, self.frozen = directory, frozen
        self.operations = {op["operation"]: op for op in frozen["operations"]}

    def rows(self) -> list[dict]:
        try:
            lines = self.path.read_text().splitlines()
            rows = [strict_json(line) for line in lines]
            if not rows or rows[0] != self.init_record:
                raise ValueError("Invalid initialization")
            attempts = [r for r in rows if r["event"] == "attempt"]
            if ([r["ordinal"] for r in attempts] != list(range(1, len(attempts)+1))
                    or len(attempts) > LIMIT or len({r["operation"] for r in attempts}) != len(attempts)):
                raise ValueError("Invalid attempts")
            for row in rows[1:]:
                if row["event"] not in {"attempt", "result", "paused"} or row["stage"] != BATCH:
                    raise ValueError("Invalid event")
                if row["event"] == "attempt":
                    op = self.operations[row["operation"]]
                    if not op["requires_model"] or row["payload_hash"] != op["payload_hash"]:
                        raise ValueError("Mismatched attempt")
            return rows
        except (ValueError, KeyError, TypeError, OSError):
            raise TrialStopped("Ledger missing/corrupt; no calls allowed") from None

    @property
    def init_record(self) -> dict:
        return {"event": "initialized", "batch": BATCH, "frozen_hash": fingerprint(self.frozen), "limit": LIMIT}

    def append(self, record: dict, reserve: bool = False) -> dict:
        with locked(self.directory / "ledger.lock"):
            rows = self.rows()
            if reserve:
                op = self.operations.get(record.get("operation"))
                if (not op or not op["requires_model"] or record.get("payload_hash") != op["payload_hash"]
                        or any(r["event"] == "paused" for r in rows)
                        or any(r.get("operation") == record["operation"] for r in rows[1:])):
                    raise TrialStopped("Duplicate, paused or mismatched operation; no send")
                record = {**record, **{key: op[key] for key in ["case", "arm", "prompt_version", "prompt_hash", "contract_hash", "retrieval_hash"]},
                          "batch": BATCH, "model": MODEL, "corpus_hash": self.frozen["corpus_hash"],
                          "code_head": self.frozen["code_head"], "frozen_hash": fingerprint(self.frozen)}
            return super().append(record, reserve)


def initialize(directory: Path, frozen: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    with locked(directory / "run.lock"):
        manifest = directory / "frozen.json"
        if manifest.exists():
            if strict_json(manifest.read_bytes()) != frozen:
                raise TrialStopped("Frozen experiment changed; no reset allowed")
            TrialLedger(directory, frozen).rows()  # Missing ledger cannot recreate allowance.
            return
        if (directory / "calls.jsonl").exists():
            raise TrialStopped("Ledger without manifest; manual audit required")
        create_json(manifest, frozen)
        ledger = TrialLedger(directory, frozen)
        with ledger.path.open("x", encoding="utf-8") as stream:
            os.chmod(ledger.path, 0o600)
            stream.write(json.dumps(ledger.init_record)+"\n")
            stream.flush()
            os.fsync(stream.fileno())


async def perform(op: dict, frozen: dict, ledger: TrialLedger, settings: Settings,
                  transport: httpx.AsyncBaseTransport) -> dict:
    """Caller serializes operations. All real HTTP passes CountedTransport."""
    start = time.monotonic()
    record = {k: v for k, v in op.items() if k not in {"payload", "context"}}
    record.update(event="result", model=MODEL if op["requires_model"] else None,
                  corpus_hash=frozen["corpus_hash"], code_head=frozen["code_head"], frozen_hash=fingerprint(frozen),
                  model_output=None, rendered=None, usage={"input_tokens": None, "output_tokens": None},
                  result="local_no_match", error=None, model_operation_seconds=None)
    response = AnswerResponse.model_validate_json(json.dumps(op["context"]))
    state = {"operation": op["operation"], "request_id": op["request_id"]}
    token = context.set(state)
    try:
        if not op["requires_model"]:
            record["rendered"] = response.model_dump(mode="json")
        else:
            async with httpx.AsyncClient(transport=CountedTransport(transport, ledger), follow_redirects=False) as http:
                model_start = time.monotonic()
                try:
                    content, usage = await QwenClient(settings, http).generate(op["payload"])
                finally:
                    record["model_operation_seconds"] = round(time.monotonic()-model_start, 4)
                record.update(model_output=content, usage=usage.model_dump())
                request = response.request.model_copy(update={"request_id": op["request_id"]})
                rendered = (render_answer(content, request, response.retrieval, MODEL, usage).model_dump(mode="json")
                            if op["arm"] == "A" else render_selection(content, response.retrieval))
                record.update(rendered=rendered, result="structured_output")
    except QwenError as error:
        record.update(error=error.code, result="quality_failure" if error.code in QUALITY_ERRORS and state.get("attempt") else "execution_failure")
    except (ValidationError, ValueError):
        record.update(error="structure", result="quality_failure" if state.get("attempt") else "execution_failure")
    except asyncio.CancelledError:
        record.update(error="cancelled", result="execution_failure")
    except Exception:
        record.update(error="recorder_or_execution", result="execution_failure")
    finally:
        context.reset(token)
    record.update(attempt=state.get("attempt"), elapsed_seconds=round(time.monotonic()-start, 4))
    # A recording failure propagates: the already durable attempt cannot be retried.
    result = ledger.append(record)
    if record["result"] == "execution_failure":
        ledger.append({"event": "paused", "operation": op["operation"], "reason": record["error"]})
    return result


def pending(ledger: TrialLedger) -> dict | None:
    rows = ledger.rows()
    if any(r["event"] == "paused" for r in rows):
        raise TrialStopped("Execution paused; explicit new direction needed, no auto-resume")
    results = [r for r in rows if r["event"] == "result"]
    operations = ledger.frozen["operations"]
    if [r["operation"] for r in results] != [o["operation"] for o in operations[:len(results)]]:
        raise TrialStopped("Result order/identity mismatch")
    if any(r["event"] == "attempt" and r["operation"] not in {v["operation"] for v in results} for r in rows):
        raise TrialStopped("Attempt without durable result; pause, never repeat")
    if any(r["result"] == "execution_failure" for r in results):
        raise TrialStopped("Recorded execution failure; no auto-resume")
    return operations[len(results)] if len(results) < len(operations) else None


async def next_operation(directory: Path, frozen: dict, settings: Settings,
                         transport: httpx.AsyncBaseTransport) -> dict:
    with locked(directory / "run.lock"):
        if strict_json((directory / "frozen.json").read_bytes()) != frozen:
            raise TrialStopped("Frozen input/code/old evidence changed; no send")
        ledger = TrialLedger(directory, frozen)
        op = pending(ledger)
        if op is None:
            return {"result": "completed", "attempts": sum(r["event"] == "attempt" for r in ledger.rows())}
        try:
            configured_endpoint(settings)
            if settings.qwen_model != MODEL:
                raise QwenError("configuration")
        except QwenError:
            ledger.append({"event": "paused", "operation": op["operation"], "reason": "configuration"})
            raise TrialStopped("Configuration mismatch; paused before sending") from None
        return await perform(op, frozen, ledger, settings, transport)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "next", "status"])
    args = parser.parse_args()
    try:
        if args.command == "status":
            frozen = strict_json((DIRECTORY / "frozen.json").read_bytes())
            rows = TrialLedger(DIRECTORY, frozen).rows()
            print(json.dumps({"attempts": sum(r["event"] == "attempt" for r in rows), "limit": LIMIT,
                              "results": [{k: r.get(k) for k in ["case", "arm", "result", "error", "attempt"]} for r in rows if r["event"] == "result"],
                              "paused": any(r["event"] == "paused" or r.get("result") == "execution_failure" for r in rows)}))
            return
        frozen = build_frozen()
        if args.command == "init":
            initialize(DIRECTORY, frozen)
            print(json.dumps({"frozen_hash": fingerprint(frozen), "operations": len(frozen["operations"]), "limit": LIMIT, "calls": 0}))
            return
        settings = get_settings()
        result = asyncio.run(next_operation(DIRECTORY, frozen, settings, httpx.AsyncHTTPTransport(retries=0)))
        print(json.dumps({k: result.get(k) for k in ["case", "arm", "result", "error", "attempt", "usage", "elapsed_seconds"]}))
    except (TrialStopped, QwenError, OSError, ValueError):
        # No exception repr, authentication headers or full upstream URL.
        print(json.dumps({"result": "stopped", "notice": "No automatic retry. Audit frozen files, durable ledger and sanitized results before any further operation."}))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()

"""Fixed eight-operation Plus gap trial; init/status do not send. No retry/reset."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import time

import httpx
from pydantic import ValidationError

from core.config import Settings, get_settings
from tooling.providers.qwen import QwenClient, QwenError, configured_endpoint, strict_json
from core.schemas.knowledge import KnowledgeResponse
from core.schemas.knowledge_answer import AnswerRequest
from core.schemas.explanation import ExplanationUsage
from tooling.travel.knowledge import fingerprint
from tools.knowledge_ab_trial import QUALITY_ERRORS, TrialStopped, create_json, locked, pending, wire_hash
from tools.knowledge_acceptance import CountedTransport, Ledger, context
from tools.knowledge_evidence_trial import EVIDENCE, sha
from tools.knowledge_model_selection import ROOT, FLASH, ObservedTransport, preserved as previous_preserved
from tools.knowledge_plus_gap_candidate import MODEL, VERSION, SYSTEM, BoundQuestion, bind, payload_for, render

DIRECTORY = EVIDENCE / "plus-gap-validation"
BATCH = "6d2-plus-gaps-20261014-authorized8-v1"
LIMIT = 8
SPEC = ROOT / "backend/knowledge/answer-plus-gap-questions.json"
PROTOCOL = ROOT / "docs/KNOWLEDGE_6D2_PLUS_GAP_PROTOCOL.md"


def preserved() -> dict:
    proof = previous_preserved()
    folder = EVIDENCE / "model-selection"
    receipt = strict_json((folder / "receipt.json").read_bytes())
    if any(sha(folder / name) != h for name, h in receipt["artifact_sha256"].items()):
        raise TrialStopped("Previous model trial evidence changed")
    old = strict_json((folder / "frozen.json").read_bytes())
    if any(sha(ROOT / name) != h for name, h in old["files"].items()):
        raise TrialStopped("Previous candidate or production changed")
    rows = [strict_json(line) for line in (folder / "calls.jsonl").read_text().splitlines()]
    if [r["ordinal"] for r in rows if r["event"] == "attempt"] != list(range(1, 21)):
        raise TrialStopped("Previous model counter changed")
    return {**proof, "model_selection_receipt_sha256": sha(folder / "receipt.json"),
            "model_selection_artifacts": receipt["artifact_sha256"], "model_selection_attempts": 20}


def captured(op: dict) -> BoundQuestion:
    bound = bind(AnswerRequest.model_validate_json(json.dumps(op["request"])),
                 KnowledgeResponse.model_validate_json(json.dumps(op["retrieval"])))
    if bound.identity != op["binding_hash"]:
        raise TrialStopped("Request/candidate binding changed")
    return bound


def make_operations(questions: dict) -> list[dict]:
    operations = []
    for repetition in (1, 2):
        for case in questions["cases"]:
            req = AnswerRequest.model_validate_json(json.dumps(case["request"]))
            request_id = f"gap-{case['expectation']['id']}-r{repetition}"
            req = req.model_copy(update={"request_id": request_id})
            ret = KnowledgeResponse.model_validate_json(json.dumps(case["retrieval"]))
            bound = bind(req, ret)
            payload = payload_for(bound)
            if payload["messages"][1]["content"] != case["visible_content"]:
                raise TrialStopped("Frozen visible evidence changed")
            baseline = dict(case["old_payload"], model=MODEL)
            baseline["messages"] = [dict(baseline["messages"][0], content=SYSTEM), *baseline["messages"][1:]]
            if payload != baseline:
                raise TrialStopped("Changes beyond missing prompt")
            operations.append({"operation": f"{BATCH}:{case['expectation']['id']}:{MODEL}:r{repetition}",
                "case": case["expectation"]["id"], "repetition": repetition, "model": MODEL, "requires_model": True,
                "request_id": request_id, "request": req.model_dump(mode="json"), "retrieval": ret.model_dump(mode="json"),
                "binding_hash": bound.identity, "payload": payload, "payload_hash": wire_hash(payload),
                "prompt_version": VERSION, "prompt_hash": fingerprint(SYSTEM),
                "contract_hash": fingerprint(payload["response_format"]), "retrieval_hash": fingerprint(ret.model_dump(mode="json"))})
    return operations


def build_frozen() -> dict:
    proof = preserved()
    questions = strict_json((DIRECTORY / "questions-frozen.json").read_bytes())
    if sha(SPEC) != questions["spec_sha256"] or questions["spec"] != strict_json(SPEC.read_bytes()):
        raise TrialStopped("Questions/expectations changed")
    settings = get_settings()
    if settings.qwen_model != FLASH:
        raise TrialStopped("Production configuration changed")
    files = [*ROOT.glob("backend/app/**/*.py"), *ROOT.glob("backend/tools/knowledge*.py"),
             ROOT / "backend/tests/test_knowledge_plus_gap_validation.py", SPEC, PROTOCOL,
             ROOT / "backend/knowledge/corpus.json", ROOT / "backend/knowledge/answer-evaluation.json"]
    prior = strict_json((EVIDENCE / "model-selection/frozen.json").read_bytes())
    return {"batch": BATCH, "limit": LIMIT, "model": MODEL, "candidate_version": VERSION,
            "code_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "files": {str(p.relative_to(ROOT)): sha(p) for p in sorted(files)}, "preserved": proof,
            "questions_sha256": sha(DIRECTORY / "questions-frozen.json"), "expectations": questions["spec"],
            "corpus_hash": questions["corpus_hash"], "corpus_version": prior["corpus_version"],
            "endpoint_hash": fingerprint(configured_endpoint(settings)), "settings": prior["settings"],
            "operations": make_operations(questions)}


class GapLedger(Ledger):
    def __init__(self, directory: Path, frozen: dict) -> None:
        super().__init__(directory / "calls.jsonl", LIMIT, BATCH)
        self.directory, self.frozen = directory, frozen
        self.operations = {op["operation"]: op for op in frozen["operations"]}

    @property
    def init_record(self) -> dict:
        return {"event": "initialized", "batch": BATCH, "limit": LIMIT, "frozen_hash": fingerprint(self.frozen)}

    def rows(self) -> list[dict]:
        try:
            rows = [strict_json(line) for line in self.path.read_text().splitlines()]
            if not rows or rows[0] != self.init_record:
                raise ValueError("Initialization mismatch")
            attempts = [r for r in rows if r["event"] == "attempt"]
            if (len(attempts) > LIMIT or [r["ordinal"] for r in attempts] != list(range(1, len(attempts)+1))
                    or len({r["operation"] for r in attempts}) != len(attempts)):
                raise ValueError("Invalid counter")
            for r in rows[1:]:
                op = self.operations[r["operation"]]
                if r["stage"] != BATCH or r["event"] not in {"attempt", "result", "paused"}:
                    raise ValueError("Invalid event")
                if r["event"] == "attempt" and any(r.get(k) != op[k] for k in
                        ["request_id", "payload_hash", "model", "repetition", "binding_hash"]):
                    raise ValueError("Invalid identity")
            return rows
        except (OSError, ValueError, KeyError, TypeError):
            raise TrialStopped("Ledger missing/corrupt; no send") from None

    def append(self, record: dict, reserve: bool = False) -> dict:
        with locked(self.directory / "ledger.lock"):
            rows = self.rows()
            if reserve:
                op = self.operations.get(record.get("operation"))
                if (not op or any(record.get(k) != op[k] for k in ["request_id", "payload_hash"])
                        or any(r["event"] == "paused" for r in rows)
                        or any(r.get("operation") == record["operation"] for r in rows[1:])):
                    raise TrialStopped("Duplicate, paused or mismatched operation")
                record = {**record, **{k: op[k] for k in ["case", "model", "repetition", "binding_hash", "prompt_version",
                          "prompt_hash", "contract_hash", "retrieval_hash"]}, "frozen_hash": fingerprint(self.frozen),
                          "code_head": self.frozen["code_head"], "corpus_hash": self.frozen["corpus_hash"]}
            return super().append(record, reserve)


def initialize(directory: Path, frozen: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    with locked(directory / "run.lock"):
        if (directory / "frozen.json").exists():
            if strict_json((directory / "frozen.json").read_bytes()) != frozen:
                raise TrialStopped("Frozen input differs")
            GapLedger(directory, frozen).rows()
            return
        if (directory / "calls.jsonl").exists():
            raise TrialStopped("Ledger without manifest")
        create_json(directory / "frozen.json", frozen)
        ledger = GapLedger(directory, frozen)
        with ledger.path.open("x", encoding="utf-8") as stream:
            os.chmod(ledger.path, 0o600)
            stream.write(json.dumps(ledger.init_record)+"\n"); stream.flush(); os.fsync(stream.fileno())


async def generate(bound: BoundQuestion, payload: dict, settings: Settings,
                   client: httpx.AsyncClient) -> tuple[object, ExplanationUsage]:
    configured_endpoint(settings)
    if settings.qwen_model != FLASH or payload != payload_for(bound):
        raise TrialStopped("Model/candidate payload differs")
    return await QwenClient(settings, client).generate(payload)


async def perform(op: dict, frozen: dict, ledger: GapLedger, settings: Settings,
                  transport: httpx.AsyncBaseTransport) -> dict:
    bound = captured(op)
    if op["model"] != MODEL or op["payload"] != payload_for(bound):
        raise TrialStopped("Frozen payload mismatch")
    record = {k: v for k, v in op.items() if k not in {"request", "retrieval", "payload"}}
    record.update(event="result", model_output=None, rendered=None, error=None,
                  result="structured_output", frozen_hash=fingerprint(frozen))
    metadata = {"reported_model": None, "usage": {"input_tokens": None, "output_tokens": None}}
    state = {"operation": op["operation"], "request_id": op["request_id"]}
    token = context.set(state); start = time.monotonic()
    try:
        observer = ObservedTransport(transport, metadata)
        async with httpx.AsyncClient(transport=CountedTransport(observer, ledger), follow_redirects=False) as client:
            content, usage = await generate(bound, op["payload"], settings, client)
            record["model_output"] = content
            metadata["usage"] = usage.model_dump()
            if any(sha(ROOT / name) != h for name, h in frozen.get("files", {}).items()):
                raise TrialStopped("Frozen code changed during request")
            current = captured(op)
            if current != bound:
                raise TrialStopped("Request context changed during await")
            record["rendered"] = render(content, bound, current)
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
    if metadata["reported_model"] not in {None, MODEL, "qwen3.7-plus"}:
        record.update(error="reported_model_mismatch", result="execution_failure")
    record.update(**metadata, attempt=state.get("attempt"), elapsed_seconds=round(time.monotonic()-start, 4))
    saved = ledger.append(record)
    if record["result"] == "execution_failure":
        ledger.append({"event": "paused", "operation": op["operation"], "reason": record["error"]})
    return saved


async def next_operation(directory: Path, frozen: dict, settings: Settings,
                         transport: httpx.AsyncBaseTransport) -> dict:
    with locked(directory / "run.lock"):
        if (directory / "halt.json").exists(): raise TrialStopped("Paused")
        if strict_json((directory / "frozen.json").read_bytes()) != frozen:
            create_json(directory / "halt.json", {"reason": "frozen_mismatch"})
            raise TrialStopped("Frozen version differs")
        ledger = GapLedger(directory, frozen)
        op = pending(ledger)
        if op is None:
            return {"result": "completed", "attempts": sum(r["event"] == "attempt" for r in ledger.rows())}
        try:
            if settings.qwen_model != FLASH or fingerprint(configured_endpoint(settings)) != frozen["endpoint_hash"]:
                raise TrialStopped("Configuration differs")
            return await perform(op, frozen, ledger, settings, transport)
        except (QwenError, TrialStopped, ValueError):
            ledger.append({"event": "paused", "operation": op["operation"], "reason": "preflight"})
            raise TrialStopped("Preflight failed; no automatic resume") from None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "next", "status"])
    args = parser.parse_args()
    try:
        if args.command == "status":
            frozen = strict_json((DIRECTORY / "frozen.json").read_bytes())
            rows = GapLedger(DIRECTORY, frozen).rows()
            print(json.dumps({"attempts": sum(r["event"] == "attempt" for r in rows), "limit": LIMIT,
                              "results": [{k: r.get(k) for k in ["case", "repetition", "result", "error", "attempt"]} for r in rows if r["event"] == "result"],
                              "paused": (DIRECTORY / "halt.json").exists() or any(r["event"] == "paused" for r in rows)}))
            return
        if (DIRECTORY / "halt.json").exists(): raise TrialStopped("Preflight previously failed")
        frozen = build_frozen()
        if args.command == "init":
            initialize(DIRECTORY, frozen)
            print(json.dumps({"frozen_hash": fingerprint(frozen), "operations": len(frozen["operations"]), "calls": 0}))
            return
        result = asyncio.run(next_operation(DIRECTORY, frozen, get_settings(), httpx.AsyncHTTPTransport(retries=0)))
        print(json.dumps({k: result.get(k) for k in ["case", "model", "repetition", "result", "error", "attempt", "usage", "elapsed_seconds"]}))
    except (TrialStopped, QwenError, OSError, ValueError):
        if args.command != "status" and (DIRECTORY / "frozen.json").exists() and not (DIRECTORY / "halt.json").exists():
            try: create_json(DIRECTORY / "halt.json", {"reason": "preflight_or_execution"})
            except OSError: pass
        print(json.dumps({"result": "stopped", "notice": "No retries or automatic resume; audit ledger."}))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()

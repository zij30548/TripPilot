"""Fixed B-v2 validation, authorized 16 calls. init/next/status; no reset/retry.

Uses the existing bounded Qwen client and fsync-before-send transport. CLI has
no path, model, limit, resume or case override. Run with -B, never via the API.
"""
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
from tooling.providers.qwen import MODEL, QwenClient, QwenError, configured_endpoint, strict_json
from core.schemas.knowledge import KnowledgeRequest, KnowledgeResponse
from core.schemas.knowledge_answer import AnswerRequest
from tooling.travel.knowledge import DATA, build_index, fingerprint
from tools.knowledge_ab_trial import QUALITY_ERRORS, TrialStopped, create_json, locked, pending, wire_hash
from tools.knowledge_acceptance import CountedTransport, Ledger, context
from tools.knowledge_b_candidate import VERSION, bind, payload_for, render
from tools.knowledge_evidence_trial import EVIDENCE, audit, sha

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = EVIDENCE / "b-candidate-validation"
BATCH = "6d2-b-v2-20261014-authorized16-v1"
LIMIT = 16
PROTOCOL = ROOT / "docs/KNOWLEDGE_6D2_B_PROTOCOL.md"
SPEC = DATA / "answer-b-validation-questions.json"


def build_frozen() -> dict:
    checked, saved = audit()
    questions = strict_json((DIRECTORY / "questions-freeze.json").read_bytes())
    if sha(SPEC) != questions["new_spec_sha256"] or questions["candidate_implementation_exists"]:
        raise TrialStopped("New questions must precede candidate edits")
    for name, digest in questions["prior_files"].items():
        if sha(ROOT / name) != digest:
            raise TrialStopped("Old production/candidate/corpus changed")
    for name, digest in questions["old_ledgers"].items():
        if sha(EVIDENCE / name) != digest:
            raise TrialStopped("Old ledger changed")
    old_rows = [strict_json(line) for line in (EVIDENCE / "ab-trial/calls.jsonl").read_text().splitlines()]
    if sum(r["event"] == "attempt" for r in old_rows) != 20:
        raise TrialStopped("Prior AB count changed")
    settings = get_settings()
    if settings.qwen_model != MODEL:
        raise TrialStopped("Model differs from fixed experiment")
    index = build_index()
    operations = []
    original = strict_json((DATA / "answer-evaluation.json").read_bytes())["cases"]
    new = strict_json(SPEC.read_bytes())["cases"]
    for group, cases in [("original", original), ("new", new)]:
        for case in cases:
            retrieval = index.search(KnowledgeRequest(venue_id=case["venue_id"], question=case["question"], limit=5))
            if group == "original" and retrieval.model_dump(mode="json") != saved[case["id"]]["response"]["retrieval"]:
                raise TrialStopped("Original retrieval changed")
            request = AnswerRequest(venue_id=case["venue_id"], question=case["question"], request_id=f"bv2-{case['id']}",
                                    corpus_version=retrieval.corpus_version, corpus_hash=retrieval.corpus_hash)
            bound = bind(request, retrieval)
            payload = payload_for(bound) if retrieval.matches else None
            operations.append({"operation": f"{BATCH}:{case['id']}:B-v2", "case": case["id"], "group": group,
                               "arm": "B-v2", "request_id": request.request_id,
                               "request": request.model_dump(mode="json"), "retrieval": retrieval.model_dump(mode="json"),
                               "binding_hash": bound.identity, "retrieval_hash": fingerprint(retrieval.model_dump(mode="json")),
                               "requires_model": bool(payload), "payload": payload,
                               "payload_hash": wire_hash(payload) if payload else None, "prompt_version": VERSION,
                               "prompt_hash": fingerprint(payload["messages"][0]["content"]) if payload else None,
                               "contract_hash": fingerprint(payload["response_format"]) if payload else None})
    if (len(original), len(new), sum(o["requires_model"] for o in operations)) != (12, 6, 16):
        raise TrialStopped("Question/call plan mismatch")
    if [o["case"] for o in operations if not o["requires_model"]] != ["a08", "a09"]:
        raise TrialStopped("Unexpected local cases")
    files = [*ROOT.glob("backend/app/**/*.py"), *ROOT.glob("backend/tools/knowledge*.py"),
             ROOT / "backend/tests/test_knowledge_b_validation.py", PROTOCOL, SPEC,
             DATA / "corpus.json", DATA / "answer-evaluation.json"]
    return {"batch": BATCH, "limit": LIMIT, "candidate_version": VERSION,
            "code_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "files": {str(p.relative_to(ROOT)): sha(p) for p in sorted(files)},
            "old_ledgers": questions["old_ledgers"], "old_audit": checked["ledgers"],
            "questions_freeze_hash": sha(DIRECTORY / "questions-freeze.json"),
            "model": MODEL, "endpoint_hash": fingerprint(configured_endpoint(settings)),
            "corpus_hash": index.hash, "corpus_version": "2026-10-14.1",
            "settings": {"sampling": "provider defaults, unchanged", "max_tokens": 1024, "deadline_seconds": 20,
                         "max_response_bytes": 32768, "max_content_bytes": 8192,
                         "stream": False, "enable_thinking": False, "retries": 0}, "operations": operations}


class ValidationLedger(Ledger):
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
                raise ValueError("Counter mismatch")
            for row in rows[1:]:
                if row["event"] not in {"attempt", "result", "paused"} or row["stage"] != BATCH:
                    raise ValueError("Event mismatch")
                op = self.operations[row["operation"]]
                if row["event"] == "attempt" and (not op["requires_model"] or any(
                    row.get(key) != op[key] for key in ["payload_hash", "request_id", "binding_hash", "prompt_version", "arm"])):
                    raise ValueError("Attempt identity mismatch")
            return rows
        except (ValueError, KeyError, TypeError, OSError):
            raise TrialStopped("Ledger missing/corrupt; no sends") from None

    def append(self, record: dict, reserve: bool = False) -> dict:
        with locked(self.directory / "ledger.lock"):
            rows = self.rows()
            if reserve:
                op = self.operations.get(record.get("operation"))
                if (not op or not op["requires_model"] or record.get("payload_hash") != op["payload_hash"]
                        or record.get("request_id") != op["request_id"]
                        or any(r["event"] == "paused" for r in rows)
                        or any(r.get("operation") == record["operation"] for r in rows[1:])):
                    raise TrialStopped("Duplicate, paused or mismatched operation")
                record = {**record, **{k: op[k] for k in ["case", "group", "arm", "prompt_version", "prompt_hash", "contract_hash", "retrieval_hash", "binding_hash"]},
                          "model": MODEL, "corpus_hash": self.frozen["corpus_hash"],
                          "code_head": self.frozen["code_head"], "frozen_hash": fingerprint(self.frozen)}
            return super().append(record, reserve)


def initialize(directory: Path, frozen: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    with locked(directory / "run.lock"):
        manifest = directory / "frozen.json"
        if manifest.exists():
            if strict_json(manifest.read_bytes()) != frozen:
                raise TrialStopped("Frozen experiment changed")
            ValidationLedger(directory, frozen).rows()
            return
        if (directory / "calls.jsonl").exists():
            raise TrialStopped("Ledger without manifest")
        create_json(manifest, frozen)
        ledger = ValidationLedger(directory, frozen)
        with ledger.path.open("x", encoding="utf-8") as stream:
            os.chmod(ledger.path, 0o600)
            stream.write(json.dumps(ledger.init_record)+"\n"); stream.flush(); os.fsync(stream.fileno())


async def perform(op: dict, frozen: dict, ledger: ValidationLedger, settings: Settings,
                  transport: httpx.AsyncBaseTransport) -> dict:
    start = time.monotonic()
    # Do not classify broken captured evidence/configuration as a model failure.
    request = AnswerRequest.model_validate_json(json.dumps(op["request"]))
    retrieval = KnowledgeResponse.model_validate_json(json.dumps(op["retrieval"]))
    captured = bind(request, retrieval)
    if captured.identity != op["binding_hash"]:
        raise TrialStopped("Captured binding mismatch")
    record = {k: v for k, v in op.items() if k not in {"payload", "retrieval", "request"}}
    record.update(event="result", model=MODEL if op["requires_model"] else None,
                  frozen_hash=fingerprint(frozen), model_output=None, rendered=None,
                  usage={"input_tokens": None, "output_tokens": None}, result="local_no_match", error=None)
    state = {"operation": op["operation"], "request_id": op["request_id"]}
    token = context.set(state)
    try:
        if not op["requires_model"]:
            record["rendered"] = render({"evidence_ids": [], "missing": ["not_in_materials"]}, captured, captured)
        else:
            async with httpx.AsyncClient(transport=CountedTransport(transport, ledger), follow_redirects=False) as http:
                content, usage = await QwenClient(settings, http).generate(op["payload"])
                record.update(model_output=content, usage=usage.model_dump())
                # Recheck immutable server context and canonical retrieval after the await.
                try:
                    current = bind(request, retrieval)
                except ValueError:
                    raise TrialStopped("Evidence changed during request") from None
                record.update(rendered=render(content, captured, current), result="structured_output")
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
    result = ledger.append(record)
    if record["result"] == "execution_failure":
        ledger.append({"event": "paused", "operation": op["operation"], "reason": record["error"]})
    return result


async def next_operation(directory: Path, frozen: dict, settings: Settings,
                         transport: httpx.AsyncBaseTransport) -> dict:
    with locked(directory / "run.lock"):
        if (directory / "halt.json").exists():
            raise TrialStopped("Preflight previously failed; no automatic resume")
        if strict_json((directory / "frozen.json").read_bytes()) != frozen:
            create_json(directory / "halt.json", {"reason": "frozen_mismatch", "calls_permitted": False})
            raise TrialStopped("Frozen version changed; no send")
        ledger = ValidationLedger(directory, frozen)
        op = pending(ledger)
        if op is None:
            return {"result": "completed", "attempts": sum(r["event"] == "attempt" for r in ledger.rows())}
        try:
            if settings.qwen_model != MODEL or fingerprint(configured_endpoint(settings)) != frozen["endpoint_hash"]:
                raise QwenError("configuration")
            return await perform(op, frozen, ledger, settings, transport)
        except (QwenError, TrialStopped, ValueError):
            ledger.append({"event": "paused", "operation": op["operation"], "reason": "configuration_or_binding"})
            raise TrialStopped("Configuration or evidence mismatch; paused") from None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "next", "status"])
    args = parser.parse_args()
    try:
        if args.command == "status":
            frozen = strict_json((DIRECTORY / "frozen.json").read_bytes())
            rows = ValidationLedger(DIRECTORY, frozen).rows()
            print(json.dumps({"attempts": sum(r["event"] == "attempt" for r in rows), "limit": LIMIT,
                              "results": [{k: r.get(k) for k in ["case", "group", "result", "error", "attempt"]} for r in rows if r["event"] == "result"],
                              "paused": (DIRECTORY / "halt.json").exists() or any(r["event"] == "paused" or r.get("result") == "execution_failure" for r in rows)}))
            return
        if (DIRECTORY / "halt.json").exists():
            raise TrialStopped("Preflight previously failed")
        frozen = build_frozen()
        if args.command == "init":
            initialize(DIRECTORY, frozen)
            print(json.dumps({"frozen_hash": fingerprint(frozen), "operations": len(frozen["operations"]), "limit": LIMIT, "calls": 0}))
            return
        result = asyncio.run(next_operation(DIRECTORY, frozen, get_settings(), httpx.AsyncHTTPTransport(retries=0)))
        print(json.dumps({k: result.get(k) for k in ["case", "group", "result", "error", "attempt", "usage", "elapsed_seconds"]}))
    except (TrialStopped, QwenError, OSError, ValueError):
        # A preflight failure must not become resumable just by reverting files.
        if args.command != "status" and (DIRECTORY / "frozen.json").exists() and not (DIRECTORY / "halt.json").exists():
            try:
                create_json(DIRECTORY / "halt.json", {"reason": "preflight_or_execution", "calls_permitted": False})
            except OSError:
                pass  # Still stop; a damaged ledger also fails closed independently.
        print(json.dumps({"result": "stopped", "notice": "No automatic resume. Audit frozen evidence and ledger; no retry."}))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()

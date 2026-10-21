"""Fixed current-candidate regression: 20 known cases, at most 18 counted calls.

No candidate edits or automatic retries. init freezes, next executes one planned
case, status only reads. The fixed batch path cannot be overridden or reset.
"""
import argparse
import asyncio
import copy
import json
import os
from pathlib import Path
import subprocess
import time

import httpx

from core.config import Settings, get_settings
from tooling.providers.qwen import QwenError, configured_endpoint, strict_json
from core.schemas.knowledge import KnowledgeRequest
from core.schemas.knowledge_answer import AnswerRequest
from tooling.travel.knowledge import DATA, build_index, fingerprint
from tools.knowledge_ab_trial import TrialStopped, create_json, locked, pending, wire_hash
from tools.knowledge_acceptance import Ledger
from tools.knowledge_evidence_trial import EVIDENCE, sha
from tools.knowledge_model_selection import ROOT, FLASH
from tools.knowledge_plus_gap_candidate import MODEL, VERSION, bind, payload_for, render
from tools.knowledge_plus_gap_validation import captured, perform as model_perform, preserved as previous_preserved

BATCH = "6d2-plus-full-20261014-authorized18-v1"
DIRECTORY = EVIDENCE / "plus-full-regression/batch-20261014-v1"
LIMIT = 18
RUBRIC = DATA / "answer-plus-full-rubric.json"
PROTOCOL = ROOT / "docs/KNOWLEDGE_6D2_PLUS_FULL_PROTOCOL.md"


def preserved() -> dict:
    proof = previous_preserved()
    folder = EVIDENCE / "plus-gap-validation"
    receipt = strict_json((folder / "receipt.json").read_bytes())
    if any(sha(folder / n) != h for n, h in receipt["artifact_sha256"].items()):
        raise TrialStopped("Previous gap evidence changed")
    old = strict_json((folder / "frozen.json").read_bytes())
    if any(sha(ROOT / n) != h for n, h in old["files"].items()):
        raise TrialStopped("Fixed candidate/code changed")
    rows = [strict_json(line) for line in (folder / "calls.jsonl").read_text().splitlines()]
    if [r["ordinal"] for r in rows if r["event"] == "attempt"] != list(range(1, 9)):
        raise TrialStopped("Previous count changed")
    return {**proof, "gap_receipt_sha256": sha(folder / "receipt.json"),
            "gap_artifacts": receipt["artifact_sha256"], "gap_attempts": 8}


def make_operations(rubric: dict) -> list[dict]:
    cases = {}
    for name in rubric["question_sources"]:
        for case in strict_json((DATA / name).read_bytes())["cases"]:
            # Preserve a01-a12 from their original file, not later annotations.
            cases.setdefault(case["id"], case)
    operations = []
    for number, identifier in enumerate(rubric["order"], 1):
        original = cases[identifier]
        expected = copy.deepcopy(original)
        expected["missing"] = rubric["strict_missing"][identifier]
        if identifier == "b03":
            expected["required"][0] = rubric["clarifications"]["b03"]["required_first"]
        if identifier == "a10":
            expected["status"] = rubric["clarifications"]["a10"]["status"]
        retrieval = build_index().search(KnowledgeRequest(venue_id=original["venue_id"], question=original["question"]))
        req = AnswerRequest(venue_id=original["venue_id"], question=original["question"], request_id=f"pfull-{identifier}-once",
                            corpus_hash=retrieval.corpus_hash, corpus_version=retrieval.corpus_version)
        bound = bind(req, retrieval)
        payload = payload_for(bound) if retrieval.matches else None
        operations.append({"operation": f"{BATCH}:{identifier}:{MODEL}:once", "case": identifier,
            "sequence": number, "repetition": 1, "model": MODEL, "request_id": req.request_id,
            "request": req.model_dump(mode="json"), "retrieval": retrieval.model_dump(mode="json"),
            "binding_hash": bound.identity, "retrieval_hash": fingerprint(retrieval.model_dump(mode="json")),
            "requires_model": payload is not None, "payload": payload, "payload_hash": wire_hash(payload) if payload else None,
            "prompt_version": VERSION, "prompt_hash": fingerprint(payload["messages"][0]["content"]) if payload else None,
            "contract_hash": fingerprint(payload["response_format"]) if payload else None,
            "original_expectation": original, "expectation": expected})
    if (len(operations) != 20 or len({o["case"] for o in operations}) != 20
            or sum(o["requires_model"] for o in operations) != LIMIT
            or [o["case"] for o in operations if not o["requires_model"]] != ["a08", "a09"]):
        raise TrialStopped("Actual branch plan differs; no probes")
    return operations


def build_frozen() -> dict:
    proof = preserved()
    rubric = strict_json(RUBRIC.read_bytes())
    operations = make_operations(rubric)
    old_b = strict_json((EVIDENCE / "b-candidate-validation/frozen.json").read_bytes())
    old_gap = strict_json((EVIDENCE / "plus-gap-validation/frozen.json").read_bytes())
    previous = {o["case"]: o for o in old_b["operations"]}
    previous.update({o["case"]: o for o in old_gap["operations"] if o["case"].startswith("g")})
    if any(o["retrieval"] != previous[o["case"]]["retrieval"] for o in operations):
        raise TrialStopped("Fixed retrieval differs")
    settings = get_settings()
    if settings.qwen_model != FLASH: raise TrialStopped("Production configuration changed")
    files = [*ROOT.glob("backend/app/**/*.py"), *ROOT.glob("backend/tools/knowledge*.py"), RUBRIC, PROTOCOL,
             ROOT / "backend/tests/test_knowledge_plus_full_regression.py", DATA / "corpus.json",
             ROOT / "docs/KNOWLEDGE_6D2_B03_ERRATUM.md", *[DATA / n for n in rubric["question_sources"]]]
    return {"batch": BATCH, "limit": LIMIT, "candidate_version": VERSION, "model": MODEL,
            "code_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "files": {str(p.relative_to(ROOT)): sha(p) for p in sorted(files)}, "preserved": proof,
            "rubric": rubric, "corpus_hash": build_index().hash, "corpus_version": old_gap["corpus_version"],
            "settings": old_gap["settings"], "endpoint_hash": fingerprint(configured_endpoint(settings)), "operations": operations}


class RegressionLedger(Ledger):
    def __init__(self, directory: Path, frozen: dict) -> None:
        super().__init__(directory / "calls.jsonl", LIMIT, BATCH)
        self.directory, self.frozen = directory, frozen
        self.operations = {o["operation"]: o for o in frozen["operations"]}

    @property
    def init_record(self) -> dict:
        return {"event": "initialized", "batch": BATCH, "limit": LIMIT, "frozen_hash": fingerprint(self.frozen)}

    def rows(self) -> list[dict]:
        try:
            rows = [strict_json(s) for s in self.path.read_text().splitlines()]
            if not rows or rows[0] != self.init_record: raise ValueError("Initialization mismatch")
            attempts = [r for r in rows if r["event"] == "attempt"]
            if (len(attempts) > LIMIT or [r["ordinal"] for r in attempts] != list(range(1, len(attempts)+1))
                    or len({r["operation"] for r in attempts}) != len(attempts)):
                raise ValueError("Invalid counter")
            for r in rows[1:]:
                op = self.operations[r["operation"]]
                if r["stage"] != BATCH or r["event"] not in {"attempt", "result", "paused"}: raise ValueError("Invalid event")
                if r["event"] == "attempt" and (not op["requires_model"] or any(r.get(k) != op[k] for k in
                        ["request_id", "payload_hash", "model", "binding_hash", "sequence"])):
                    raise ValueError("Invalid attempt identity")
            return rows
        except (OSError, ValueError, KeyError, TypeError):
            raise TrialStopped("Ledger missing/corrupt; no send") from None

    def append(self, record: dict, reserve: bool = False) -> dict:
        with locked(self.directory / "ledger.lock"):
            rows = self.rows()
            if reserve:
                op = self.operations.get(record.get("operation"))
                if (not op or not op["requires_model"] or any(record.get(k) != op[k] for k in ["request_id", "payload_hash"])
                        or any(r["event"] == "paused" for r in rows)
                        or any(r.get("operation") == record["operation"] for r in rows[1:])):
                    raise TrialStopped("Duplicate, paused or mismatched operation")
                record = {**record, **{k: op[k] for k in ["case", "model", "sequence", "binding_hash", "prompt_version",
                          "prompt_hash", "contract_hash", "retrieval_hash"]}, "frozen_hash": fingerprint(self.frozen),
                          "code_head": self.frozen["code_head"], "corpus_hash": self.frozen["corpus_hash"]}
            return super().append(record, reserve)


def initialize(directory: Path, frozen: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    with locked(directory / "run.lock"):
        if (directory / "frozen.json").exists():
            if strict_json((directory / "frozen.json").read_bytes()) != frozen: raise TrialStopped("Frozen input differs")
            RegressionLedger(directory, frozen).rows()
            return
        if (directory / "calls.jsonl").exists(): raise TrialStopped("Ledger without manifest")
        create_json(directory / "frozen.json", frozen)
        ledger = RegressionLedger(directory, frozen)
        with ledger.path.open("x", encoding="utf-8") as stream:
            os.chmod(ledger.path, 0o600)
            stream.write(json.dumps(ledger.init_record)+"\n"); stream.flush(); os.fsync(stream.fileno())


async def next_operation(directory: Path, frozen: dict, settings: Settings, transport: httpx.AsyncBaseTransport) -> dict:
    with locked(directory / "run.lock"):
        if (directory / "halt.json").exists(): raise TrialStopped("Paused")
        if strict_json((directory / "frozen.json").read_bytes()) != frozen:
            create_json(directory / "halt.json", {"reason": "frozen_mismatch"})
            raise TrialStopped("Frozen version differs")
        ledger = RegressionLedger(directory, frozen)
        op = pending(ledger)
        if op is None: return {"result": "completed", "attempts": sum(r["event"] == "attempt" for r in ledger.rows())}
        try:
            if any(sha(ROOT / n) != h for n, h in frozen.get("files", {}).items()): raise TrialStopped("Files changed")
            bound = captured(op)
            if not op["requires_model"]:
                if op["payload"] is not None or json.loads(bound.original.retrieval_json)["matches"]:
                    raise TrialStopped("Invalid local branch")
                start = time.monotonic()
                result = render({"evidence_ids": [], "missing": ["not_in_materials"]}, bound, captured(op))
                return ledger.append({**{k:v for k,v in op.items() if k not in {"payload", "request", "retrieval"}},
                    "event": "result", "result": "local_no_match", "model": None, "requested_model": MODEL,
                    "model_output": None, "rendered": result, "attempt": None, "error": None, "reported_model": None,
                    "usage": {"input_tokens": None, "output_tokens": None}, "elapsed_seconds": round(time.monotonic()-start, 4),
                    "frozen_hash": fingerprint(frozen)})
            if settings.qwen_model != FLASH or fingerprint(configured_endpoint(settings)) != frozen["endpoint_hash"]:
                raise TrialStopped("Configuration changed")
            # Original fixed candidate, transport and projection, no patched globals.
            return await model_perform(op, frozen, ledger, settings, transport)
        except (QwenError, TrialStopped, ValueError):
            ledger.append({"event": "paused", "operation": op["operation"], "reason": "preflight"})
            raise TrialStopped("Preflight failed; no automatic resume") from None


def summarize_reviews(reviews: list[dict], planned: int = 20) -> dict:
    """Check human-style labels, not infer semantic correctness from output IDs."""
    if len({r["case"] for r in reviews}) != len(reviews) or len(reviews) > planned:
        raise ValueError("Duplicate/extra review")
    strict = {"pass": 0, "fail": 0, "pending": 0}
    business = {"pass": 0, "conservative": 0, "blocking": 0, "pending": 0}
    for r in reviews:
        checks = r["checks"]
        values = list(checks.values())
        if set(checks) != {"relevance", "coverage", "conditions_sources", "missing", "status", "references_structure"}:
            raise ValueError("Missing review dimensions")
        if any(v is not None and type(v) is not bool for v in values): raise ValueError("Invalid assessment")
        label = "fail" if False in values else "pending" if None in values else "pass"
        if r["strict"] != label: raise ValueError("Strict score inconsistent")
        category = r["business"]
        if category not in business or not r["reason"]: raise ValueError("Review reason required")
        if category == "pass" and label != "pass": raise ValueError("Failure needs classification")
        if category == "conservative" and (label != "fail" or not all(checks[k] is True for k in
                ["relevance", "coverage", "conditions_sources", "references_structure"])
                or r.get("conservative_conditions_verified") is not True):
            raise ValueError("Not eligible for conservative classification")
        if r.get("reading_burden") and not r.get("reading_note"): raise ValueError("Reading evidence required")
        strict[label] += 1; business[category] += 1
    return {"reviewed": len(reviews), "unexecuted_or_unreviewed": planned-len(reviews), "strict": strict, "business": business,
            "reading_burden_cases": sum(bool(r.get("reading_burden")) for r in reviews),
            "eligible_for_integration_acceptance": len(reviews) == planned and business["blocking"] == 0
                and business["pending"] == 0 and strict["pending"] == 0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "next", "status"])
    args = parser.parse_args()
    try:
        if args.command == "status":
            frozen = strict_json((DIRECTORY / "frozen.json").read_bytes()); rows = RegressionLedger(DIRECTORY, frozen).rows()
            print(json.dumps({"attempts": sum(r["event"] == "attempt" for r in rows), "limit": LIMIT,
                "results": [{k:r.get(k) for k in ["case", "result", "error", "attempt"]} for r in rows if r["event"] == "result"],
                "paused": (DIRECTORY / "halt.json").exists() or any(r["event"] == "paused" for r in rows)}))
            return
        if (DIRECTORY / "halt.json").exists(): raise TrialStopped("Preflight previously failed")
        frozen = build_frozen()
        if args.command == "init":
            initialize(DIRECTORY, frozen)
            print(json.dumps({"frozen_hash": fingerprint(frozen), "operations": len(frozen["operations"]), "planned_calls": LIMIT, "calls": 0}))
            return
        result = asyncio.run(next_operation(DIRECTORY, frozen, get_settings(), httpx.AsyncHTTPTransport(retries=0)))
        print(json.dumps({k:result.get(k) for k in ["case", "model", "result", "error", "attempt", "usage", "elapsed_seconds"]}))
        if result.get("result") == "execution_failure": raise SystemExit(2)
    except (TrialStopped, QwenError, OSError, ValueError):
        if args.command != "status" and (DIRECTORY / "frozen.json").exists() and not (DIRECTORY / "halt.json").exists():
            try: create_json(DIRECTORY / "halt.json", {"reason": "preflight_or_execution"})
            except OSError: pass
        print(json.dumps({"result": "stopped", "notice": "No retries or automatic resume; audit ledger."}))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()

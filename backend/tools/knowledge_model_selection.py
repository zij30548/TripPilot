"""Fixed 5 x 2 x 2 model experiment, isolated from production configuration.

init freezes without sending; next performs one preplanned operation; status is
read-only. No model/path/limit override, retry or automatic resume is available.
"""
import argparse
import asyncio
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import AsyncIterator

import httpx
from pydantic import ValidationError

from core.config import Settings, get_settings
from tooling.providers.qwen import MODEL, MAX_OUTPUT_BYTES, QwenClient, QwenError, configured_endpoint, strict_json
from core.schemas.knowledge import KnowledgeResponse
from core.schemas.knowledge_answer import AnswerRequest
from core.schemas.explanation import ExplanationUsage
from tooling.travel.knowledge import fingerprint
from tools.knowledge_ab_trial import QUALITY_ERRORS, TrialStopped, create_json, locked, pending, wire_hash
from tools.knowledge_acceptance import CountedTransport, Ledger, context
from tools.knowledge_b_candidate import VERSION, BoundQuestion, bind, payload_for, render
from tools.knowledge_evidence_trial import EVIDENCE, sha

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = EVIDENCE / "model-selection"
BATCH = "6d2-two-snapshots-20261014-authorized20-v1"
FLASH = MODEL
PLUS = "qwen3.7-plus-2026-05-26"
MODELS = (FLASH, PLUS)
CASES = ("a01", "a03", "a04", "a05", "a06")
LIMIT = 20
PROTOCOL = ROOT / "docs/KNOWLEDGE_6D2_MODEL_PROTOCOL.md"


def captured(op: dict) -> BoundQuestion:
    bound = bind(AnswerRequest.model_validate_json(json.dumps(op["request"])),
                 KnowledgeResponse.model_validate_json(json.dumps(op["retrieval"])))
    if bound.identity != op["binding_hash"]:
        raise TrialStopped("Request binding changed")
    return bound


def experiment_payload(bound: BoundQuestion, model: str) -> dict:
    if model not in MODELS:
        raise TrialStopped("Model not in experiment allowlist")
    payload = payload_for(bound)
    payload["model"] = model  # The only changed wire field; default configuration stays Flash.
    return payload


def make_operations(prior: dict) -> list[dict]:
    source = {o["case"]: o for o in prior["operations"] if o["case"] in CASES}
    if set(source) != set(CASES):
        raise TrialStopped("Missing fixed question")
    operations = []
    for repetition in (1, 2):
        for index, case in enumerate(CASES):
            order = MODELS if (index + repetition - 1) % 2 == 0 else MODELS[::-1]
            for model in order:
                base = source[case]
                if experiment_payload(captured(base), FLASH) != base["payload"]:
                    raise TrialStopped("B candidate differs from prior frozen input")
                op = copy.deepcopy(base)
                short = "flash" if model == FLASH else "plus"
                op.update(operation=f"{BATCH}:{case}:{model}:r{repetition}", model=model,
                          repetition=repetition, arm=short, group="diagnostic-five", request_id=f"ms-{case}-{short}-r{repetition}")
                op["request"]["request_id"] = op["request_id"]
                bound = bind(AnswerRequest.model_validate_json(json.dumps(op["request"])),
                             KnowledgeResponse.model_validate_json(json.dumps(op["retrieval"])))
                payload = experiment_payload(bound, model)
                op.update(binding_hash=bound.identity, payload=payload, payload_hash=wire_hash(payload),
                          business_payload_hash=wire_hash({k: v for k, v in payload.items() if k != "model"}))
                operations.append(op)
    return operations


def preserved() -> dict:
    """Verify every previous receipt/ledger, not just an in-memory allowance."""
    manifests = [
        ("fix/baseline.json", EVIDENCE, "old_evidence_sha256"),
        ("fix/delivery-receipt.json", EVIDENCE / "fix", "artifact_sha256"),
        ("ab-trial/receipt.json", EVIDENCE / "ab-trial", "artifact_sha256"),
        ("b-candidate-validation/receipt.json", EVIDENCE / "b-candidate-validation", "artifact_sha256"),
    ]
    proof = {}
    for name, base, key in manifests:
        doc = strict_json((EVIDENCE / name).read_bytes())
        if any(sha(base / n) != h for n, h in doc[key].items()):
            raise TrialStopped("Previous evidence changed")
        proof[name] = {"sha256": sha(EVIDENCE / name), "files_verified": len(doc[key])}
    old_ledgers = {}
    for name, count in [("calls.jsonl", 20), ("fix/calls.jsonl", 16), ("ab-trial/calls.jsonl", 20),
                        ("b-candidate-validation/calls.jsonl", 16)]:
        rows = [strict_json(s) for s in (EVIDENCE / name).read_text().splitlines()]
        if [r["ordinal"] for r in rows if r["event"] == "attempt"] != list(range(1, count + 1)):
            raise TrialStopped("Prior count changed")
        old_ledgers[name] = {"sha256": sha(EVIDENCE / name), "attempts": count}
    prior = strict_json((EVIDENCE / "b-candidate-validation/frozen.json").read_bytes())
    if any(sha(ROOT / name) != h for name, h in prior["files"].items()):
        raise TrialStopped("Fixed candidate/code/corpus changed")
    caches = strict_json((EVIDENCE / "fix/baseline.json").read_bytes())["cache_sha256"]
    if any(sha(ROOT / name) != h for name, h in caches.items()):
        raise TrialStopped("Protected cache changed")
    return {"receipts": proof, "old_ledgers": old_ledgers, "cache_sha256": caches,
            "prior_frozen_sha256": sha(EVIDENCE / "b-candidate-validation/frozen.json")}


def build_frozen() -> dict:
    proof = preserved()
    prior = strict_json((EVIDENCE / "b-candidate-validation/frozen.json").read_bytes())
    settings = get_settings()
    if settings.qwen_model != FLASH:
        raise TrialStopped("Production model configuration changed")
    files = [*ROOT.glob("backend/app/**/*.py"), *ROOT.glob("backend/tools/knowledge*.py"), PROTOCOL,
             ROOT / "backend/tests/test_knowledge_model_selection.py", ROOT / "docs/KNOWLEDGE_6D2_B03_ERRATUM.md",
             ROOT / "backend/knowledge/corpus.json", ROOT / "backend/knowledge/answer-evaluation.json"]
    spec = strict_json((ROOT / "backend/knowledge/answer-evaluation.json").read_bytes())
    return {"batch": BATCH, "limit": LIMIT, "models": list(MODELS), "candidate_version": VERSION,
            "code_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "files": {str(p.relative_to(ROOT)): sha(p) for p in sorted(files)}, "preserved": proof,
            "corpus_hash": prior["corpus_hash"], "corpus_version": prior["corpus_version"],
            "endpoint_hash": fingerprint(configured_endpoint(settings)), "settings": prior["settings"],
            "expectations": [c for c in spec["cases"] if c["id"] in CASES],
            "operations": make_operations(prior)}


class SelectionLedger(Ledger):
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
                    raise ValueError("Unknown event")
                if r["event"] == "attempt" and any(r.get(k) != op[k] for k in
                    ["request_id", "payload_hash", "model", "repetition", "binding_hash"]):
                    raise ValueError("Invalid attempt identity")
            return rows
        except (OSError, ValueError, KeyError, TypeError):
            raise TrialStopped("Counter missing/corrupt; no send") from None

    def append(self, record: dict, reserve: bool = False) -> dict:
        with locked(self.directory / "ledger.lock"):
            rows = self.rows()
            if reserve:
                op = self.operations.get(record.get("operation"))
                if (not op or any(record.get(k) != op[k] for k in ["request_id", "payload_hash"])
                        or any(r["event"] == "paused" for r in rows)
                        or any(r.get("operation") == record["operation"] for r in rows[1:])):
                    raise TrialStopped("Duplicate, paused or mismatched operation")
                record = {**record, **{k: op[k] for k in ["case", "model", "repetition", "arm", "prompt_version",
                          "prompt_hash", "contract_hash", "retrieval_hash", "binding_hash", "business_payload_hash"]},
                          "frozen_hash": fingerprint(self.frozen), "code_head": self.frozen["code_head"],
                          "corpus_hash": self.frozen["corpus_hash"]}
            return super().append(record, reserve)


def initialize(directory: Path, frozen: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    with locked(directory / "run.lock"):
        if (directory / "frozen.json").exists():
            if strict_json((directory / "frozen.json").read_bytes()) != frozen:
                raise TrialStopped("Frozen input changed")
            SelectionLedger(directory, frozen).rows()
            return
        if (directory / "calls.jsonl").exists():
            raise TrialStopped("Ledger without manifest")
        create_json(directory / "frozen.json", frozen)
        ledger = SelectionLedger(directory, frozen)
        with ledger.path.open("x", encoding="utf-8") as stream:
            os.chmod(ledger.path, 0o600)
            stream.write(json.dumps(ledger.init_record)+"\n"); stream.flush(); os.fsync(stream.fileno())


class ObservedStream(httpx.AsyncByteStream):
    """Bounded passive tee; preserves original bytes/reads, never logs raw body."""
    def __init__(self, stream: httpx.AsyncByteStream, metadata: dict) -> None:
        self.stream, self.metadata = stream, metadata

    async def __aiter__(self) -> AsyncIterator[bytes]:
        body = bytearray()
        overflow = False
        async for chunk in self.stream:
            if len(body) + len(chunk) <= MAX_OUTPUT_BYTES and not overflow:
                body.extend(chunk)
            else:
                overflow = True
                body.clear()
            yield chunk
        if not overflow:
            try:
                value = strict_json(body)
                if not isinstance(value, dict): return
                model = value.get("model")
                if isinstance(model, str) and re.fullmatch(r"qwen3\.7-(?:plus|flash)(?:-\d{4}-\d{2}-\d{2})?", model):
                    self.metadata["reported_model"] = model
                usage = value.get("usage")
                if isinstance(usage, dict):
                    for remote, local in [("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens")]:
                        n = usage.get(remote)
                        if type(n) is int and 0 <= n <= 1_000_000:
                            self.metadata["usage"][local] = n
            except (ValueError, UnicodeError, RecursionError):
                pass

    async def aclose(self) -> None:
        await self.stream.aclose()


class ObservedTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport, metadata: dict) -> None:
        self.inner, self.metadata = inner, metadata

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.inner.handle_async_request(request)
        response.stream = ObservedStream(response.stream, self.metadata)
        return response

    async def aclose(self) -> None:
        await self.inner.aclose()


async def generate(bound: BoundQuestion, payload: dict, settings: Settings,
                   client: httpx.AsyncClient) -> tuple[object, ExplanationUsage]:
    """Experiment-only model gate; no mutation/patch of production allowlists."""
    configured_endpoint(settings)  # Existing key, HTTPS host/path and default-model checks remain.
    if settings.qwen_model != FLASH or payload != experiment_payload(bound, payload.get("model")):
        raise TrialStopped("Model or frozen B payload differs")
    return await QwenClient(settings, client).generate(payload)


async def perform(op: dict, frozen: dict, ledger: SelectionLedger, settings: Settings,
                  transport: httpx.AsyncBaseTransport) -> dict:
    bound = captured(op)
    if op["payload"] != experiment_payload(bound, op["model"]):
        raise TrialStopped("Frozen payload mismatch")
    start = time.monotonic()
    record = {k: v for k, v in op.items() if k not in {"request", "retrieval", "payload"}}
    metadata = {"reported_model": None, "usage": {"input_tokens": None, "output_tokens": None}}
    record.update(event="result", model_output=None, rendered=None, error=None,
                  result="structured_output", frozen_hash=fingerprint(frozen))
    state = {"operation": op["operation"], "request_id": op["request_id"]}
    token = context.set(state)
    try:
        observer = ObservedTransport(transport, metadata)
        async with httpx.AsyncClient(transport=CountedTransport(observer, ledger), follow_redirects=False) as client:
            content, usage = await generate(bound, op["payload"], settings, client)
            record.update(model_output=content)
            metadata["usage"] = usage.model_dump()
            current = captured(op)  # Revalidates corpus/identity after await.
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
    reported = metadata["reported_model"]
    if reported is not None and reported not in {op["model"], op["model"].rsplit("-2026-", 1)[0]}:
        record.update(error="reported_model_mismatch", result="execution_failure")
    record.update(**metadata, attempt=state.get("attempt"), elapsed_seconds=round(time.monotonic()-start, 4))
    saved = ledger.append(record)
    if record["result"] == "execution_failure":
        ledger.append({"event": "paused", "operation": op["operation"], "reason": record["error"]})
    return saved


async def next_operation(directory: Path, frozen: dict, settings: Settings,
                         transport: httpx.AsyncBaseTransport) -> dict:
    with locked(directory / "run.lock"):
        if (directory / "halt.json").exists():
            raise TrialStopped("Execution paused")
        if strict_json((directory / "frozen.json").read_bytes()) != frozen:
            create_json(directory / "halt.json", {"reason": "frozen_mismatch"})
            raise TrialStopped("Frozen version differs")
        ledger = SelectionLedger(directory, frozen)
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
            rows = SelectionLedger(DIRECTORY, frozen).rows()
            print(json.dumps({"attempts": sum(r["event"] == "attempt" for r in rows), "limit": LIMIT,
                              "results": [{k: r.get(k) for k in ["case", "model", "repetition", "result", "error", "attempt"]} for r in rows if r["event"] == "result"],
                              "paused": (DIRECTORY / "halt.json").exists() or any(r["event"] == "paused" for r in rows)}))
            return
        if (DIRECTORY / "halt.json").exists(): raise TrialStopped("Preflight previously failed")
        frozen = build_frozen()
        if args.command == "init":
            initialize(DIRECTORY, frozen)
            print(json.dumps({"frozen_hash": fingerprint(frozen), "operations": len(frozen["operations"]), "limit": LIMIT, "calls": 0}))
            return
        result = asyncio.run(next_operation(DIRECTORY, frozen, get_settings(), httpx.AsyncHTTPTransport(retries=0)))
        print(json.dumps({k: result.get(k) for k in ["case", "model", "repetition", "result", "error", "attempt", "usage", "elapsed_seconds"]}))
    except (TrialStopped, QwenError, OSError, ValueError):
        if args.command != "status" and (DIRECTORY / "frozen.json").exists() and not (DIRECTORY / "halt.json").exists():
            try: create_json(DIRECTORY / "halt.json", {"reason": "preflight_or_execution"})
            except OSError: pass
        print(json.dumps({"result": "stopped", "notice": "No retries or automatic resume; audit ledger and frozen version."}))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()

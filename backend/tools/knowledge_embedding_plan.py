"""Prepare exact future embedding inputs, never send them or read credentials.

The plan is not an authorization, quota ledger, authenticated model output or
quality result. Provider policy is read as literal data without importing the
optional runtime/native stack. No endpoint or send command exists here.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path

from core.llm_utils import bounded_json, RuntimeFailure
from tooling.travel.knowledge_national import NationalKnowledgeIndex
from tools import knowledge_relevance_evaluation as storage

BACKEND = Path(__file__).resolve().parents[1]
POLICY_SOURCE = BACKEND / "echomind_core/embedding.py"
PLAN_SCHEMA = "knowledge-embedding-preparation-plan-v1"
TEXT_VERSION = "complete-reviewed-theme-embedding-text-v1"
MAX_PLAN_BYTES = 4 * 1024 * 1024
POLICY_KEYS = {"EMBEDDING_MODEL", "DIMENSIONS", "MAX_BATCH", "MAX_TEXT_CHARS",
               "MAX_TEXT_BYTES", "MAX_INPUT_BYTES", "MAX_RESPONSE_BYTES", "REQUEST_SECONDS"}


def _policy_literal(node: ast.AST, depth: int = 0):
    if depth > 4:
        raise ValueError()
    # The existing byte cap uses 2 * 1024 * 1024. Only bounded integer
    # multiplication is allowed in addition to literal data, never Python eval.
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        left, right = _policy_literal(node.left, depth + 1), _policy_literal(node.right, depth + 1)
        if type(left) is not int or type(right) is not int or not 0 < left <= 128 * 1024 * 1024 or not 0 < right <= 128 * 1024 * 1024:
            raise ValueError()
        value = left * right
        if value > 128 * 1024 * 1024:
            raise ValueError()
        return value
    return ast.literal_eval(node)


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def provider_policy(path: Path = POLICY_SOURCE) -> dict:
    """Read only top-level literals; no eval/import/config/model construction."""
    raw = storage._read(path, storage.MAX_SOURCE_BYTES)
    values = {}
    try:
        for node in ast.parse(raw).body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in POLICY_KEYS:
                        if target.id in values:
                            raise ValueError()
                        values[target.id] = _policy_literal(node.value)
        if (set(values) != POLICY_KEYS or type(values["EMBEDDING_MODEL"]) is not str
                or not 1 <= len(values["EMBEDDING_MODEL"]) <= 100
                or any(type(values[key]) is not int or values[key] <= 0
                       for key in POLICY_KEYS - {"EMBEDDING_MODEL", "REQUEST_SECONDS"})
                or type(values["REQUEST_SECONDS"]) not in (int, float)
                or not 0 < values["REQUEST_SECONDS"] <= 20):
            raise ValueError()
    except (ValueError, TypeError, SyntaxError, RecursionError):
        raise storage.EvaluationError("embedding_policy_not_literal_or_invalid") from None
    return values | {"source_sha256": sha256(raw)}


def model_payload(texts: list[str], policy: dict) -> dict:
    """Same bounded request shape as the preserved embedding adapter."""
    if (type(texts) is not list or not 1 <= len(texts) <= policy["MAX_BATCH"]
            or any(type(text) is not str or not text.strip() or len(text) > policy["MAX_TEXT_CHARS"]
                   or len(text.encode("utf-8")) > policy["MAX_TEXT_BYTES"] for text in texts)):
        raise storage.EvaluationError("embedding_plan_input_capacity")
    payload = {"model": policy["EMBEDDING_MODEL"], "input": list(texts),
               "dimensions": policy["DIMENSIONS"], "encoding_format": "float"}
    try:
        bounded_json(payload, policy["MAX_INPUT_BYTES"])
    except RuntimeFailure:
        raise storage.EvaluationError("embedding_plan_payload_capacity") from None
    return payload


def document_text(match, base: NationalKnowledgeIndex) -> str:
    # Complete reviewed theme, never labels/answers/IDs or a generated rewrite.
    venue = base.venues[match.source.venue_id]
    return (f"场馆：{venue.name}\n主题：{match.section.heading}\n"
            f"整理摘要：{match.section.summary}\n原文短摘录：{match.section.excerpt}")


def _operations(kind: str, items: list[dict], policy: dict) -> list[dict]:
    result = []
    pending: list[dict] = []

    def append(group: list[dict]) -> None:
        payload = model_payload([item["text"] for item in group], policy)
        raw = bounded_json(payload, policy["MAX_INPUT_BYTES"]).encode()
        result.append({"operation_id": f"{kind}-{len(result) + 1:03d}", "kind": kind,
            "items": [{key: value for key, value in item.items() if key != "text"} for item in group],
            "payload": payload, "payload_sha256": sha256(raw), "payload_bytes": len(raw)})

    for item in items:
        model_payload([item["text"]], policy)  # Validate all inputs before any output.
        candidate = pending + [item]
        try:
            model_payload([part["text"] for part in candidate], policy)
        except storage.EvaluationError:
            append(pending)
            pending = [item]
        else:
            pending = candidate
    if pending:
        append(pending)
    return result


def build_plan(base: NationalKnowledgeIndex, cases: list, code_identity: dict,
               *, policy_source: Path = POLICY_SOURCE) -> dict:
    policy = provider_policy(policy_source)
    documents = []
    for match, _ in sorted(base.chunks, key=lambda row: row[0].chunk_id):
        text = document_text(match, base)
        documents.append({"item_id": match.chunk_id, "unit_hash": base.unit_hashes[match.chunk_id],
                          "text_sha256": sha256(text.encode()), "text": text})
    queries = []
    for case in cases:
        query = storage._request(case)
        base.validate_request(query)
        # Exactly the original question: no expected IDs, rationale, subquestion
        # labels, case number, selected source URL or evaluation answer.
        text = query.question
        queries.append({"request_hash": storage.fingerprint(query.model_dump(mode="json")),
                        "text_sha256": sha256(text.encode()), "text": text})
    if not queries or len({item["request_hash"] for item in queries}) != len(queries):
        raise storage.EvaluationError("embedding_plan_query_identity")
    operations = _operations("documents", documents, policy) + _operations("queries", queries, policy)
    value = {"schema_version": PLAN_SCHEMA, "text_version": TEXT_VERSION,
        "code_identity": dict(code_identity), "corpus_version": base.corpus.corpus_version,
        "corpus_hash": base.hash, "provider_policy": policy,
        "document_inputs": len(documents), "query_inputs": len(queries),
        "proposed_physical_attempts": len(operations), "authorized_attempts": 0,
        "actual_model_calls": 0, "external_calls": 0, "sender_available": False,
        "generation_calls": 0, "rerank_calls": 0, "automatic_retries": 0,
        "parameters_note": "UTF-8 limits are not tokenizer measurements; usage and RMB cost unknown.",
        "required_before_execution": ["new explicit embedding allowance", "version-bound durable pre-send ledger",
            "trusted metered preparation context; do not expand the chat task's three-call limit",
            "same Beijing fixed server endpoint, no credential export", "pause on execution uncertainty"],
        "operations": operations}
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > MAX_PLAN_BYTES:
        raise storage.EvaluationError("embedding_plan_capacity")
    return value


def _prepared(receipt: Path) -> dict:
    # Lazy local import avoids circular module imports in final code freezing.
    from tools import knowledge_semantic_evaluation as evaluation
    value, dataset, base = evaluation.verify(receipt)
    identity = {"protocol_version": evaluation.PROTOCOL, "code_fingerprint": value["code_fingerprint"],
                "code_receipt_sha256": sha256(storage._read(receipt, storage.MAX_RECEIPT_BYTES, private=True)),
                "inputs_receipt_sha256": value["inputs_receipt_sha256"]}
    before = storage._base_projection(base)
    plan = build_plan(base, dataset.cases, identity)
    again, _, _ = evaluation.verify(receipt)
    if again != value or storage._base_projection(base) != before:
        raise storage.EvaluationError("embedding_plan_frozen_source_changed")
    return plan


def prepare(receipt: Path, output: Path) -> dict:
    plan = _prepared(receipt)
    storage._output(output, plan, MAX_PLAN_BYTES)
    return plan


def verify_plan(receipt: Path, plan_path: Path) -> dict:
    expected = _prepared(receipt)
    raw = storage._read(plan_path, MAX_PLAN_BYTES, private=True)
    # Python dict equality considers False == 0 and 1024.0 == 1024. Exact
    # payload verification must preserve JSON types as well as values.
    if storage.fingerprint(storage.strict_json(raw)) != storage.fingerprint(expected):
        raise storage.EvaluationError("embedding_plan_changed")
    return expected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    for name in ("prepare", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--receipt", required=True, type=Path)
        command.add_argument("--output" if name == "prepare" else "--plan", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        plan = prepare(args.receipt, args.output) if args.operation == "prepare" else verify_plan(args.receipt, args.plan)
        print(json.dumps({"status": "ok", "operation": args.operation, "model_calls": 0, "external_calls": 0,
            "documents": plan["document_inputs"], "queries": plan["query_inputs"],
            "proposed_physical_attempts": plan["proposed_physical_attempts"], "authorized_attempts": 0}))
        return 0
    except (ValueError, OSError, UnicodeError, RuntimeFailure):
        print(json.dumps({"status": "stopped", "reason": "offline_plan_validation_failed", "model_calls": 0}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

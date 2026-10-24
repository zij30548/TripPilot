"""Offline three-arm mechanism checks; no model sender or real-quality switch.

The new questions may be frozen before candidate code exists. A completed code
freeze still does not create embeddings or authorize paid calls. ``run`` accepts
only explicitly synthetic controlled artifacts, never a claimed live origin.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from core.schemas.knowledge_national import (
    CityId, Hash, Id, METHOD, NationalKnowledgeRequest, NationalKnowledgeResponse,
    OfflineVectorArtifact, Strict, Version,
)
from tooling.providers.qwen import strict_json
from tooling.travel.knowledge_national import (
    CORPUS_PATH, MANIFEST_PATH, NationalKnowledgeIndex, build_national_index, fingerprint,
)
from tools import knowledge_national_evaluation as national
from tools import knowledge_relevance_evaluation as previous

BACKEND = Path(__file__).resolve().parents[1]
DATASET_PATH = BACKEND / "evaluations/knowledge-semantic-v1.json"
OLD_DATASET_PATH = BACKEND / "evaluations/knowledge-national-v1.json"
RELEVANCE_DATASET_PATH = BACKEND / "evaluations/knowledge-national-relevance-v1.json"
PROTOCOL = "national-semantic-20261024-v1"
INPUT_SCHEMA = "knowledge-semantic-input-freeze-v1"
FREEZE_SCHEMA = "knowledge-semantic-code-freeze-v1"
REPORT_SCHEMA = "knowledge-semantic-controlled-report-v1"
CANDIDATE_VERSION = "knowledge-semantic-candidate-v1"
CONTROLLED_MODEL = "controlled-fixture-vector-v1"
METHODS = {"A": METHOD, "B": "offline-cosine-national-v1", "C": "offline-rrf-national-v1"}
ARMS = {"A": "lexical", "B": "semantic", "C": "hybrid"}
MAX_ARTIFACT_BYTES = 20 * 1024 * 1024
MAX_REPORT_BYTES = 8 * 1024 * 1024
EvaluationError = previous.EvaluationError
Text = Annotated[str, StringConstraints(min_length=1, max_length=300)]
ChunkId = previous.ChunkId


class Subquestion(Strict):
    text: Text
    required: list[ChunkId] = Field(max_length=20)
    availability: Literal["evidence", "missing"]
    missing_reason: Literal["not_in_materials", "live_uncollected", "current_applicability_unknown",
                            "conflicting_materials", "scope_mismatch"] | None

    @model_validator(mode="after")
    def coherent(self):
        if (self.text != self.text.strip() or any(ord(char) < 32 or ord(char) == 127 for char in self.text)
                or len(set(self.required)) != len(self.required)
                or self.availability == "evidence" and (not self.required or self.missing_reason is not None)
                or self.availability == "missing" and (self.required or self.missing_reason is None)):
            raise ValueError("Invalid subquestion annotation")
        return self


class EvaluationCase(Strict):
    id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,39}$")]
    split: Literal["development", "validation"]
    city_id: CityId
    venue_id: Id
    question: Annotated[str, StringConstraints(min_length=2, max_length=160)]
    visit_date: date | None
    relevant: list[ChunkId] = Field(max_length=20)
    required: list[ChunkId] = Field(max_length=20)
    expectation: Literal["evidence", "limited", "no_material"]
    rationale: Annotated[str, StringConstraints(min_length=1, max_length=1200)]
    unanswered: list[Text] = Field(max_length=10)
    subquestions: list[Subquestion] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def coherent(self):
        evidence = [part for part in self.subquestions if part.availability == "evidence"]
        missing = [part.text for part in self.subquestions if part.availability == "missing"]
        if (len(set(self.relevant)) != len(self.relevant) or len(set(self.required)) != len(self.required)
                or not set(self.required) <= set(self.relevant)
                or len({part.text for part in self.subquestions}) != len(self.subquestions)
                or len(set(self.unanswered)) != len(self.unanswered)
                or (missing != self.unanswered if self.expectation == "limited" else bool(self.unanswered))
                or {identifier for part in evidence for identifier in part.required} != set(self.required)):
            raise ValueError("Inconsistent subquestion or reference annotations")
        if (self.expectation == "evidence" and (not evidence or missing)
                or self.expectation == "limited" and (not evidence or not missing)
                or self.expectation == "no_material" and (evidence or not missing or self.relevant or self.required)):
            raise ValueError("Invalid expected evidence availability")
        return self


class EvaluationDataset(Strict):
    schema_version: Literal["knowledge-semantic-evaluation-v1"]
    protocol_version: Literal["national-semantic-20261024-v1"]
    corpus_version: Version
    corpus_hash: Hash
    thresholds: previous.Thresholds
    cases: list[EvaluationCase] = Field(min_length=40, max_length=40)

    @model_validator(mode="after")
    def groups(self):
        if (len({case.id for case in self.cases}) != 40
                or len({(case.city_id, case.venue_id, case.question, case.visit_date) for case in self.cases}) != 40):
            raise ValueError("Duplicate evaluation identity")
        for split in ("development", "validation"):
            group = [case for case in self.cases if case.split == split]
            if len(group) != 20 or sum(case.expectation == "no_material" for case in group) != 8:
                raise ValueError("Fixed evaluation group mismatch")
        return self


class QueryVector(Strict):
    request_hash: Hash
    question_sha256: Hash
    values: list[float] = Field(min_length=1, max_length=4096)


class ControlledArtifact(Strict):
    schema_version: Literal["knowledge-semantic-controlled-artifact-v1"]
    provenance: Literal["synthetic_controlled_not_model"]
    fixture_version: Version
    corpus_version: Version
    document_vectors: OfflineVectorArtifact
    queries: list[QueryVector] = Field(min_length=1, max_length=40)


def _request(case) -> NationalKnowledgeRequest:
    return previous._request(case)


def _input_paths() -> dict[str, Path]:
    return {"backend/evaluations/knowledge-semantic-v1.json": DATASET_PATH,
            "backend/evaluations/knowledge-national-v1.json": OLD_DATASET_PATH,
            "backend/evaluations/knowledge-national-relevance-v1.json": RELEVANCE_DATASET_PATH,
            "backend/knowledge/national-corpus-v2.json": CORPUS_PATH,
            "backend/knowledge/national-sources-v2.json": MANIFEST_PATH}


def _source_paths() -> dict[str, Path]:
    return previous._source_paths() | _input_paths() | {
        "backend/tooling/travel/knowledge_semantic.py": BACKEND / "tooling/travel/knowledge_semantic.py",
        "backend/tests/test_knowledge_semantic.py": BACKEND / "tests/test_knowledge_semantic.py",
        "backend/tools/knowledge_semantic_evaluation.py": Path(__file__).resolve(),
        "backend/tests/test_knowledge_semantic_evaluation.py": BACKEND / "tests/test_knowledge_semantic_evaluation.py",
        "backend/tools/knowledge_embedding_plan.py": BACKEND / "tools/knowledge_embedding_plan.py",
        "backend/tests/test_knowledge_embedding_plan.py": BACKEND / "tests/test_knowledge_embedding_plan.py",
        "backend/echomind_core/embedding.py": BACKEND / "echomind_core/embedding.py",
        "backend/core/llm_utils.py": BACKEND / "core/llm_utils.py",
        "backend/echomind_core/upstream/core/llm_utils.py": BACKEND / "echomind_core/upstream/core/llm_utils.py",
    }


def _load_inputs():
    files = previous._snapshot(_input_paths())
    raw = previous._read(DATASET_PATH, previous.MAX_DATASET_BYTES)
    strict_json(raw)
    dataset = EvaluationDataset.model_validate_json(raw)
    old = []
    for path, schema in ((OLD_DATASET_PATH, national.EvaluationDataset),
                         (RELEVANCE_DATASET_PATH, previous.EvaluationDataset)):
        raw = previous._read(path, previous.MAX_DATASET_BYTES)
        strict_json(raw)
        old.append(schema.model_validate_json(raw))
    known_questions = {(case.city_id, case.venue_id, case.question, case.visit_date)
                       for group in old for case in group.cases}
    if any((case.city_id, case.venue_id, case.question, case.visit_date) in known_questions for case in dataset.cases):
        raise EvaluationError("known_question_reused_as_fresh")
    base = build_national_index(CORPUS_PATH, MANIFEST_PATH)
    for group in [dataset, *old]:
        if group.corpus_hash != base.hash or group.corpus_version != base.corpus.corpus_version:
            raise EvaluationError("dataset_corpus_mismatch")
        previous._validate_cases(group.cases, base)
    if base.method != METHOD or old[0].method != METHOD or previous._snapshot(_input_paths()) != files:
        raise EvaluationError("inputs_changed_or_method_invalid")
    return dataset, base, files


INPUT_KEYS = {"schema_version", "protocol_version", "created_at", "files", "input_fingerprint",
              "corpus_version", "corpus_hash", "model_calls", "external_calls"}
FREEZE_KEYS = {"schema_version", "protocol_version", "created_at", "files", "code_fingerprint",
               "inputs_receipt_path", "inputs_receipt_sha256", "corpus_version", "corpus_hash",
               "methods", "candidate_version", "model_calls", "external_calls"}


def _receipt(path: Path, schema: str, keys: set[str]) -> dict:
    value = strict_json(previous._read(path, previous.MAX_RECEIPT_BYTES, private=True))
    if (type(value) is not dict or set(value) != keys or value.get("schema_version") != schema
            or value.get("protocol_version") != PROTOCOL
            or type(value.get("model_calls")) is not int or value["model_calls"] != 0
            or type(value.get("external_calls")) is not int or value["external_calls"] != 0):
        raise EvaluationError("invalid_freeze_receipt")
    files = value.get("files")
    if (type(files) is not dict or any(type(name) is not str or type(item) is not dict
            or set(item) != {"sha256", "bytes"} or type(item["sha256"]) is not str
            or re.fullmatch(r"[a-f0-9]{64}", item["sha256"]) is None
            or type(item["bytes"]) is not int or not 0 < item["bytes"] <= previous.MAX_SOURCE_BYTES
            for name, item in files.items())):
        raise EvaluationError("invalid_freeze_receipt")
    try:
        created = datetime.fromisoformat(value["created_at"])
        if created.utcoffset().total_seconds() != 0:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise EvaluationError("invalid_freeze_receipt") from None
    return value


def freeze_inputs(receipt: Path) -> dict:
    dataset, base, files = _load_inputs()  # Deliberately no candidate import/search.
    value = {"schema_version": INPUT_SCHEMA, "protocol_version": PROTOCOL,
        "created_at": datetime.now(timezone.utc).isoformat(), "files": files,
        "input_fingerprint": fingerprint(files), "corpus_version": dataset.corpus_version,
        "corpus_hash": base.hash, "model_calls": 0, "external_calls": 0}
    previous._output(receipt, value, previous.MAX_RECEIPT_BYTES)
    return value


def verify_inputs(receipt: Path):
    value = _receipt(receipt, INPUT_SCHEMA, INPUT_KEYS)
    dataset, base, files = _load_inputs()
    if (value["files"] != files or value["input_fingerprint"] != fingerprint(files)
            or value["corpus_hash"] != base.hash or value["corpus_version"] != dataset.corpus_version):
        raise EvaluationError("frozen_inputs_changed")
    return value, dataset, base


def _candidate(base: NationalKnowledgeIndex, vectors: OfflineVectorArtifact | None = None):
    from tooling.travel.knowledge_semantic import OfflineSemanticIndex, SemanticResponse
    if vectors is None:
        return OfflineSemanticIndex, SemanticResponse
    candidate = OfflineSemanticIndex(base, vectors, expected_model=CONTROLLED_MODEL)
    if (candidate.hash != base.hash or candidate.corpus != base.corpus
            or candidate.candidate_version != CANDIDATE_VERSION
            or previous._base_projection(candidate.base) != previous._base_projection(base)):
        raise EvaluationError("candidate_identity_mismatch")
    return candidate, SemanticResponse


def freeze(inputs: Path, receipt: Path) -> dict:
    _, dataset, base = verify_inputs(inputs)
    base_before = previous._base_projection(base)
    files = previous._snapshot(_source_paths())
    input_hash = hashlib.sha256(previous._read(inputs, previous.MAX_RECEIPT_BYTES, private=True)).hexdigest()
    _candidate(base)  # Import only: no vectors exist or are fabricated at freeze.
    if previous._snapshot(_source_paths()) != files or previous._base_projection(base) != base_before:
        raise EvaluationError("source_changed_during_freeze")
    verify_inputs(inputs)
    value = {"schema_version": FREEZE_SCHEMA, "protocol_version": PROTOCOL,
        "created_at": datetime.now(timezone.utc).isoformat(), "files": files,
        "code_fingerprint": fingerprint(files), "inputs_receipt_path": str(inputs),
        "inputs_receipt_sha256": input_hash, "corpus_version": dataset.corpus_version,
        "corpus_hash": base.hash, "methods": METHODS, "candidate_version": CANDIDATE_VERSION,
        "model_calls": 0, "external_calls": 0}
    previous._output(receipt, value, previous.MAX_RECEIPT_BYTES)
    return value


def verify(receipt: Path):
    frozen = _receipt(receipt, FREEZE_SCHEMA, FREEZE_KEYS)
    if (type(frozen["inputs_receipt_path"]) is not str or frozen["methods"] != METHODS
            or frozen["candidate_version"] != CANDIDATE_VERSION):
        raise EvaluationError("invalid_freeze_receipt")
    _, dataset, base = verify_inputs(Path(frozen["inputs_receipt_path"]))
    base_before = previous._base_projection(base)
    files = previous._snapshot(_source_paths())
    if (files != frozen["files"] or fingerprint(files) != frozen["code_fingerprint"]
            or frozen["corpus_version"] != dataset.corpus_version or frozen["corpus_hash"] != base.hash
            or hashlib.sha256(previous._read(Path(frozen["inputs_receipt_path"]), previous.MAX_RECEIPT_BYTES,
                                           private=True)).hexdigest() != frozen["inputs_receipt_sha256"]):
        raise EvaluationError("frozen_version_changed")
    _candidate(base)
    if previous._snapshot(_source_paths()) != files or previous._base_projection(base) != base_before:
        raise EvaluationError("source_changed_during_verification")
    return frozen, dataset, base


def _safe_vector(values: list[float], dimension: int) -> bool:
    return (len(values) == dimension and all(type(item) in (int, float) and math.isfinite(item) for item in values)
            and 0 < sum(item * item for item in values) < float("inf"))


def load_artifact(path: Path, dataset: EvaluationDataset, base: NationalKnowledgeIndex):
    raw = previous._read(path, MAX_ARTIFACT_BYTES, private=True)
    strict_json(raw)
    artifact = ControlledArtifact.model_validate_json(raw)
    vectors = artifact.document_vectors
    if (artifact.corpus_version != dataset.corpus_version or vectors.corpus_hash != base.hash
            or vectors.model != CONTROLLED_MODEL or len(vectors.units) != len(base.unit_hashes)
            or len({unit.chunk_id for unit in vectors.units}) != len(vectors.units)
            or {unit.chunk_id: unit.unit_hash for unit in vectors.units} != base.unit_hashes
            or any(not _safe_vector(unit.values, vectors.dimension) for unit in vectors.units)):
        raise EvaluationError("controlled_artifact_document_mismatch")
    expected = {fingerprint(_request(case).model_dump(mode="json")):
                hashlib.sha256(case.question.encode()).hexdigest() for case in dataset.cases}
    if (len(artifact.queries) != len(expected) or len({query.request_hash for query in artifact.queries}) != len(expected)
            or {query.request_hash: query.question_sha256 for query in artifact.queries} != expected
            or any(not _safe_vector(query.values, vectors.dimension) for query in artifact.queries)):
        raise EvaluationError("controlled_artifact_query_mismatch")
    return artifact, {query.request_hash: tuple(query.values) for query in artifact.queries}, hashlib.sha256(raw).hexdigest()


class _BoundArm:
    def __init__(self, candidate, arm: str, queries: dict):
        self.candidate, self.arm, self.queries = candidate, arm, queries
        self.method = METHODS[arm]

    def search(self, request: NationalKnowledgeRequest):
        values = self.queries.get(fingerprint(request.model_dump(mode="json")))
        if values is None:
            raise EvaluationError("query_vector_missing")
        result = self.candidate.search(request, arm=ARMS[self.arm],
            query_vector=values, query_model=CONTROLLED_MODEL)
        if (result.arm != ARMS[self.arm] or result.candidate_version != CANDIDATE_VERSION
                or result.embedding_model != CONTROLLED_MODEL
                or result.embedding_dimension != len(values)):
            raise EvaluationError("candidate_result_binding_invalid")
        return result


def _subquestions(rows: list[dict], cases: list[EvaluationCase]) -> None:
    for row, case in zip(rows, cases, strict=True):
        available = set(row["valid_returned"][:5]) if row["error"] is None else set()
        row["subquestion_evidence"] = [{"text": part.text, "availability": part.availability,
            "required": part.required, "missing_reason": part.missing_reason,
            "evidence_covered": set(part.required) <= available if part.availability == "evidence" else None}
            for part in case.subquestions]


def summarize(rows: list[dict], thresholds: previous.Thresholds) -> dict:
    result = previous.summarize(rows, thresholds)
    supported = [part for row in rows for part in row["subquestion_evidence"] if part["availability"] == "evidence"]
    result["subquestion_evidence_at_5"] = previous._fraction(sum(part["evidence_covered"] is True for part in supported), len(supported))
    result["annotated_missing_subquestions"] = sum(part["availability"] == "missing"
        for row in rows for part in row["subquestion_evidence"])
    result["mechanical_thresholds_passed"] = result.pop("gate_passed")
    result["semantic_quality_acceptance"] = "not_assessed_controlled_vectors"
    return result


def run(receipt: Path, artifact_path: Path, output: Path, split: str) -> dict:
    if split not in {"development", "validation"}:
        raise EvaluationError("invalid_split")
    frozen, dataset, base = verify(receipt)
    base_before = previous._base_projection(base)
    artifact, queries, artifact_hash = load_artifact(artifact_path, dataset, base)
    artifact_before = fingerprint(artifact.model_dump(mode="json"))
    candidate, response_type = _candidate(base, artifact.document_vectors)
    if (previous._base_projection(base) != base_before
            or fingerprint(artifact.model_dump(mode="json")) != artifact_before):
        raise EvaluationError("candidate_mutated_shared_baseline")
    cases = [case for case in dataset.cases if case.split == split]
    arms = {"A": previous.evaluate_arm(base, NationalKnowledgeResponse, base, cases)}
    for arm in ("B", "C"):
        arms[arm] = previous.evaluate_arm(_BoundArm(candidate, arm, queries), response_type, base, cases)
    for rows in arms.values():
        _subquestions(rows, cases)
    if (previous._snapshot(_source_paths()) != frozen["files"] or previous._base_projection(base) != base_before
            or fingerprint(artifact.model_dump(mode="json")) != artifact_before
            or hashlib.sha256(previous._read(artifact_path, MAX_ARTIFACT_BYTES, private=True)).hexdigest() != artifact_hash):
        raise EvaluationError("frozen_version_changed_during_run")
    verify(receipt)
    report = {"schema_version": REPORT_SCHEMA, "protocol_version": PROTOCOL,
        "scope": "controlled_mechanism_only_not_semantic_quality_or_answer_accuracy", "split": split,
        "mechanism_only": True, "quality_acceptance_not_assessed": True,
        "corpus_version": base.corpus.corpus_version, "corpus_hash": base.hash,
        "methods": METHODS, "candidate_version": CANDIDATE_VERSION,
        "freeze_receipt_sha256": hashlib.sha256(previous._read(receipt, previous.MAX_RECEIPT_BYTES, private=True)).hexdigest(),
        "inputs_receipt_sha256": frozen["inputs_receipt_sha256"], "code_fingerprint": frozen["code_fingerprint"],
        "artifact_sha256": artifact_hash, "artifact_provenance": artifact.provenance,
        "fixture_version": artifact.fixture_version, "embedding_model": artifact.document_vectors.model,
        "embedding_dimension": artifact.document_vectors.dimension, "model_calls": 0, "external_calls": 0,
        "thresholds": dataset.thresholds.model_dump(),
        "summary": {arm: summarize(rows, dataset.thresholds) for arm, rows in arms.items()}, "arms": arms,
        "limitations": ["Synthetic vectors test wiring and invariants, not semantic model quality.",
            "Subquestion evidence coverage does not establish answer completeness or correctness.",
            "Missing subquestions are assessment labels; retrieval does not generate or adjudicate answers.",
            "Exact source projection and declared-date consistency do not certify present official applicability.",
            "No live/model-origin artifact or sending mode is accepted by this tool."]}
    previous._output(output, report, MAX_REPORT_BYTES)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline three-arm mechanism checks; no network or models")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("freeze-inputs", "freeze", "verify", "run"):
        command = commands.add_parser(name)
        command.add_argument("--receipt", type=Path, required=True)
        if name == "freeze":
            command.add_argument("--inputs", type=Path, required=True)
        if name == "run":
            command.add_argument("--artifact", type=Path, required=True)
            command.add_argument("--split", choices=("development", "validation"), required=True)
            command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "freeze-inputs":
            result = freeze_inputs(args.receipt)
        elif args.command == "freeze":
            result = freeze(args.inputs, args.receipt)
        elif args.command == "verify":
            result, *_ = verify(args.receipt)
        else:
            result = run(args.receipt, args.artifact, args.output, args.split)
        print(json.dumps({"status": "ok", "operation": args.command, "model_calls": 0,
            "external_calls": 0, "corpus_hash": result["corpus_hash"]}))
        return 0
    except (ValueError, TypeError, OSError, RecursionError, ImportError):
        print(json.dumps({"status": "failed", "code": "offline_semantic_evaluation_failed",
                          "model_calls": 0, "external_calls": 0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

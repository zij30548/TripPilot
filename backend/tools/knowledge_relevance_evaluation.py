"""Offline, version-bound lexical A/B evaluation; never a model sender.

Input freezing deliberately does not import the candidate. The original corpus,
60 questions, evaluator and historical scores remain independent and unchanged.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

from core.schemas.knowledge_national import (
    CityId, Hash, Id, METHOD, NOTICES, NationalKnowledgeRequest,
    NationalKnowledgeResponse, Strict, Version,
)
from tooling.providers.qwen import strict_json
from tooling.travel.knowledge_national import (
    CORPUS_PATH, MANIFEST_PATH, NationalKnowledgeIndex, build_national_index,
    fingerprint,
)
from tools import knowledge_national_evaluation as legacy

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
DATASET_PATH = BACKEND / "evaluations/knowledge-national-relevance-v1.json"
KNOWN_DATASET_PATH = BACKEND / "evaluations/knowledge-national-v1.json"
PROTOCOL = "national-relevance-20261024-v1"
INPUT_SCHEMA = "knowledge-relevance-input-freeze-v1"
FREEZE_SCHEMA = "knowledge-relevance-code-freeze-v1"
REPORT_SCHEMA = "knowledge-relevance-report-v1"
CANDIDATE_METHOD = "topic-gated-lexical-candidate-v1"
CANDIDATE_VERSION = "knowledge-relevance-v1"
MAX_SOURCE_BYTES = 1024 * 1024
MAX_DATASET_BYTES = 256 * 1024
MAX_RECEIPT_BYTES = 128 * 1024
MAX_REPORT_BYTES = 8 * 1024 * 1024
ChunkId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,79}/[a-z][a-z0-9_-]{0,79}$")]


class EvaluationError(ValueError):
    """Fixed safe diagnostic only; never include source bodies or exceptions."""


class Thresholds(Strict):
    hit_at_3: Literal[0.9]
    required_at_5: Literal[0.9]
    no_material_false_hit_rate: Literal[0.1]
    cross_scope_hits: int = Field(ge=0, le=0)


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
    unanswered: list[Annotated[str, StringConstraints(min_length=1, max_length=300)]] = Field(max_length=10)

    @model_validator(mode="after")
    def annotations(self):
        if (len(set(self.relevant)) != len(self.relevant) or len(set(self.required)) != len(self.required)
                or not set(self.required) <= set(self.relevant)
                or len(set(self.unanswered)) != len(self.unanswered)
                or any(value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value)
                       for value in self.unanswered)):
            raise ValueError("Invalid annotation")
        if self.expectation == "no_material":
            if self.relevant or self.required:
                raise ValueError("Absent questions cannot have positive references")
        elif not self.relevant or not self.required:
            raise ValueError("Positive questions need complete evidence annotations")
        if bool(self.unanswered) != (self.expectation == "limited"):
            raise ValueError("Only limited cases must identify actual unanswered matters")
        return self


class EvaluationDataset(Strict):
    schema_version: Literal["knowledge-relevance-evaluation-v1"]
    protocol_version: Literal["national-relevance-20261024-v1"]
    corpus_version: Version
    corpus_hash: Hash
    thresholds: Thresholds
    cases: list[EvaluationCase] = Field(min_length=60, max_length=60)

    @model_validator(mode="after")
    def groups(self):
        if (len({case.id for case in self.cases}) != 60
                or len({(case.city_id, case.venue_id, case.question, case.visit_date) for case in self.cases}) != 60):
            raise ValueError("Duplicate evaluation identity")
        for split in ("development", "validation"):
            group = [case for case in self.cases if case.split == split]
            if (len(group) != 30 or sum(case.expectation == "no_material" for case in group) != 10
                    or sum(case.expectation != "no_material" for case in group) != 20):
                raise ValueError("Fixed evaluation group mismatch")
        return self


def _read(path: Path, maximum: int, *, private: bool = False) -> bytes:
    descriptor = None
    try:
        if not path.is_absolute() or path.is_symlink():
            raise EvaluationError("absolute_regular_file_required")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= maximum
                or private and (before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) != 0o600)):
            raise EvaluationError("artifact_permissions_or_capacity")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            raw = handle.read(maximum + 1)
            after = os.fstat(handle.fileno())
        if (len(raw) > maximum or (before.st_size, before.st_mtime_ns, before.st_ino)
                != (after.st_size, after.st_mtime_ns, after.st_ino)):
            raise EvaluationError("artifact_changed_during_read")
        return raw
    except OSError:
        raise EvaluationError("artifact_read_failed") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _output(path: Path, value: dict, maximum: int) -> bytes:
    if (not path.is_absolute() or path.parent.resolve() != path.parent or path == ROOT or ROOT in path.parents
            or not path.parent.is_dir() or path.parent.stat().st_mode & 0o022):
        raise EvaluationError("private_external_output_required")
    raw = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    if len(raw) > maximum:
        raise EvaluationError("output_capacity")
    descriptor = None
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        raise EvaluationError("exclusive_output_failed") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return raw


def _input_paths() -> dict[str, Path]:
    return {"backend/evaluations/knowledge-national-relevance-v1.json": DATASET_PATH,
            "backend/evaluations/knowledge-national-v1.json": KNOWN_DATASET_PATH,
            "backend/knowledge/national-corpus-v2.json": CORPUS_PATH,
            "backend/knowledge/national-sources-v2.json": MANIFEST_PATH}


def _source_paths() -> dict[str, Path]:
    return legacy._source_paths() | _input_paths() | {
        "backend/tooling/travel/knowledge_relevance.py": BACKEND / "tooling/travel/knowledge_relevance.py",
        "backend/tests/test_knowledge_relevance.py": BACKEND / "tests/test_knowledge_relevance.py",
        "backend/tools/knowledge_relevance_evaluation.py": Path(__file__).resolve(),
        "backend/tests/test_knowledge_relevance_evaluation.py": BACKEND / "tests/test_knowledge_relevance_evaluation.py",
    }


def _snapshot(paths: dict[str, Path]) -> dict[str, dict[str, str | int]]:
    return {name: {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
            for name, path in sorted(paths.items()) for raw in [_read(path, MAX_SOURCE_BYTES)]}


def _validate_cases(cases: list, base: NationalKnowledgeIndex) -> None:
    units = {match.chunk_id: match for match, _ in base.chunks}
    for case in cases:
        try:
            base.validate_request(_request(case))
        except ValueError:
            raise EvaluationError("dataset_scope_invalid") from None
        if any(identifier not in units or units[identifier].source.venue_id != case.venue_id
               for identifier in case.relevant + case.required):
            raise EvaluationError("dataset_reference_invalid")


def _request(case) -> NationalKnowledgeRequest:
    # Assessment labels, IDs, rationales and missing matters never reach either
    # retrieval method. Both arms receive these same five allowed fields.
    return NationalKnowledgeRequest(city_id=case.city_id, venue_id=case.venue_id,
        question=case.question, visit_date=case.visit_date, limit=5)


def _load_inputs() -> tuple[EvaluationDataset, legacy.EvaluationDataset, NationalKnowledgeIndex, dict]:
    before = _snapshot(_input_paths())
    raw = _read(DATASET_PATH, MAX_DATASET_BYTES)
    strict_json(raw)
    dataset = EvaluationDataset.model_validate_json(raw)
    known_raw = _read(KNOWN_DATASET_PATH, MAX_DATASET_BYTES)
    strict_json(known_raw)
    known = legacy.EvaluationDataset.model_validate_json(known_raw)
    known_questions = {(case.city_id, case.venue_id, case.question, case.visit_date) for case in known.cases}
    if any((case.city_id, case.venue_id, case.question, case.visit_date) in known_questions for case in dataset.cases):
        raise EvaluationError("known_question_reused_as_fresh")
    base = build_national_index(CORPUS_PATH, MANIFEST_PATH)
    if (dataset.corpus_hash != base.hash or dataset.corpus_version != base.corpus.corpus_version
            or known.corpus_hash != base.hash or known.corpus_version != base.corpus.corpus_version
            or known.method != METHOD or base.method != METHOD):
        raise EvaluationError("dataset_corpus_or_method_mismatch")
    _validate_cases(dataset.cases, base)
    _validate_cases(known.cases, base)
    if _snapshot(_input_paths()) != before:
        raise EvaluationError("input_changed_during_validation")
    return dataset, known, base, before


def _receipt(path: Path, schema: str, keys: set[str]) -> dict:
    value = strict_json(_read(path, MAX_RECEIPT_BYTES, private=True))
    if (type(value) is not dict or set(value) != keys or value.get("schema_version") != schema
            or value.get("protocol_version") != PROTOCOL
            or type(value.get("model_calls")) is not int or value["model_calls"] != 0
            or type(value.get("external_calls")) is not int or value["external_calls"] != 0):
        raise EvaluationError("invalid_freeze_receipt")
    files = value.get("files")
    if (type(files) is not dict or any(type(name) is not str or type(item) is not dict
            or set(item) != {"sha256", "bytes"} or type(item["sha256"]) is not str
            or re.fullmatch(r"[a-f0-9]{64}", item["sha256"]) is None
            or type(item["bytes"]) is not int or not 0 < item["bytes"] <= MAX_SOURCE_BYTES
            for name, item in files.items())):
        raise EvaluationError("invalid_freeze_receipt")
    try:
        created = datetime.fromisoformat(value["created_at"])
        if created.utcoffset().total_seconds() != 0:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise EvaluationError("invalid_freeze_receipt") from None
    return value


INPUT_KEYS = {"schema_version", "protocol_version", "created_at", "files", "input_fingerprint",
              "corpus_version", "corpus_hash", "model_calls", "external_calls"}
FREEZE_KEYS = {"schema_version", "protocol_version", "created_at", "files", "code_fingerprint",
               "inputs_receipt_path", "inputs_receipt_sha256", "corpus_version", "corpus_hash",
               "baseline_method", "candidate_method", "candidate_version", "model_calls", "external_calls"}


def freeze_inputs(receipt: Path) -> dict:
    dataset, _, base, files = _load_inputs()  # No candidate import or search.
    value = {"schema_version": INPUT_SCHEMA, "protocol_version": PROTOCOL,
        "created_at": datetime.now(timezone.utc).isoformat(), "files": files,
        "input_fingerprint": fingerprint(files), "corpus_version": dataset.corpus_version,
        "corpus_hash": base.hash, "model_calls": 0, "external_calls": 0}
    _output(receipt, value, MAX_RECEIPT_BYTES)
    return value


def verify_inputs(receipt: Path) -> tuple[dict, EvaluationDataset, legacy.EvaluationDataset, NationalKnowledgeIndex]:
    value = _receipt(receipt, INPUT_SCHEMA, INPUT_KEYS)
    dataset, known, base, files = _load_inputs()
    if (value["files"] != files or value["input_fingerprint"] != fingerprint(files)
            or value["corpus_hash"] != base.hash or value["corpus_version"] != dataset.corpus_version):
        raise EvaluationError("frozen_inputs_changed")
    return value, dataset, known, base


def _candidate(base: NationalKnowledgeIndex):
    # This module is intentionally absent from freeze-inputs execution.
    from tooling.travel.knowledge_relevance import RelevanceIndex, RelevanceResponse
    candidate = RelevanceIndex(base)
    candidate_base = candidate.base
    if (type(candidate_base) is not type(base) or candidate_base.hash != base.hash
            or _base_projection(candidate_base) != _base_projection(base)
            or candidate.hash != base.hash or candidate.corpus != base.corpus
            or candidate.method != CANDIDATE_METHOD or candidate.candidate_version != CANDIDATE_VERSION):
        raise EvaluationError("candidate_identity_mismatch")
    return candidate, RelevanceResponse


def _base_projection(base: NationalKnowledgeIndex) -> dict:
    return {"hash": base.hash, "method": base.method,
            "corpus": base.corpus.model_dump(mode="json"),
            "manifest": base.manifest.model_dump(mode="json"),
            "units": [match.model_dump(mode="json") for match, _ in base.chunks],
            "cities": {key: value.model_dump(mode="json") for key, value in base.cities.items()},
            "venues": {key: value.model_dump(mode="json") for key, value in base.venues.items()}}


def freeze(inputs: Path, receipt: Path) -> dict:
    _, dataset, _, base = verify_inputs(inputs)
    base_before = _base_projection(base)
    input_hash = hashlib.sha256(_read(inputs, MAX_RECEIPT_BYTES, private=True)).hexdigest()
    files = _snapshot(_source_paths())
    candidate, _ = _candidate(base)  # Construction only, no retrieval.
    verify_inputs(inputs)
    if (_snapshot(_source_paths()) != files or _base_projection(base) != base_before
            or hashlib.sha256(_read(inputs, MAX_RECEIPT_BYTES, private=True)).hexdigest() != input_hash):
        raise EvaluationError("source_changed_during_freeze")
    value = {"schema_version": FREEZE_SCHEMA, "protocol_version": PROTOCOL,
        "created_at": datetime.now(timezone.utc).isoformat(), "files": files,
        "code_fingerprint": fingerprint(files), "inputs_receipt_path": str(inputs),
        "inputs_receipt_sha256": input_hash,
        "corpus_version": dataset.corpus_version, "corpus_hash": base.hash,
        "baseline_method": base.method, "candidate_method": candidate.method,
        "candidate_version": candidate.candidate_version, "model_calls": 0, "external_calls": 0}
    _output(receipt, value, MAX_RECEIPT_BYTES)
    return value


def verify(receipt: Path):
    value = _receipt(receipt, FREEZE_SCHEMA, FREEZE_KEYS)
    if (type(value["inputs_receipt_path"]) is not str or value["baseline_method"] != METHOD
            or value["candidate_method"] != CANDIDATE_METHOD or value["candidate_version"] != CANDIDATE_VERSION):
        raise EvaluationError("invalid_freeze_receipt")
    inputs = Path(value["inputs_receipt_path"])
    _, dataset, known, base = verify_inputs(inputs)
    files = _snapshot(_source_paths())
    if (value["files"] != files or value["code_fingerprint"] != fingerprint(files)
            or value["corpus_version"] != dataset.corpus_version or value["corpus_hash"] != base.hash
            or value["inputs_receipt_sha256"] != hashlib.sha256(_read(inputs, MAX_RECEIPT_BYTES, private=True)).hexdigest()):
        raise EvaluationError("frozen_version_changed")
    base_before = _base_projection(base)
    candidate, response_type = _candidate(base)
    if _base_projection(base) != base_before or _snapshot(_source_paths()) != files:
        raise EvaluationError("source_changed_during_verification")
    return value, dataset, known, base, candidate, response_type


def _applicability_status(source, visit_date: date | None) -> str:
    # Independent decision table, rather than asking the production function
    # to check itself. It still checks declared metadata, not source truth.
    if source.kind == "historical":
        return "historical_reference"
    if visit_date is None:
        return "no_visit_date"
    if ((source.effective_from is not None and visit_date < source.effective_from)
            or (source.effective_until is not None and visit_date > source.effective_until)):
        return "outside_declared_range"
    if source.effective_from is not None and source.effective_until is not None:
        return "within_declared_range_unverified"
    return "current_applicability_unknown"


def evaluate_arm(index, response_type: type[BaseModel], base: NationalKnowledgeIndex, cases: list,
                 *, annotation_scope: str = "fresh_frozen") -> list[dict]:
    units = {match.chunk_id: match.model_copy(deep=True) for match, _ in base.chunks}
    cities = {key: value.model_copy(deep=True) for key, value in base.cities.items()}
    venues = {key: value.model_copy(deep=True) for key, value in base.venues.items()}
    corpus_hash, corpus_version, document_count = base.hash, base.corpus.corpus_version, len(base.corpus.documents)
    rows = []
    for case in cases:
        row = case.model_dump(mode="json") | {"annotation_scope": annotation_scope,
            "returned": [], "valid_returned": [], "first_relevant_rank": None,
            "hit_at_3": None if case.expectation == "no_material" else False,
            "all_required_at_5": None if case.expectation == "no_material" else False,
            "no_material_false_hit": False if case.expectation == "no_material" else None,
            "relevant_units": 0, "returned_units": 0, "noise_units": 0, "reading_characters": 0,
            "cross_scope_hits": 0, "reference_errors": 0, "applicability_errors": 0,
            "result": None, "error": None}
        request = _request(case)
        try:
            response = index.search(request.model_copy(deep=True))
            if not isinstance(response, response_type):
                raise EvaluationError("invalid_result")
            # model_copy and mutation bypass Pydantic assignment validation.
            response = response_type.model_validate_json(response.model_dump_json())
            if (response.method != index.method or response.corpus_hash != corpus_hash
                    or response.corpus_version != corpus_version or response.request != request
                    or response.document_count != document_count or response.chunk_count != len(units)
                    or response.city != cities[case.city_id] or response.venue != venues[case.venue_id]):
                raise EvaluationError("invalid_result")
            row["result"] = response.model_dump(mode="json")
            for match in response.matches:
                known = units.get(match.chunk_id)
                row["returned"].append(match.chunk_id)
                row["reading_characters"] += len(match.section.heading) + len(match.section.summary) + len(match.section.excerpt)
                if match.source.venue_id != case.venue_id:
                    row["cross_scope_hits"] += 1
                valid = (known is not None and match.chunk_id == f"{match.source.id}/{match.section.id}"
                    and match.content_hash == known.content_hash and match.source == known.source and match.section == known.section)
                if not valid:
                    row["reference_errors"] += 1
                else:
                    row["valid_returned"].append(match.chunk_id)
                status = _applicability_status(match.source, case.visit_date)
                if match.applicability_check.status != status or match.applicability_check.notice != NOTICES[status]:
                    row["applicability_errors"] += 1
            ids = row["valid_returned"]
            row["returned_units"] = len(row["returned"])
            row["relevant_units"] = sum(identifier in case.relevant for identifier in ids)
            row["noise_units"] = row["returned_units"] - row["relevant_units"]
            rank = next((rank for rank, identifier in enumerate(row["returned"], 1)
                         if identifier in ids and identifier in case.relevant), None)
            row["first_relevant_rank"] = rank
            if case.expectation == "no_material":
                row["no_material_false_hit"] = bool(row["returned"])
            else:
                row["hit_at_3"] = rank is not None and rank <= 3
                row["all_required_at_5"] = bool(case.required) and set(case.required) <= set(ids[:5])
            if row["reference_errors"] or row["applicability_errors"] or row["cross_scope_hits"]:
                row["error"] = "result_integrity_failed"
                row["first_relevant_rank"] = None
                if case.expectation != "no_material":
                    row["hit_at_3"], row["all_required_at_5"] = False, False
        except Exception as error:
            row["error"] = "invalid_result" if isinstance(error, (EvaluationError, ValueError, TypeError)) else "retrieval_error"
            if case.expectation == "no_material":
                row["no_material_false_hit"] = bool(row["returned"])
        rows.append(row)
    return rows


def _fraction(numerator: int, denominator: int) -> dict:
    return {"numerator": numerator, "denominator": denominator,
            "value": numerator / denominator if denominator else None}


def summarize(rows: list[dict], thresholds: Thresholds) -> dict:
    positives = [row for row in rows if row["expectation"] != "no_material"]
    absent = [row for row in rows if row["expectation"] == "no_material"]
    hit = _fraction(sum(row["hit_at_3"] is True for row in positives), len(positives))
    required = _fraction(sum(row["all_required_at_5"] is True for row in positives), len(positives))
    false = _fraction(sum(row["no_material_false_hit"] is True for row in absent), len(absent))
    reciprocal = sum(1 / row["first_relevant_rank"] for row in positives if row["first_relevant_rank"] is not None)
    scope = sum(row["cross_scope_hits"] for row in rows)
    refs = sum(row["reference_errors"] for row in rows)
    applicability = sum(row["applicability_errors"] for row in rows)
    errors = sum(row["error"] is not None for row in rows)
    relevant = sum(row["relevant_units"] for row in rows)
    returned = sum(row["returned_units"] for row in rows)
    reading = sum(row["reading_characters"] for row in rows)
    return {"questions": len(rows), "hit_at_3": hit, "required_at_5": required,
        "no_material_false_hits": false,
        "mrr": {"reciprocal_rank_sum": reciprocal, "denominator": len(positives),
                "value": reciprocal / len(positives) if positives else None},
        "relevant_units": relevant, "returned_units": returned, "noise_units": returned - relevant,
        "unit_precision": _fraction(relevant, returned), "unit_noise_rate": _fraction(returned - relevant, returned),
        "reading_characters": reading, "reading_characters_per_question": _fraction(reading, len(rows)),
        "cross_scope_hits": scope, "reference_errors": refs, "applicability_errors": applicability,
        "execution_errors": errors,
        "gate_passed": bool(positives and absent and hit["value"] >= thresholds.hit_at_3
            and required["value"] >= thresholds.required_at_5
            and false["value"] <= thresholds.no_material_false_hit_rate
            and scope <= thresholds.cross_scope_hits and refs == 0 and applicability == 0 and errors == 0)}


def run(receipt: Path, output: Path, split: str) -> dict:
    if split not in {"development", "validation", "known_diagnostic"}:
        raise EvaluationError("invalid_split")
    frozen, dataset, known, base, candidate, response_type = verify(receipt)
    base_before = _base_projection(base)
    cases = known.cases if split == "known_diagnostic" else [case for case in dataset.cases if case.split == split]
    scope = "known_diagnostic_not_held_out_or_new_acceptance" if split == "known_diagnostic" else "fresh_frozen"
    arms = {"A": evaluate_arm(base, NationalKnowledgeResponse, base, cases, annotation_scope=scope),
            "B": evaluate_arm(candidate, response_type, base, cases, annotation_scope=scope)}
    if (_snapshot(_source_paths()) != frozen["files"] or _base_projection(base) != base_before
            or hashlib.sha256(_read(Path(frozen["inputs_receipt_path"]), MAX_RECEIPT_BYTES, private=True)).hexdigest()
            != frozen["inputs_receipt_sha256"]):
        raise EvaluationError("frozen_version_changed_during_run")
    report = {"schema_version": REPORT_SCHEMA, "protocol_version": PROTOCOL,
        "split": split, "scope": "reference_retrieval_only_not_answer_accuracy", "annotation_scope": scope,
        "corpus_version": base.corpus.corpus_version, "corpus_hash": base.hash,
        "baseline_method": base.method, "candidate_method": candidate.method, "candidate_version": candidate.candidate_version,
        "freeze_receipt_sha256": hashlib.sha256(_read(receipt, MAX_RECEIPT_BYTES, private=True)).hexdigest(),
        "inputs_receipt_sha256": frozen["inputs_receipt_sha256"], "code_fingerprint": frozen["code_fingerprint"],
        "model_calls": 0, "external_calls": 0, "thresholds": dataset.thresholds.model_dump(),
        "summary": {arm: summarize(rows, dataset.thresholds) for arm, rows in arms.items()}, "arms": arms,
        "limitations": ["Direct unit relevance comes from frozen manual labels, not an answer correctness proof.",
            "Applicability checks concern declared metadata consistency, not current official validity.",
            "Precision and reading burden are reported separately; coverage does not erase irrelevant evidence.",
            "Known diagnostics do not change historical scores or count toward fresh acceptance."]}
    _output(output, report, MAX_REPORT_BYTES)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline fixed lexical A/B evaluation, no network or model")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("freeze-inputs", "freeze", "verify", "run"):
        command = commands.add_parser(name)
        command.add_argument("--receipt", type=Path, required=True)
        if name == "freeze":
            command.add_argument("--inputs", type=Path, required=True)
        if name == "run":
            command.add_argument("--split", choices=("development", "validation", "known_diagnostic"), required=True)
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
            result = run(args.receipt, args.output, args.split)
        print(json.dumps({"status": "ok", "operation": args.command, "model_calls": 0,
            "external_calls": 0, "corpus_hash": result["corpus_hash"]}))
        return 0
    except (ValueError, TypeError, OSError, RecursionError, ImportError):
        print(json.dumps({"status": "failed", "code": "offline_relevance_evaluation_failed",
                          "model_calls": 0, "external_calls": 0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

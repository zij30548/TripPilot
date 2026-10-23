"""Frozen, local-only national-reference retrieval evaluation.

No sender, credentials, environment configuration, database or model exists in
this tool. Receipts bind the reviewed corpus and actual implementation before
the first run. These metrics assess retrieval, never generated-answer accuracy.
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

from pydantic import Field, StringConstraints, model_validator

from core.schemas.knowledge_national import (
    CityId, Hash, Id, METHOD, NationalKnowledgeRequest, NationalKnowledgeResponse,
    Strict, Version,
)
from tooling.providers.qwen import strict_json
from tooling.travel.knowledge_national import (
    CORPUS_PATH, MANIFEST_PATH, NationalKnowledgeIndex, applicability,
    build_national_index, fingerprint,
)

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
DATASET_PATH = BACKEND / "evaluations" / "knowledge-national-v1.json"
PROTOCOL = "national-retrieval-20261024-v1"
FREEZE_SCHEMA = "knowledge-evaluation-freeze-v1"
REPORT_SCHEMA = "knowledge-evaluation-report-national-v1"
MAX_DATASET_BYTES = 256 * 1024
MAX_RECEIPT_BYTES = 64 * 1024
MAX_SOURCE_BYTES = 1024 * 1024
MAX_REPORT_BYTES = 2 * 1024 * 1024
ChunkId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,79}/[a-z][a-z0-9_-]{0,79}$")]


class EvaluationError(ValueError):
    """A fixed diagnostic, not raw source text, paths or exception bodies."""


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

    @model_validator(mode="after")
    def annotations(self):
        if (len(set(self.relevant)) != len(self.relevant) or len(set(self.required)) != len(self.required)
                or not set(self.required) <= set(self.relevant)):
            raise ValueError("Invalid or duplicate annotation")
        if self.expectation == "no_material":
            if self.relevant or self.required:
                raise ValueError("No-material questions cannot have positive labels")
        elif not self.relevant or not self.required:
            raise ValueError("Positive questions need explicit relevant and required evidence")
        return self


class EvaluationDataset(Strict):
    schema_version: Literal["knowledge-evaluation-national-v1"]
    corpus_version: Version
    corpus_hash: Hash
    protocol_version: Literal["national-retrieval-20261024-v1"]
    method: Literal["cjk-bigram-bm25-national-v1"]
    thresholds: Thresholds
    cases: list[EvaluationCase] = Field(min_length=60, max_length=60)

    @model_validator(mode="after")
    def frozen_groups(self):
        if len({case.id for case in self.cases}) != 60:
            raise ValueError("Duplicate evaluation case")
        identities = {(case.city_id, case.venue_id, case.question, case.visit_date) for case in self.cases}
        if len(identities) != 60:
            raise ValueError("Repeated questions are not distinct evaluation samples")
        for split in ("development", "validation"):
            cases = [case for case in self.cases if case.split == split]
            if (len(cases) != 30 or sum(case.expectation == "no_material" for case in cases) != 10
                    or sum(case.expectation in {"evidence", "limited"} for case in cases) != 20):
                raise ValueError("Fixed evaluation group mismatch")
        return self


def _read(path: Path, maximum: int, *, private: bool = False) -> bytes:
    descriptor = None
    try:
        if not path.is_absolute() or path.is_symlink():
            raise EvaluationError("regular_absolute_file_required")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size <= 0
                or before.st_size > maximum or private and (
                    before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) != 0o600)):
            raise EvaluationError("artifact_permissions_or_capacity")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            raw = handle.read(maximum + 1)
            after = os.fstat(handle.fileno())
        if len(raw) > maximum or (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise EvaluationError("artifact_changed_during_read")
        return raw
    except OSError:
        raise EvaluationError("artifact_read_failed") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _source_paths() -> dict[str, Path]:
    """Fixed source whitelist; neither CLI nor a question supplies file paths."""
    return {
        "backend/evaluations/knowledge-national-v1.json": DATASET_PATH,
        "backend/knowledge/national-corpus-v2.json": CORPUS_PATH,
        "backend/knowledge/national-sources-v2.json": MANIFEST_PATH,
        "backend/tools/knowledge_national_evaluation.py": Path(__file__).resolve(),
        "backend/core/schemas/knowledge_national.py": BACKEND / "core/schemas/knowledge_national.py",
        "backend/tooling/travel/knowledge_national.py": BACKEND / "tooling/travel/knowledge_national.py",
        "backend/tooling/travel/knowledge.py": BACKEND / "tooling/travel/knowledge.py",
        "backend/core/schemas/knowledge.py": BACKEND / "core/schemas/knowledge.py",
        "backend/core/chat_cities.py": BACKEND / "core/chat_cities.py",
        "backend/core/destinations.py": BACKEND / "core/destinations.py",
        "backend/tooling/providers/qwen.py": BACKEND / "tooling/providers/qwen.py",
        "shared/chat-cities.json": ROOT / "shared/chat-cities.json",
        "shared/chat-cities-source.json": ROOT / "shared/chat-cities-source.json",
        "shared/destinations.json": ROOT / "shared/destinations.json",
    }


def _snapshot() -> dict[str, dict[str, str | int]]:
    result = {}
    for name, path in sorted(_source_paths().items()):
        raw = _read(path, MAX_SOURCE_BYTES)
        result[name] = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    return result


def validate_dataset(dataset: EvaluationDataset, index: NationalKnowledgeIndex) -> None:
    if (dataset.corpus_version != index.corpus.corpus_version or dataset.corpus_hash != index.hash
            or dataset.method != index.method or index.method != METHOD):
        raise EvaluationError("dataset_corpus_or_method_mismatch")
    units = {match.chunk_id: match for match, _ in index.chunks}
    for case in dataset.cases:
        try:
            index.validate_request(NationalKnowledgeRequest(city_id=case.city_id, venue_id=case.venue_id,
                question=case.question, visit_date=case.visit_date))
        except ValueError:
            raise EvaluationError("dataset_scope_invalid") from None
        for identifier in set(case.relevant + case.required):
            match = units.get(identifier)
            if match is None or match.source.venue_id != case.venue_id:
                raise EvaluationError("dataset_reference_invalid")


def _load_experiment() -> tuple[EvaluationDataset, NationalKnowledgeIndex, dict]:
    before = _snapshot()
    raw = _read(DATASET_PATH, MAX_DATASET_BYTES)
    strict_json(raw)
    dataset = EvaluationDataset.model_validate_json(raw)
    index = build_national_index(CORPUS_PATH, MANIFEST_PATH)
    validate_dataset(dataset, index)
    if _snapshot() != before:
        raise EvaluationError("freeze_source_changed")
    return dataset, index, before


def _output(path: Path, value: dict, maximum: int) -> bytes:
    if (not path.is_absolute() or path.parent.resolve() != path.parent
            or path == ROOT or ROOT in path.parents or not path.parent.is_dir()
            or path.parent.stat().st_mode & 0o022):
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


def freeze(receipt: Path) -> dict:
    dataset, index, files = _load_experiment()  # Validation only, no search.
    value = {"schema_version": FREEZE_SCHEMA, "protocol_version": PROTOCOL,
        "created_at": datetime.now(timezone.utc).isoformat(), "method": METHOD,
        "corpus_version": dataset.corpus_version, "corpus_hash": index.hash,
        "dataset_sha256": files["backend/evaluations/knowledge-national-v1.json"]["sha256"],
        "files": files, "code_fingerprint": fingerprint(files),
        "model_calls": 0, "external_calls": 0}
    _output(receipt, value, MAX_RECEIPT_BYTES)
    return value


def verify(receipt: Path) -> tuple[dict, EvaluationDataset, NationalKnowledgeIndex]:
    raw = _read(receipt, MAX_RECEIPT_BYTES, private=True)
    value = strict_json(raw)
    keys = {"schema_version", "protocol_version", "created_at", "method", "corpus_version", "corpus_hash",
            "dataset_sha256", "files", "code_fingerprint", "model_calls", "external_calls"}
    if (type(value) is not dict or set(value) != keys or value["schema_version"] != FREEZE_SCHEMA
            or value["protocol_version"] != PROTOCOL or value["method"] != METHOD
            or type(value["model_calls"]) is not int or value["model_calls"] != 0
            or type(value["external_calls"]) is not int or value["external_calls"] != 0):
        raise EvaluationError("invalid_freeze_receipt")
    if type(value["files"]) is not dict or any(
            type(name) is not str or type(item) is not dict or set(item) != {"sha256", "bytes"}
            or type(item["sha256"]) is not str or re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is None
            or type(item["bytes"]) is not int or not 0 < item["bytes"] <= MAX_SOURCE_BYTES
            for name, item in value["files"].items()):
        raise EvaluationError("invalid_freeze_receipt")
    try:
        acquired = datetime.fromisoformat(value["created_at"])
        if acquired.utcoffset().total_seconds() != 0:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise EvaluationError("invalid_freeze_receipt") from None
    dataset, index, files = _load_experiment()
    if (value["files"] != files or value["code_fingerprint"] != fingerprint(files)
            or value["corpus_version"] != dataset.corpus_version or value["corpus_hash"] != index.hash
            or value["dataset_sha256"] != files["backend/evaluations/knowledge-national-v1.json"]["sha256"]):
        raise EvaluationError("frozen_version_changed")
    return value, dataset, index


def evaluate_cases(index: NationalKnowledgeIndex, cases: list[EvaluationCase]) -> list[dict]:
    """Generic controlled-test seam. Production freeze still requires 60 cases."""
    units = {match.chunk_id: match for match, _ in index.chunks}
    rows = []
    for case in cases:
        row = case.model_dump(mode="json") | {"returned": [], "first_relevant_rank": None,
            "hit_at_3": False if case.expectation != "no_material" else None,
            "all_required_at_5": False if case.expectation != "no_material" else None,
            "no_material_false_hit": False if case.expectation == "no_material" else None,
            "cross_scope_hits": 0, "reference_errors": 0, "applicability_errors": 0,
            "applicability": [], "error": None}
        request = NationalKnowledgeRequest(city_id=case.city_id, venue_id=case.venue_id,
            question=case.question, visit_date=case.visit_date, limit=5)
        try:
            response = index.search(request)
            if (not isinstance(response, NationalKnowledgeResponse) or response.method != index.method
                    or response.corpus_hash != index.hash or response.request != request or len(response.matches) > 5):
                raise EvaluationError("invalid_result")
            for match in response.matches:
                known = units.get(match.chunk_id)
                row["returned"].append(match.chunk_id)
                if (match.source.venue_id != case.venue_id or response.venue.id != case.venue_id
                        or response.city.id != case.city_id or response.venue.city_id != case.city_id):
                    row["cross_scope_hits"] += 1
                if (known is None or match.chunk_id != f"{match.source.id}/{match.section.id}"
                        or known.source.venue_id != match.source.venue_id
                        or known.content_hash != match.content_hash):
                    row["reference_errors"] += 1
                check = applicability(match.source, case.visit_date)
                if check != match.applicability_check:
                    row["applicability_errors"] += 1
                row["applicability"].append({"chunk_id": match.chunk_id,
                    "status": match.applicability_check.status, "notice": match.applicability_check.notice,
                    "source_kind": match.source.kind,
                    "effective_from": match.source.effective_from.isoformat() if match.source.effective_from else None,
                    "effective_until": match.source.effective_until.isoformat() if match.source.effective_until else None})
            ids = row["returned"]
            rank = next((rank for rank, identifier in enumerate(ids, 1) if identifier in case.relevant), None)
            row["first_relevant_rank"] = rank
            if case.expectation == "no_material":
                row["no_material_false_hit"] = bool(ids)
            else:
                row["hit_at_3"] = rank is not None and rank <= 3
                row["all_required_at_5"] = bool(case.required) and set(case.required) <= set(ids[:5])
        except Exception as error:
            # Keep the failed question in every applicable denominator. No raw
            # exceptions, source bodies, retry or replacement sample is used.
            row["error"] = "invalid_result" if isinstance(error, EvaluationError) else "retrieval_error"
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
    errors = sum(row["error"] is not None for row in rows)
    scope = sum(row["cross_scope_hits"] for row in rows)
    references = sum(row["reference_errors"] for row in rows)
    applicability_errors = sum(row["applicability_errors"] for row in rows)
    gate = bool(positives and absent and hit["value"] >= thresholds.hit_at_3
        and required["value"] >= thresholds.required_at_5 and false["value"] <= thresholds.no_material_false_hit_rate
        and scope <= thresholds.cross_scope_hits and references == 0 and applicability_errors == 0 and errors == 0)
    return {"questions": len(rows), "hit_at_3": hit, "required_at_5": required,
        "no_material_false_hits": false, "mrr": {"reciprocal_rank_sum": reciprocal,
            "denominator": len(positives), "value": reciprocal / len(positives) if positives else None},
        "cross_scope_hits": scope, "reference_errors": references, "applicability_errors": applicability_errors,
        "execution_errors": errors, "no_material_execution_errors": sum(row["error"] is not None for row in absent),
        "gate_passed": gate}


def run(receipt: Path, output: Path) -> dict:
    frozen, dataset, index = verify(receipt)
    rows = evaluate_cases(index, dataset.cases)
    if _snapshot() != frozen["files"]:
        raise EvaluationError("frozen_version_changed_during_run")
    groups = {split: summarize([row for row in rows if row["split"] == split], dataset.thresholds)
              for split in ("development", "validation")}
    report = {"schema_version": REPORT_SCHEMA, "protocol_version": PROTOCOL, "method": METHOD,
        "corpus_version": dataset.corpus_version, "corpus_hash": index.hash,
        "freeze_receipt_sha256": hashlib.sha256(_read(receipt, MAX_RECEIPT_BYTES, private=True)).hexdigest(),
        "dataset_sha256": frozen["dataset_sha256"], "code_fingerprint": frozen["code_fingerprint"],
        "scope": "reference_retrieval_only_not_answer_accuracy", "thresholds": dataset.thresholds.model_dump(),
        "model_calls": 0, "external_calls": 0, "splits": groups,
        "total": summarize(rows, dataset.thresholds),
        "gate_passed": all(value["gate_passed"] for value in groups.values()), "cases": rows}
    _output(output, report, MAX_REPORT_BYTES)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Frozen local-reference retrieval evaluation; no model or network")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("freeze", "verify", "run"):
        command = commands.add_parser(name)
        command.add_argument("--receipt", type=Path, required=True)
        if name == "run":
            command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "freeze":
            result = freeze(args.receipt)
        elif args.command == "verify":
            result, _, _ = verify(args.receipt)
        else:
            result = run(args.receipt, args.output)
        print(json.dumps({"status": "ok", "operation": args.command, "model_calls": 0,
            "external_calls": 0, "corpus_hash": result["corpus_hash"],
            **({"gate_passed": result["gate_passed"]} if args.command == "run" else {})}))
        return 0
    except (ValueError, TypeError, OSError, RecursionError):
        print(json.dumps({"status": "failed", "code": "offline_evaluation_or_freeze_failed", "model_calls": 0, "external_calls": 0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

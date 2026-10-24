"""Explicit travel evaluation adapters over the preserved EchoMind evaluators.

No HTTP route, background job, sender, new budget or default switch is added.
Original IntentEvaluator computes Accuracy/Macro-F1; SafeJudge makes at most one
already-bound attempt.  Technical/program checks, semantic evidence review and
Judge quality remain separate: a high average cannot erase a blocking error.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
import re
from typing import Any

from core.llm_utils import bounded_json
from echomind_core.execution import ExecutionError, SafeJudge, execution_gate
from echomind_core.provider import MODEL
from echomind_core.travel_domain import TravelIntent, TravelIntentRecognizer
from echomind_core.travel_package import FrozenTravelDomainPackage
from echomind_core.travel_stages import StageClient
from echomind_core.upstream.evaluation.evaluator import IntentEvaluator, IntentTestCase

VERSION = "travel-original-evaluation-v1"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_DIMENSIONS = ("relevance", "accuracy", "completeness", "helpfulness")


def _hash(value: Any) -> str:
    try:
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                        separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    except (UnicodeError, TypeError, ValueError):
        raise ExecutionError("travel_evaluation_invalid") from None


@dataclass(frozen=True)
class TravelIntentCase:
    case_id: str
    message: str
    expected_intent: TravelIntent
    history: tuple[tuple[str, str], ...] = ()
    reference_notes: str = ""

    def __post_init__(self) -> None:
        if (type(self.case_id) is not str or _ID.fullmatch(self.case_id) is None
                or type(self.message) is not str or not 1 <= len(self.message) <= 4000
                or type(self.expected_intent) is not TravelIntent
                or type(self.history) is not tuple or len(self.history) > 12
                or any(type(row) is not tuple or len(row) != 2 or type(row[0]) is not str
                       or row[0] not in {"user", "assistant"}
                       or type(row[1]) is not str or len(row[1]) > 4000 for row in self.history)
                or type(self.reference_notes) is not str or len(self.reference_notes) > 8000):
            raise ExecutionError("travel_evaluation_invalid")
        bounded_json({"message": self.message, "history": self.history}, 24_000)


class _FinitePredictionPort:
    def __init__(self, recognizer: TravelIntentRecognizer):
        self.recognizer = recognizer

    async def recognize(self, message: str, history=None):
        result = await self.recognizer.recognize(message, history)
        if (type(result.intent) is not TravelIntent or type(result.confidence) not in (int, float)
                or not math.isfinite(result.confidence) or not 0 <= result.confidence <= 1
                or type(result.reasoning) is not str or len(result.reasoning) > 160):
            raise ExecutionError("travel_evaluation_prediction")
        return result


class TravelIntentEvaluator:
    """Original metric algorithm; references never enter classifier arguments.

    A batch shares its caller's existing gate and budget.  It never creates a
    budget per case, splits/retries failed cases or silently removes failures.
    An execution failure aborts the batch; no partial aggregate is fabricated.
    """
    def __init__(self, recognizer: TravelIntentRecognizer):
        if type(recognizer) is not TravelIntentRecognizer:
            raise ExecutionError("travel_evaluation_invalid")
        self._original = IntentEvaluator(_FinitePredictionPort(recognizer))

    async def evaluate(self, cases: tuple[TravelIntentCase, ...] | list[TravelIntentCase]) -> dict[str, Any]:
        if type(cases) not in (tuple, list) or not 1 <= len(cases) <= 32:
            raise ExecutionError("travel_evaluation_invalid")
        captured = tuple(cases)
        if (any(type(case) is not TravelIntentCase for case in captured)
                or len({case.case_id for case in captured}) != len(captured)):
            raise ExecutionError("travel_evaluation_invalid")
        identity = [{"id": case.case_id, "message": case.message,
                     "expected": case.expected_intent.value, "history": case.history,
                     "reference_notes": case.reference_notes} for case in captured]
        input_hash = _hash(identity)  # Validate all freeze metadata before any send.
        original_cases = [IntentTestCase(case.message, case.expected_intent.value,
            {"history": [{"role": role, "content": content} for role, content in case.history]})
            for case in captured]
        result = await self._original.evaluate(original_cases)
        return {"schema_version": VERSION, "kind": "original_intent_metrics", "input_hash": input_hash,
                "case_ids": [case.case_id for case in captured], "metrics": result,
                "quality_claim": "depends_on_frozen_cases_and_actual_model_evidence"}


@dataclass(frozen=True)
class JudgeAssessment:
    dimensions: tuple[float | None, float | None, float | None, float | None]
    judge_failed: bool
    attempts: int
    stop_reason: str | None
    model_reservations: int
    input_hash: str
    package_version: str

    def __post_init__(self) -> None:
        if (type(self.dimensions) is not tuple or len(self.dimensions) != 4
                or type(self.judge_failed) is not bool
                or any(value is not None and (type(value) not in (int, float)
                       or not math.isfinite(value) or not 0 <= value <= 1) for value in self.dimensions)
                or (self.judge_failed and any(value is not None for value in self.dimensions))
                or (not self.judge_failed and (any(value is None for value in self.dimensions)
                                              or self.stop_reason != "end_turn"))
                or type(self.attempts) is not int or self.attempts != 1
                or type(self.model_reservations) is not int or not 0 <= self.model_reservations <= 1
                or self.stop_reason is not None and (type(self.stop_reason) is not str
                    or self.stop_reason not in {"end_turn", "max_tokens", "tool_use"})
                or type(self.input_hash) is not str or re.fullmatch(r"[0-9a-f]{64}", self.input_hash) is None
                or type(self.package_version) is not str or re.fullmatch(r"[0-9a-f]{64}", self.package_version) is None):
            raise ExecutionError("travel_evaluation_invalid")

    @property
    def overall(self) -> float | None:
        if self.judge_failed or any(value is None for value in self.dimensions):
            return None
        return sum(self.dimensions) / 4

    def as_dict(self) -> dict[str, Any]:
        return {"status": "unscored" if self.judge_failed else "scored",
                "scores": dict(zip(_DIMENSIONS, self.dimensions)), "overall": self.overall,
                "attempts": self.attempts, "stop_reason": self.stop_reason,
                "model_reservations": self.model_reservations, "input_hash": self.input_hash,
                "requested_model": MODEL, "upstream_model": "unknown",
                "package_version": self.package_version}


class TravelJudge:
    """Travel stage instructions + original one-attempt SafeJudge/parser.

    Existing calibration examples/parser are preserved, not claimed as a newly
    calibrated travel judge.  A response parsed at a truncation boundary is
    still unscored. No output repair, second judge or retry is permitted here.
    """
    def __init__(self, package: FrozenTravelDomainPackage):
        if type(package) is not FrozenTravelDomainPackage:
            raise ExecutionError("travel_evaluation_invalid")
        self.package = package
        self._judge = SafeJudge(StageClient(package, "judge"))
        self._used = False

    async def judge(self, question: str, response: str, context: str | None = None) -> JudgeAssessment:
        if (type(question) is not str or not 1 <= len(question) <= 4000
                or type(response) is not str or not 1 <= len(response) <= 8000
                or context is not None and (type(context) is not str or len(context) > 8000)):
            raise ExecutionError("travel_evaluation_invalid")
        bounded_json([question, response, context], 16_000)
        identity = {"question": question, "response": response, "context": context,
                    "package_version": self.package.version, "stage": "judge", "model": MODEL}
        input_hash = _hash(identity)
        gate = execution_gate()
        if self._used:
            raise ExecutionError("travel_evaluation_already_used")
        self._used = True
        before = gate.budget.counts[0]
        result = await self._judge.judge(question, response, context)
        values = tuple(getattr(result, field) for field in _DIMENSIONS)
        valid = (not result.judge_failed and result.stop_reason == "end_turn"
                 and all(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1
                         for value in values))
        return JudgeAssessment(tuple(float(value) for value in values) if valid else (None,) * 4,
            not valid, result.attempts, result.stop_reason, gate.budget.counts[0] - before,
            input_hash, self.package.version)


class ReviewVerdict(Enum):
    PASS = "pass"
    FAIL = "fail"
    PENDING = "pending"


class TravelBlocker(Enum):
    WRONG_FACT = "wrong_fact"
    WRONG_SOURCE = "wrong_source"
    WRONG_CITY_OR_VENUE = "wrong_city_or_venue"
    CHANGED_CONDITION = "changed_condition"
    IMPORTANT_OMISSION = "important_omission"
    UNEXECUTED_SUCCESS_CLAIM = "unexecuted_success_claim"
    UNAUTHORIZED_ACTION = "unauthorized_action"
    HIDDEN_CONFLICT_OR_UNKNOWN = "hidden_conflict_or_unknown"


@dataclass(frozen=True)
class TravelBusinessReview:
    """Explicit checks/reviewer decisions, never automatically proved by JSON.

    Citation identity can be checked mechanically.  Direct support, complete
    coverage and condition fidelity require separate evidence-backed review;
    callers must record its method and cannot substitute the judge's average.
    """
    request_binding_valid: bool
    tool_authority_valid: bool
    citation_identity_valid: bool
    evidence_support: ReviewVerdict
    question_coverage: ReviewVerdict
    conditions_and_scope: ReviewVerdict
    missing_and_state: ReviewVerdict
    blockers: tuple[TravelBlocker, ...] = ()

    def __post_init__(self) -> None:
        if (any(type(value) is not bool for value in (
                self.request_binding_valid, self.tool_authority_valid, self.citation_identity_valid))
                or any(type(value) is not ReviewVerdict for value in (
                    self.evidence_support, self.question_coverage, self.conditions_and_scope, self.missing_and_state))
                or type(self.blockers) is not tuple or len(self.blockers) > len(TravelBlocker)
                or any(type(value) is not TravelBlocker for value in self.blockers)
                or len(set(self.blockers)) != len(self.blockers)):
            raise ExecutionError("travel_evaluation_invalid")


def assess_reply(judge: JudgeAssessment, review: TravelBusinessReview, *, threshold: float = .75) -> dict[str, Any]:
    if (type(judge) is not JudgeAssessment or type(review) is not TravelBusinessReview
            or type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0 <= threshold <= 1):
        raise ExecutionError("travel_evaluation_invalid")
    mechanical = {"request_binding_valid": review.request_binding_valid,
                  "tool_authority_valid": review.tool_authority_valid,
                  "citation_identity_valid": review.citation_identity_valid}
    semantic = {"evidence_support": review.evidence_support.value,
                "question_coverage": review.question_coverage.value,
                "conditions_and_scope": review.conditions_and_scope.value,
                "missing_and_state": review.missing_and_state.value}
    blocked = not all(mechanical.values()) or bool(review.blockers) or "fail" in semantic.values()
    pending = "pending" in semantic.values()
    status = ("blocked" if blocked else "pending" if pending else "unscored" if judge.overall is None
              else "passed" if judge.overall >= threshold else "quality_failed")
    return {"schema_version": VERSION, "status": status, "passed": status == "passed",
            "program_checks": mechanical, "semantic_review": semantic,
            "blockers": [item.value for item in review.blockers], "judge": judge.as_dict(),
            "quality_threshold": threshold, "average_overrides_blockers": False}

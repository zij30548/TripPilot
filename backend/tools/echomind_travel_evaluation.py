"""Offline travel-candidate cases and cost review; deliberately no sender.

This is a scenario/rubric contract, not authorization, a live ledger, a semantic
grader, or a production router. It imports neither the candidate nor a model
client. A later authorized trial must freeze actual prompts, tools, retrieval,
code and payloads and use the existing durable send gates; historical unused
allowances cannot be consumed through this file.
"""
import argparse
from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

from core.llm_utils import strict_json

VERSION = "echomind-travel-offline-evaluation-v1"
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "backend/evaluations/echomind-travel-v1.json"
MAX_MANIFEST_BYTES = 128 * 1024
MAX_MODEL_INPUT_BYTES = 48_000
Text = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=2000)]
Identifier = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Destination = Literal["shanghai", "hangzhou", "beijing", "guangzhou", "chengdu", "xian"]


class EvaluationError(ValueError):
    """Fixed safe code; never print fixture input or validation exceptions."""


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Authorization(Strict):
    real_model_calls: Literal[0]
    real_amap_calls: Literal[0]
    automatic_retry: Literal[False]

    @model_validator(mode="before")
    @classmethod
    def exact_scalars(cls, value: Any) -> Any:
        if type(value) is dict and (type(value.get("real_model_calls")) is not int
                                   or type(value.get("real_amap_calls")) is not int
                                   or type(value.get("automatic_retry")) is not bool):
            raise ValueError("authorization_scalar_type")
        return value


class Runtime(Strict):
    chat_model: Literal["qwen3.7-flash-2026-07-15"]
    embedding_model: Literal["text-embedding-v4"]
    embedding_dimensions: Literal[1024]
    embedding_batch_limit: Literal[10]
    task_model_limit: Literal[3]
    task_tool_limit: Literal[4]
    task_seconds: Literal[45]
    active_product_cutover: Literal[False]

    @model_validator(mode="before")
    @classmethod
    def exact_scalars(cls, value: Any) -> Any:
        if type(value) is dict and (any(type(value.get(key)) is not int for key in (
                "embedding_dimensions", "embedding_batch_limit", "task_model_limit", "task_tool_limit", "task_seconds"))
                or type(value.get("active_product_cutover")) is not bool):
            raise ValueError("runtime_scalar_type")
        return value


class Message(Strict):
    role: Literal["user", "assistant"]
    content: Text


class InputContext(Strict):
    """Finite business availability, not invented POIs or authoritative totals."""
    destination_id: Destination | None
    start_date: str | None
    end_date: str | None
    formal_draft: Literal["none", "valid", "stale"]
    cost_inputs: Literal["none", "unknown", "invalid", "complete"]
    preview: Literal["none", "ready", "stale"]

    @model_validator(mode="after")
    def dates(self) -> "InputContext":
        if (self.start_date is None) != (self.end_date is None):
            raise ValueError("date_pair")
        if self.start_date is not None:
            try:
                first, last = date.fromisoformat(self.start_date), date.fromisoformat(self.end_date or "")
                if (first.isoformat() != self.start_date or last.isoformat() != self.end_date
                        or not 1 <= (last - first).days + 1 <= 7):
                    raise ValueError()
            except (TypeError, ValueError):
                raise ValueError("date_range") from None
        if self.formal_draft == "valid" and (self.destination_id is None or self.start_date is None):
            raise ValueError("formal_context_missing")
        if self.preview != "none" and self.formal_draft != "valid":
            raise ValueError("preview_without_formal")
        return self


class BusinessInput(Strict):
    history: list[Message] = Field(max_length=8)
    text: Text
    context: InputContext


class Review(Strict):
    expected: list[Text] = Field(min_length=1, max_length=12)
    blocking: list[Text] = Field(min_length=1, max_length=12)
    dimensions: list[Literal["selection", "coverage", "facts", "confirmation", "state", "privacy", "budget"]] = Field(min_length=1, max_length=7)
    controlled_checks: list[Text] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def unique_dimensions(self) -> "Review":
        if len(self.dimensions) != len(set(self.dimensions)):
            raise ValueError("duplicate_dimension")
        return self


class Case(Strict):
    id: Identifier
    category: Literal["day", "budget", "clarification", "poi", "preview", "scope", "safety"]
    branch: Literal["semantic_candidate", "controlled_boundary"]
    data_mode: Literal["controlled_fixture"]
    input: BusinessInput
    review: Review


class Manifest(Strict):
    schema_version: Literal["echomind-travel-offline-evaluation-v1"]
    candidate: Identifier
    preparation: Literal["scenarios_fixed_live_payload_not_frozen"]
    authorization: Authorization
    runtime: Runtime
    limitations: list[Text] = Field(min_length=3, max_length=12)
    cases: list[Case] = Field(min_length=10, max_length=16)

    @model_validator(mode="after")
    def case_matrix(self) -> "Manifest":
        ids = [item.id for item in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_case")
        required = {"day", "budget", "clarification", "poi", "preview", "scope", "safety"}
        if set(item.category for item in self.cases) != required:
            raise ValueError("case_matrix")
        return self


def canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise EvaluationError("invalid_json_value") from None


def load(path: Path = DEFAULT_MANIFEST) -> Manifest:
    """Bounded read, duplicate-key rejection and strict nested whitelist."""
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise EvaluationError("manifest_too_large")
        return Manifest.model_validate(strict_json(raw))
    except EvaluationError:
        raise
    except (OSError, ValueError, TypeError, ValidationError, RecursionError):
        raise EvaluationError("invalid_manifest") from None


def model_input(case: Case) -> dict[str, Any]:
    """Business-only projection; labels/IDs/expected checks never enter it.

    This is NOT a ready-to-send provider payload. The trusted future runner must
    use its real bound snapshot rather than trusting this controlled fixture.
    Server request IDs, ownership and version gates are managed outside it.
    """
    if type(case) is not Case:
        raise EvaluationError("invalid_case")
    value = case.input.model_dump(mode="json")
    raw = canonical(value)
    if len(raw) > MAX_MODEL_INPUT_BYTES:
        raise EvaluationError("model_input_too_large")
    # Round trip severs every mutable reference; no copy of the entire case.
    return strict_json(raw)


def cost_plan(*, profile: str, template_count: int, prepared_index: bool) -> dict[str, Any]:
    """Planned path versus hard send cap; neither is a spending permit.

    Every HTTP embedding batch is one model send, including preparation. Do not
    turn an over-budget profile into extra tasks to regain budget. Individual
    turns still share the unchanged 3-model/4-tool/45-second runtime limits.
    The original Agent can request another tool at its second model response:
    direct-tools' expected selection + feedback is TWO planned sends, not a
    guarantee that the runtime cannot use its THIRD permitted send. A future
    frozen trial must reserve the hard cap, or explicitly lower its TaskBudget.
    """
    if (type(prepared_index) is not bool or type(template_count) is not int
            or not 0 <= template_count <= 1024):
        raise EvaluationError("invalid_cost_plan")
    profiles = {
        # Planned path costs, not substitutes for the runtime hard send cap.
        "direct_tools": {"tool_selection": 1, "feedback": 1},
        "fusion_classification": {"intent": 1, "query_embeddings": 1},
        "parallel_review": {"child_agents": 2, "composer": 1},
        # A diagnostic only, never an enabled auto-execution path.
        "full_chain_diagnostic": {"intent": 1, "query_embeddings": 1, "agent": 1,
                                  "feedback": 1, "rewrite": 1, "rerank": 1,
                                  "composer": 1, "judge": 1},
    }
    if (type(profile) is not str or profile not in profiles
            or profile in {"fusion_classification", "full_chain_diagnostic"} and not template_count):
        raise EvaluationError("invalid_cost_plan")
    components = dict(profiles[profile])
    uses_index = profile in {"fusion_classification", "full_chain_diagnostic"}
    components["template_preparation"] = (template_count + 9) // 10 if uses_index and not prepared_index else 0
    total = sum(components.values())
    prerequisite_ready = not uses_index or prepared_index
    enabled = profile in {"direct_tools", "fusion_classification"}
    return {"profile": profile, "planned_components": components,
            "planned_model_sends": total, "runtime_hard_max_model_sends": 3,
            "enabled_by_this_candidate": enabled,
            "implementation_status": "candidate_entry" if enabled else (
                "source_only_hypothetical" if profile == "parallel_review" else "diagnostic_only"),
            "fits_existing_task_budget": total <= 3,
            "prerequisites_ready": prerequisite_ready,
            "blocked_reason": ("travel_composer_not_integrated" if profile == "parallel_review" else
                               "diagnostic_only_over_budget" if profile == "full_chain_diagnostic" else
                               "embedding_preparation_required" if not prerequisite_ready else None),
            "live_authorized_calls": 0, "may_send": False,
            "execution_boundary": {"direct_tools": "tool_loop_within_shared_budget",
                                   "fusion_classification": "classify_only_no_followup_task",
                                   "parallel_review": "trusted_intent_prepared_context_no_tools",
                                   "full_chain_diagnostic": "blocked_not_an_execution_mode"}[profile],
            "note": "planned_sends_are_not_hard_cap_or_actual_usage; authorization_and_payload_freeze_required"}


def verify(manifest: Manifest) -> dict[str, Any]:
    rows = [{"id": item.id, "branch": item.branch,
             "business_input_sha256": hashlib.sha256(canonical(model_input(item))).hexdigest()}
            for item in manifest.cases]
    return {"schema_version": VERSION, "candidate": manifest.candidate,
            "manifest_sha256": hashlib.sha256(canonical(manifest.model_dump(mode="json"))).hexdigest(),
            "case_count": len(rows), "branches": dict(Counter(item.branch for item in manifest.cases)),
            "real_model_calls": 0, "real_amap_calls": 0,
            "live_payload_frozen": False, "live_ready": False, "cases": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify", "plan"))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--template-count", type=int)
    parser.add_argument("--prepared-index", action="store_true")
    args = parser.parse_args()
    try:
        result = verify(load(args.manifest))
        if args.command == "plan":
            result["cost_review"] = [cost_plan(profile=profile, template_count=args.template_count or 0,
                                             prepared_index=args.prepared_index)
                                     for profile in ("direct_tools", "parallel_review")]
            if args.template_count is not None:
                result["cost_review"].extend(cost_plan(profile=profile, template_count=args.template_count,
                                                       prepared_index=args.prepared_index)
                                             for profile in ("fusion_classification", "full_chain_diagnostic"))
            else:
                result["index_preparation"] = "pending_actual_template_count_and_fingerprint"
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except EvaluationError as error:
        parser.exit(2, f"Offline evaluation stopped: {error}. No request was sent.\n")


if __name__ == "__main__":
    main()

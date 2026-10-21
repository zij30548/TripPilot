from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from core.schemas.explanation import ExplanationUsage, Identifier, ScheduleSnapshot, StrictModel
from core.schemas.route import PlaceId


class AdjustmentIntentRequest(StrictModel):
    contract_version: Literal["adjustment-intent-v1"]
    request_id: Identifier
    draft_version: int = Field(ge=0, le=9_007_199_254_740_991)
    instruction: Annotated[str, StringConstraints(min_length=1, max_length=400)]
    snapshot: ScheduleSnapshot

    @model_validator(mode="after")
    def valid_instruction(self) -> Self:
        if not self.instruction.strip() or any(ord(c) < 32 and c not in "\n\t" for c in self.instruction):
            raise ValueError("Invalid instruction")
        return self


ReasonCode = Literal["single_optional", "unclear_request", "ambiguous_target", "negated_request",
                     "multiple_changes", "outside_scope", "required_target"]


class IntentSelection(StrictModel):
    decision: Literal["execute", "clarify", "unsupported"]
    action: Literal["exclude_optional"] | None
    target_id: PlaceId | None
    reason_code: ReasonCode

    @model_validator(mode="after")
    def consistent(self) -> Self:
        codes = {"execute": {"single_optional"},
                 "clarify": {"unclear_request", "ambiguous_target", "negated_request"},
                 "unsupported": {"multiple_changes", "outside_scope", "required_target"}}
        if self.reason_code not in codes[self.decision]:
            raise ValueError("Incompatible intent reason")
        if self.decision == "execute":
            if self.action != "exclude_optional" or self.target_id is None:
                raise ValueError("Missing adjustment target")
        elif self.action is not None or self.target_id is not None:
            raise ValueError("Non-executable intent cannot carry an operation")
        return self


class AdjustmentIntentResponse(StrictModel):
    contract_version: Literal["adjustment-intent-v1"] = "adjustment-intent-v1"
    request_id: Identifier
    draft_version: int
    schedule_generated_at: AwareDatetime
    interpreted_at: AwareDatetime
    model: str
    selection: IntentSelection
    usage: ExplanationUsage

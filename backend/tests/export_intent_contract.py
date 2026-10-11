"""Offline cross-language contract: no external calls, no credentials."""
import json

from app.schemas.adjustment_intent import AdjustmentIntentResponse
from app.schemas.explanation import ExplanationUsage
from app.services.adjustment_intent import validate_intent
from tests.test_adjustment_intent import body, sample, selection

if __name__ == "__main__":
    schedule = sample()
    values = []
    for decision, reason in [("execute", "single_optional"), ("clarify", "unclear_request"), ("unsupported", "multiple_changes")]:
        result = AdjustmentIntentResponse(request_id="contract-1", draft_version=7,
            schedule_generated_at=schedule.generated_at, interpreted_at=schedule.generated_at,
            model="qwen3.7-flash-2026-07-15", usage=ExplanationUsage(),
            selection=validate_intent(selection(decision, reason=reason), schedule, body()["instruction"]))
        values.append(result.model_dump(mode="json"))
    print(json.dumps({"schedule": schedule.model_dump(mode="json"), "responses": values}))

"""Offline Python→TypeScript fixtures, never calls any external service."""
import json
from datetime import datetime, timezone

from app.schemas.explanation import ExplanationResponse, ExplanationUsage, ModelSelection
from app.services.explanation import build_facts, compact_schedule, fixed_limitations, render_selection
from tests.explanation_samples import business_samples

if __name__ == "__main__":
    output = []
    for name, schedule in business_samples().items():
        selected = ModelSelection.model_validate({"highlights": [{"message_code": "arrangement", "fact_ids": ["f0"]}]})
        result = ExplanationResponse(request_id=name, model="qwen3.7-flash-2026-07-15",
            schedule_generated_at=schedule.generated_at, explained_at=datetime.now(timezone.utc),
            highlights=render_selection(selected, build_facts(schedule)), limitations=fixed_limitations(schedule),
            usage=ExplanationUsage())
        from dataclasses import asdict
        output.append({"name": name, "schedule": schedule.model_dump(mode="json"), "facts": [asdict(fact) for fact in build_facts(schedule)],
                       "snapshot": compact_schedule(schedule), "explanation": result.model_dump(mode="json")})
    print(json.dumps(output, ensure_ascii=False))

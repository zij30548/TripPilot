"""Actual Python outputs for TS checks; real local corpus + controlled model only."""
import json
from app.integrations.qwen import MODEL
from app.schemas.explanation import ExplanationUsage
from app.services.knowledge_answer import render_answer
from tests.test_knowledge_answer import fixtures

rows = []
for question, venue, content in [
    ("数字馆专项预约", "museum_east", None),
    ("婴儿车和实时剩余名额", "museum_east", {"status":"partial", "answers":[{"text":"已收录资料说明借车需300元押金或有效证件，仅限馆内。", "evidence_ids":["E1"]}], "missing":["live_availability"]}),
    ("数字馆专项预约", "museum_people", {"status":"insufficient", "answers":[], "missing":["not_in_materials"]}),
    ("迪士尼烟花", "natural", {"status":"insufficient", "answers":[], "missing":["not_in_materials"]}),
]:
    q, r, default = fixtures(question, venue)
    result = render_answer(content or default, q, r, MODEL if r.matches else None, ExplanationUsage())
    rows.append(result.model_dump(mode="json"))
print(json.dumps(rows, ensure_ascii=False))

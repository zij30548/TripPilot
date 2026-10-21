"""Actual Pydantic chat-2 outputs for TypeScript guards; no HTTP/config/DB."""
import json

from app.schemas.chat_places import Batch, ChatPlacesWorkspace
from app.schemas.destination import LocatedPlace


def cases() -> list[dict]:
    identifier = "20000000-0000-4000-8000-000000000001"
    operation = "30000000-0000-4000-8000-000000000001"
    empty = ChatPlacesWorkspace(conversation_id=identifier, intent_revision=2, revision=0)
    outputs = [empty]
    for city_id, cityname, citycode, adcode in (("310000", "上海市", "021", "310106"),
                                               ("320500", "苏州市", "0512", "320508")):
        place = LocatedPlace(id="controlled-only", name="受控地点，不是真实搜索", address="受控地址", latitude=31.3,
            longitude=120.6, category=None, source="amap", region={"citycode": citycode, "cityname": cityname, "adcode": adcode})
        batch = Batch(id="40000000-0000-4000-8000-000000000001", city_id=city_id, keyword="受控地点", role="required",
                      queried_at="2026-10-21T00:00:00Z", places=[place])
        base = empty.model_dump() | {"city_id": city_id, "revision": 4, "batch": batch.model_dump(),
            "last_operation": {"operation_id": operation, "state": "succeeded", "error_code": None}}
        outputs.append(ChatPlacesWorkspace.model_validate(base))
        outputs.append(ChatPlacesWorkspace.model_validate(base | {"accommodation": place.model_dump(), "required_places": [place.model_dump()]}))
        outputs.append(ChatPlacesWorkspace.model_validate(base | {"stale": True, "batch": None}))
    for state in ("running", "failed", "cancelled", "uncertain"):
        outputs.append(ChatPlacesWorkspace.model_validate(empty.model_dump() | {
            "last_operation": {"operation_id": operation, "state": state, "error_code": None if state == "running" else "request_failed"}}))
    for reason in ("city_ambiguous", "city_confirmation_required", "multi_city", "missing_place", "unsupported"):
        outputs.append(ChatPlacesWorkspace.model_validate(empty.model_dump() | {"clarification": reason,
            "proposed_city_id": "320500" if reason == "city_confirmation_required" else None, "request_text": "受控请求"}))
    return [output.model_dump(mode="json") for output in outputs]


if __name__ == "__main__":
    print(json.dumps(cases(), ensure_ascii=False))

"""Temporary disk DB only. Export actual v2 API serialization for TS validation."""
import json
from tests.test_destinations import DestinationAPIs, modern_snapshot
from uuid import uuid4
from core.destinations import DESTINATIONS

case = DestinationAPIs()
case.setUp()
try:
    rows = []
    for city in DESTINATIONS:
        for sample in ("complete_walk", "transit_unknown_legs", "required_prefix", "three_days_blank"):
            response = case.client.post("/saved-trips", json={"operation_id": uuid4().hex, "name": "controlled-city", "snapshot": modern_snapshot(city, sample)})
            assert response.status_code == 200
            rows.append(response.json())
    print(json.dumps(rows, ensure_ascii=False))
finally:
    case.tearDown()
    case.doCleanups()

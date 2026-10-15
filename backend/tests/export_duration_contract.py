"""Actual Python serialization + disk-backed v3 contract, synthetic data only."""
import json
import tempfile
from pathlib import Path
from uuid import uuid4
from sqlmodel import Session
from app.destinations import DESTINATIONS
from app.planning_limits import LIMITS
from app.schemas.saved_trip import SaveTrip
from app.saved_trips import engine_for, save
from tests.test_duration import city_request, controlled_schedule, archive_for

with tempfile.TemporaryDirectory() as folder:
    engine=engine_for(str(Path(folder)/"duration.sqlite3"))
    rows=[]
    for city in DESTINATIONS:
        for mode in ["walking","transit"]:
            for optional in [1,3]:
                raw=archive_for(controlled_schedule(city_request(city,mode,optional=optional)))
                op=SaveTrip.model_validate_json(json.dumps({"operation_id":uuid4().hex,"name":"controlled duration","snapshot":raw}),strict=True)
                with Session(engine) as session: rows.append(save(session,op).model_dump(mode="json"))
    engine.dispose()
    print(json.dumps({"limits":LIMITS,"rows":rows},ensure_ascii=False))

"""Controlled disk-backed archives; never access the user's database or APIs."""
import json
import tempfile
from pathlib import Path
from sqlmodel import Session
from app.saved_trips import engine_for, save
from app.schemas.saved_trip import SaveTrip
from tests.test_saved_trips import payload
from tests.explanation_samples import business_samples

with tempfile.TemporaryDirectory() as folder:
    engine = engine_for(str(Path(folder) / "controlled.sqlite3"))
    results = []
    for name in business_samples():
        with Session(engine, expire_on_commit=False) as session:
            request = SaveTrip.model_validate_json(json.dumps(payload(name)), strict=True)
            results.append(save(session, request).model_dump(mode="json"))
    engine.dispose()
    print(json.dumps(results, ensure_ascii=False))

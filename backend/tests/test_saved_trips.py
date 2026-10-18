import copy
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from tests.business_client import BusinessTestClient as TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from app.saved_trips import ArchiveError, SavedTripRow, database_path, detail, engine_for, save
from app.schemas.saved_trip import SaveTrip, SavedSnapshot
from main import app
from tests.explanation_samples import business_samples
from tests.archive_owner import OWNER, seed_owner


def snapshot(name: str = "complete_walk") -> dict:
    result = business_samples()[name].model_dump(mode="json")
    req = result["request"]
    requirements = {k: req[k] for k in ("start_date", "end_date", "accommodation_place", "must_visit_places", "daily_start_time", "daily_end_time")}
    requirements.update(budget=3000, travelers=2, accommodation_location=req["accommodation_place"]["name"],
                        pace="balanced", interests=["摄影"], must_visit=[p["name"] for p in req["must_visit_places"]], avoid_places=[])
    return {"schema_version": "saved-trip-v1", "requirements": requirements, "schedule": result,
            "costs": {"tickets": {i["place_id"]: "" for d in result["days"] for i in d["items"] if i["kind"] == "visit"},
                      "meals": {d["date"]: "" for d in result["days"]}, "accommodation": "0", "other": "invalid"}}


def payload(name: str = "complete_walk") -> dict:
    return {"operation_id": uuid4().hex, "name": "受控存档", "snapshot": snapshot(name)}


class ArchiveContract(unittest.TestCase):
    def test_full_business_samples_roundtrip_and_money_text_unchanged(self):
        for name in business_samples():
            with self.subTest(name=name):
                raw = snapshot(name)
                parsed = SavedSnapshot.model_validate_json(json.dumps(raw), strict=True)
                self.assertEqual(parsed.costs.model_dump(), raw["costs"])
                self.assertEqual(parsed.schedule.model_dump(mode="json"), raw["schedule"])

    def test_invalid_fields_references_limits_and_versions(self):
        mutations = [
            lambda d: d.update(schema_version="saved-trip-v2"),
            lambda d: d["schedule"].update(extra=1),
            lambda d: d["schedule"]["days"][0]["items"][0].update(extra=1),
            lambda d: d["schedule"]["days"][0].update(return_time="23:59"),
            lambda d: d["schedule"]["edges"][0]["origin"].update(place_id="foreign"),
            lambda d: d["schedule"]["edges"][0].update(duration_seconds="600"),
            lambda d: d["requirements"].update(start_date="2026-10-09"),
            lambda d: d["requirements"].update(travelers=True),
            lambda d: d["costs"]["tickets"].update(preview_only="10"),
            lambda d: d["costs"]["meals"].update(unrelated="10"),
            lambda d: d["costs"].update(other="9"*257),
            lambda d: d["schedule"]["rules"].append("伪造规则"),
            lambda d: d["schedule"]["request"]["accommodation_place"].update(extra="x"),
        ]
        for mutate in mutations:
            raw = snapshot(); mutate(raw)
            with self.assertRaises(ValueError): SavedSnapshot.model_validate_json(json.dumps(raw), strict=True)


class ArchivePersistence(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "archives.sqlite3"
        seed_owner(engine_for(str(self.path)))
        app.dependency_overrides[database_path] = lambda: self.path
        self.client = TestClient(app)
    def tearDown(self):
        self.client.close(); app.dependency_overrides.clear()
        engine_for(str(self.path)).dispose(); engine_for.cache_clear(); self.directory.cleanup()

    def test_create_list_pagination_reopen_read_and_no_external_calls(self):
        with patch("httpx.AsyncClient.send", side_effect=AssertionError("No external HTTP")):
            ids = []
            for sample in ["complete_walk", "transit_unknown_legs", "required_prefix", "three_days_blank", "no_visits"]:
                response = self.client.post("/saved-trips", json=payload(sample))
                self.assertEqual(response.status_code, 200, response.text); ids.append(response.json()["id"])
            first = self.client.get("/saved-trips?limit=2").json()
            self.assertEqual([v["id"] for v in first["items"]], ids[::-1][:2]); self.assertTrue(first["has_more"])
            self.assertNotIn("snapshot", first["items"][0])
            self.assertEqual(len(self.client.get("/saved-trips?offset=4&limit=2").json()["items"]),1)
            self.assertEqual(self.client.get("/saved-trips?limit=21").status_code,422)
            engine_for(str(self.path)).dispose(); engine_for.cache_clear()
            self.assertEqual(self.client.get("/saved-trips/"+ids[0]).json()["snapshot"]["costs"]["other"],"invalid")
            self.assertEqual(len(self.client.get("/saved-trips").json()["items"]),5)
            self.assertEqual(self.client.get("/saved-trips/"+uuid4().hex).status_code,404)

    def test_idempotency_after_response_loss_and_conflicting_payload(self):
        body = payload()
        first = self.client.post("/saved-trips",json=body).json()  # response ignored by hypothetical client
        second = self.client.post("/saved-trips",json=body).json()
        self.assertEqual(first,second)
        body["name"] = "不同内容"
        self.assertEqual(self.client.post("/saved-trips",json=body).status_code,409)
        self.assertEqual(len(self.client.get("/saved-trips").json()["items"]),1)

    def test_concurrent_same_operation_uses_database_unique_constraint(self):
        engine = engine_for(str(self.path)); request = SaveTrip.model_validate_json(json.dumps(payload()),strict=True)
        def submit(_):
            with Session(engine,expire_on_commit=False) as session: return save(session,request,OWNER).id
        with ThreadPoolExecutor(max_workers=4) as pool: ids = list(pool.map(submit,range(4)))
        self.assertEqual(len(set(ids)),1)
        self.assertEqual(len(self.client.get("/saved-trips").json()["items"]),1)

    def test_commit_failure_rolls_back_and_safe_error(self):
        with patch.object(Session,"commit",side_effect=OperationalError("sensitive SQL",{},Exception("private path"))):
            result = self.client.post("/saved-trips",json=payload())
        self.assertEqual(result.status_code,503)
        self.assertNotIn("sensitive",result.text); self.assertNotIn("private",result.text)
        self.assertEqual(self.client.get("/saved-trips").json()["items"],[])

    def test_corruption_unsupported_version_and_unwritable_storage(self):
        row_id = self.client.post("/saved-trips",json=payload()).json()["id"]
        engine = engine_for(str(self.path))
        for content in ['{"broken":true}', json.dumps(snapshot()).replace('saved-trip-v1','saved-trip-v9')]:
            with Session(engine) as session:
                row=session.get(SavedTripRow,row_id); row.snapshot_json=content; session.add(row); session.commit()
            response=self.client.get("/saved-trips/"+row_id)
            self.assertEqual(response.status_code,422); self.assertEqual(response.json()["detail"]["code"],"invalid_record")
        with patch("app.saved_trips.engine_for",side_effect=OSError("private path")):
            response=self.client.get("/saved-trips")
        self.assertEqual(response.status_code,503); self.assertNotIn(str(self.path),response.text)

    def test_locked_disk_database_errors_safely_and_then_recovers(self):
        engine=engine_for(str(self.path))
        with engine.connect() as connection:
            connection.exec_driver_sql("BEGIN EXCLUSIVE")
            response=self.client.post("/saved-trips",json=payload())
            self.assertEqual(response.status_code,503)
            connection.rollback()
        self.assertEqual(self.client.get("/saved-trips").json()["items"],[])

    def test_duplicate_json_nonfinite_unknown_and_oversize_rejected(self):
        body=json.dumps(payload())
        for raw in [body[:-1]+',"name":"duplicate"}',body.replace('3000','NaN'),body.replace('3000','1e400'),body[:-1]+',"url":"https://bad.invalid"}']:
            self.assertEqual(self.client.post("/saved-trips",content=raw).status_code,422)
        self.assertEqual(self.client.post("/saved-trips",content=b'x'*(2*1024*1024+1)).status_code,413)
        self.assertEqual(self.client.get("/saved-trips").json()["items"],[])

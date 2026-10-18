"""Acceptance recorder checks: temporary evidence only, no real HTTP or user DB."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from app.integrations.amap_transit import AmapTransitClient
from app.schemas.transit import TransitRouteRequest
from tools.traffic_acceptance import RecordingTransport, Gate, freeze, prepare, scrub, freeze_repair, verify_repair, replay_transit, CASES
from app.schemas.destination import CityScheduleRequest
from app.schemas.schedule import ScheduleEdge
from app.services.schedule_routes import required_edges
from app.services.schedule import build_schedule
from tests.test_schedule import NOW
from tests.test_transit import sample_request, sample_upstream
from tests.test_schedule_optional import payload


def controlled_input() -> dict:
    value = payload(2, 0, transport_mode="transit", start_date="2026-10-20", end_date="2026-10-20")
    value["destination_id"] = "shanghai"
    for place in [value["accommodation_place"], *value["must_visit_places"]]:
        place["region"] = {"citycode": "021", "cityname": "上海市", "adcode": "310106"}
    for setting in value["duration_settings"]:
        setting.update(minutes=90, source="user")
    return value


class RecorderTests(unittest.IsolatedAsyncioTestCase):
    async def test_passthrough_preserves_response_and_counts_each_existing_adapter_retry(self) -> None:
        calls = []
        def handle(req: httpx.Request) -> httpx.Response:
            calls.append(req)
            if len(calls) == 1:
                raise httpx.ConnectError("sensitive-key-should-not-log", request=req)
            return httpx.Response(200, json=sample_upstream())
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            async with httpx.AsyncClient(transport=RecordingTransport(path, ("sensitive-key-should-not-log",), httpx.MockTransport(handle))) as client:
                result = await AmapTransitClient(client, "sensitive-key-should-not-log").transit(TransitRouteRequest.model_validate(sample_request()))
            self.assertEqual(result.status, "ok")
            self.assertEqual(result.route.duration_seconds, 1201)
            rows = [json.loads(line) for line in (path / "attempts.jsonl").read_text().splitlines()]
            self.assertEqual(sum(row["event"] == "http_started" for row in rows), 2)
            self.assertEqual(sum(row["event"] == "http_failed" for row in rows), 1)
            self.assertEqual(calls[0].url.params["key"], "sensitive-key-should-not-log")
            self.assertNotIn("sensitive-key-should-not-log", "".join(p.read_text() for p in path.iterdir()))
            original = json.loads(next(path.glob("upstream-*.json")).read_text())
            self.assertEqual(original, sample_upstream())

    async def test_sent_cancellation_is_counted_without_retry(self) -> None:
        sent = asyncio.Event()
        async def handle(req: httpx.Request) -> httpx.Response:
            sent.set()
            await asyncio.Future()
            raise AssertionError("unreachable")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            async with httpx.AsyncClient(transport=RecordingTransport(path, (), httpx.MockTransport(handle))) as client:
                task = asyncio.create_task(client.get("https://restapi.amap.com/v3/direction/walking"))
                await sent.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            rows = [json.loads(line) for line in (path / "attempts.jsonl").read_text().splitlines()]
            self.assertEqual([r["event"] for r in rows], ["http_started", "http_failed"])
            self.assertEqual(rows[-1]["category"], "cancelled")

    async def test_prepared_request_runs_once_and_unknown_outcome_does_not_allow_resend(self) -> None:
        calls = []
        async def app(scope, receive, send):
            calls.append(await receive())
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b'{"controlled":true}'})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            freeze(path)
            prepare("c01", controlled_input(), path)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=Gate(app, path)), base_url="http://test") as client:
                result = await client.post("/trips/schedule-preview", json=controlled_input())
                self.assertEqual(result.status_code, 200)
                with self.assertRaises(FileExistsError):
                    await client.post("/trips/schedule-preview", json=controlled_input())
                denied = await client.post("/trips/schedule-explanation", json={})
                self.assertEqual(denied.status_code, 403)
                self.assertEqual((await client.post("/saved-trips", json={})).status_code, 403)
            self.assertEqual(len(calls), 1)
            self.assertTrue((path / "c01-input.json").exists())
            self.assertTrue((path / "c01-attempt.json").exists())
            self.assertTrue((path / "c01-response.json").exists())

    async def test_invalid_case_or_changed_payload_rejected_before_dispatch(self) -> None:
        async def app(*_args):
            raise AssertionError("must not dispatch")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            freeze(path)
            with self.assertRaises(ValueError):
                prepare("c01", controlled_input() | {"transport_mode": "walking"}, path)
            prepare("c01", controlled_input(), path)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=Gate(app, path)), base_url="http://test") as client:
                with self.assertRaises(ValueError):
                    await client.post("/trips/schedule-preview", json=controlled_input() | {"daily_end_time": "19:00"})
            self.assertFalse((path / "c01-attempt.json").exists())
            with self.assertRaises(FileExistsError):
                freeze(path)

    async def test_scrub_removes_credentials_even_in_unknown_keys_and_strings(self) -> None:
        secret = "a-sensitive-test-value"
        actual = scrub({"key": secret, "Authorization": secret, f"unknown-{secret}": [f"value {secret}"]}, (secret,))
        self.assertNotIn(secret, json.dumps(actual))
        self.assertNotIn("Authorization", actual)
        self.assertIn("unknown-[redacted]", actual)

    def initial_records(self, path: Path) -> None:
        freeze(path)
        (path / "attempts.jsonl").write_text('{"event":"controlled-original"}\n')
        for case in CASES:
            for suffix in ["input", "attempt", "response"]:
                (path / f"{case['id']}-{suffix}.json").write_text(json.dumps(controlled_input() if suffix == "input" else {"controlled": suffix}))

    async def test_one_repair_submission_preserves_initial_outcomes_and_denies_other_operations(self) -> None:
        calls = []
        async def app(scope, receive, send):
            calls.append(await receive())
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b'{"controlled":true}'})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            self.initial_records(path)
            before = {p.name: p.read_bytes() for p in path.iterdir()}
            freeze_repair(path)
            repair = path / "repair-c02"
            verify_repair(repair)
            with self.assertRaises(FileExistsError):
                freeze_repair(path)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=Gate(app, repair, repair=True)), base_url="http://test") as client:
                self.assertEqual((await client.get("/places/search")).status_code, 403)
                self.assertEqual((await client.post("/trips/schedule-preview", json=controlled_input())).status_code, 200)
                with self.assertRaises(FileExistsError):
                    await client.post("/trips/schedule-preview", json=controlled_input())
            self.assertEqual(len(calls), 1)
            self.assertEqual(before, {name: (path / name).read_bytes() for name in before})

    async def test_repair_rejects_changed_original_ledger_input_and_frozen_version(self) -> None:
        for name in ["attempts.jsonl", "c02-input.json", "repair-c02/c02-input.json"]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                path = Path(folder)
                self.initial_records(path)
                freeze_repair(path)
                with (path / name).open("a") as stream:
                    stream.write(" ")
                if name.startswith("repair-"):
                    # An actual changed value, not insignificant JSON whitespace.
                    (path / name).write_text(json.dumps(controlled_input() | {"daily_end_time": "19:00"}))
                with self.assertRaises(ValueError):
                    verify_repair(path / "repair-c02")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            self.initial_records(path)
            freeze_repair(path)
            protocol = path / "repair-c02/protocol.json"
            value = json.loads(protocol.read_bytes())
            value["files"][next(iter(value["files"]))] = "changed"
            protocol.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                verify_repair(path / "repair-c02")

    async def test_offline_replay_uses_only_saved_directed_bodies_and_cannot_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            rows = []
            for case_id in ["c01", "c02"]:
                value = controlled_input()
                request = CityScheduleRequest.model_validate(value)
                edges = []
                for i, (origin, destination) in enumerate(required_edges(request)):
                    edges.append(ScheduleEdge(id=f"e{i}", origin=origin, destination=destination,
                                              transport_mode="transit", status="data_error", queried_at=NOW))
                    operation = f"{case_id}-{i}"
                    artifact = f"controlled-{operation}.json"
                    upstream = sample_upstream()
                    upstream["route"]["transits"][0]["segments"][0]["walking"]["steps"].insert(1, {
                        "instruction": "受控重复点", "polyline": "121,31;121,31"})
                    (path / artifact).write_text(json.dumps(upstream))
                    key = lambda p: f"{p.longitude:.6f},{p.latitude:.6f}"
                    rows.extend([
                        {"event": "http_started", "case": case_id, "operation": operation,
                         "parameters": {"origin": key(origin), "destination": key(destination)}},
                        {"event": "http_completed", "operation": operation, "artifact": artifact},
                    ])
                (path / f"{case_id}-input.json").write_text(json.dumps(value))
                (path / f"{case_id}-response.json").write_text(json.dumps({
                    "body": {"schedule": build_schedule(request, edges, NOW).model_dump(mode="json")}}))
            (path / "attempts.jsonl").write_text("\n".join(json.dumps(row) for row in rows))
            with patch("httpx.AsyncClient", side_effect=AssertionError("offline replay must not create a network client")):
                replay_transit(path)
            for case_id in ["c01", "c02"]:
                result = json.loads((path / f"{case_id}-offline-replay.json").read_bytes())
                self.assertIn("not-new-live", result["kind"])
                schedule = result["schedule"]
                self.assertEqual(schedule["status"], "complete")
                self.assertEqual(len(schedule["edges"]), 5)
                self.assertTrue(all(e["duration_seconds"] == 1201 and e["duration_minutes"] == 21
                                    and not e["transit_route"]["geometry_complete"] for e in schedule["edges"]))
                self.assertEqual(schedule["generated_at"], NOW.isoformat().replace("+00:00", "Z"))
            with self.assertRaises(FileExistsError):
                replay_transit(path)

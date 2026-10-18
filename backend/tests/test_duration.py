"""Seven-day boundaries and frozen archive semantics. No user DB or upstream I/O."""
import asyncio
import copy
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from sqlmodel import Session

from app.destinations import DESTINATIONS
from app.planning_limits import LIMITS
from app.schemas.destination import CityScheduleRequest, CityScheduleResponse, CityTripRequest
from app.schemas.explanation import ScheduleSnapshot
from app.schemas.adjustment_intent import AdjustmentIntentRequest
from app.schemas.itinerary_query import ItineraryQueryRequest
from app.schemas.saved_trip import SaveTrip, snapshot_adapter
from app.schemas.schedule import ScheduleEdge, ScheduleRequest
from app.schemas.weather import CityWeatherRequest
from app.saved_trips import engine_for, save, detail, SavedTripRow
from tests.archive_owner import OWNER, seed_owner
from app.integrations.amap_walking import AmapWalkingClient
from app.integrations.amap_transit import AmapTransitClient
from app.integrations.amap_weather import convert_forecast
from app.services.explanation import compact_schedule, validate_snapshot
from app.services.schedule import build_required_schedule, build_schedule
from app.services.schedule_routes import ScheduleRouteCollector, required_edges, optional_edges, unique_pairs
from app.services.schedule_drawing import drawing_for
from tests.test_destinations import modern_snapshot
from tests.test_schedule import NOW
from tests.test_schedule_optional import payload, ok_route
from tests.test_transit import sample_upstream as transit_data
from tests.test_weather import sample_upstream as weather_data, QUERY_TIME


def city_request(city="hangzhou", mode="walking", required=6, optional=3, days=7, start="2026-12-28"):
    raw = payload(required, optional, start_date=start,
                  end_date=(date.fromisoformat(start)+timedelta(days=days-1)).isoformat(),
                  daily_end_time="12:00", lunch={"enabled": False, "start_time": "12:00", "end_time": "13:00"},
                  destination_id=city, transport_mode=mode)
    d = DESTINATIONS[city]
    for p in [raw["accommodation_place"], *raw["must_visit_places"], *raw["optional_places"]]:
        p["region"] = {"citycode": d.citycode, "cityname": d.official_name, "adcode": d.district_adcodes[1]}
    for setting in raw["duration_settings"][:required]:
        setting.update(minutes=160, source="user")
    return CityScheduleRequest.model_validate(raw)


def controlled_schedule(req, budget=True):
    """Canonical deterministic fixture, not a claimed upstream sample."""
    core = req.core()
    pairs = required_edges(core)
    def edge(i, pair):
        same = pair[0] == pair[1]
        fields = dict(id=f"e{i}", origin=pair[0], destination=pair[1], queried_at=NOW, transport_mode=req.transport_mode)
        if budget and i >= LIMITS["max_started_routes"]:
            return ScheduleEdge(**fields, status="budget_exhausted", message="受控预算耗尽，未查询")
        route = None
        if req.transport_mode == "transit" and not same:
            # Reuse a real normalized fixture with unknown leg time/fare/geometry.
            route = copy.deepcopy(modern_snapshot(sample="transit_unknown_legs")["schedule"]["edges"][0]["transit_route"])
            route["duration_seconds"] = 600
        return ScheduleEdge(**fields, status="same_place" if same else "ok", source="same_place" if same else "amap",
                            duration_seconds=0 if same else 600, duration_minutes=0 if same else 10,
                            distance_meters=(0 if same else 600) if req.transport_mode == "walking" else None,
                            transit_route=route, selection_rule="first_supported_complete" if route else None)
    edges = [edge(i, p) for i,p in enumerate(pairs)]
    baseline = build_required_schedule(core, edges, NOW)
    if not baseline.unscheduled:
        pairs = unique_pairs(pairs + optional_edges(core, baseline.days))
    result = build_schedule(core, [edge(i,p) for i,p in enumerate(pairs)], NOW)
    return CityScheduleResponse(**{**result.model_dump(), "request": req})


def archive_for(schedule):
    req = schedule.request
    requirements = {k: req.model_dump(mode="json")[k] for k in ("destination_id", "start_date", "end_date", "daily_start_time", "daily_end_time", "accommodation_place", "must_visit_places")}
    requirements.update(budget=3000.0, travelers=2, accommodation_location=req.accommodation_place.name,
                        pace="balanced", interests=[], must_visit=[p.name for p in req.must_visit_places], avoid_places=[])
    requirements = CityTripRequest.model_validate(requirements).model_dump(mode="json")
    return {"schema_version": "saved-trip-v3", "requirements": requirements, "schedule": schedule.model_dump(mode="json"),
            "costs": {"tickets": {i.place_id: "" for d in schedule.days for i in d.items if i.kind == "visit"},
                      "meals": {d.date.isoformat(): "" for d in schedule.days}, "accommodation": "0", "other": "invalid"}}


class DurationContracts(unittest.TestCase):
    def test_calendar_current_and_legacy_versions(self):
        for start in ["2026-01-30", "2026-12-28", "2028-02-27", "0001-12-29"]:
            for n in [1,3,4,7]:
                req = city_request(days=n, start=start)
                self.assertEqual((req.end_date-req.start_date).days+1,n)
                if n>3:
                    with self.assertRaises(ValueError): ScheduleRequest.model_validate(req.core().model_dump(mode="json"))
        for start,end in [("2026-01-01","2026-01-08"),("2026-02-29","2026-03-01"),("2026-01-03","2026-01-01"),("0000-01-01","0000-01-02")]:
            raw=city_request().model_dump(mode="json"); raw.update(start_date=start,end_date=end)
            with self.assertRaises(ValueError): CityScheduleRequest.model_validate(raw)

    def test_six_cities_both_modes_actual_day_seven_visit_and_41_edges(self):
        for city in DESTINATIONS:
            for mode in ["walking","transit"]:
                for optional in [1,3]:
                    result=controlled_schedule(city_request(city,mode,optional=optional))
                    self.assertEqual(len(result.days),7)
                    self.assertEqual([d.items[1].place_id for d in result.days[:6]], [f"p{i}" for i in range(1,7)])
                    if optional==1:
                        self.assertEqual(result.days[6].items[1].place_id,"o1")
                    else:
                        self.assertEqual(len(result.edges),41)
                        self.assertEqual(result.days[6].items,[])
                        self.assertTrue(all(o.attempts[-1].outcome=="budget_exhausted" for o in result.optional_results))
                        self.assertEqual(sum(e.status=="ok" for e in result.edges),29)
                    self.assertEqual(result.unscheduled,[])
                    self.assertTrue(all(d.return_time is None or d.return_time<= "12:00" for d in result.days))
                    compact=ScheduleSnapshot.model_validate_json(json.dumps(compact_schedule(result)))
                    validate_snapshot(compact)
                    raw=archive_for(result)
                    self.assertEqual(snapshot_adapter.validate_json(json.dumps(raw),strict=True).model_dump(mode="json"),raw)
                    # Same bytes cannot masquerade as a legacy archive.
                    raw["schema_version"]="saved-trip-v2"
                    with self.assertRaises(ValueError): snapshot_adapter.validate_json(json.dumps(raw),strict=True)

    def test_lunch_wait_boundary_required_prefix_unchanged_and_zero_movement(self):
        raw=city_request(optional=1).model_dump(mode="json")
        raw.update(daily_end_time="13:30",lunch={"enabled":True,"start_time":"12:00","end_time":"13:00"})
        req=CityScheduleRequest.model_validate(raw); result=controlled_schedule(req)
        self.assertEqual(result.days[3].items[1].place_id,"p4")
        for day in result.days:
            for item in day.items:
                if item.kind in ("visit","walk","transit"):
                    self.assertFalse(item.start_time<"13:00" and item.end_time>"12:00")
        raw=city_request(required=1,optional=0).model_dump(mode="json")
        raw["must_visit_places"]=[copy.deepcopy(raw["accommodation_place"])]; raw["duration_settings"]=[{"place_id":"stay","minutes":60,"source":"default"}]
        result=controlled_schedule(CityScheduleRequest.model_validate(raw))
        self.assertEqual(len(result.edges),1); self.assertEqual(result.edges[0].status,"same_place")
        self.assertEqual(result.days[0].items[1].kind,"visit")

    def test_weather_one_batch_seven_dates_partial_no_fill(self):
        req=CityWeatherRequest.model_validate({"destination_id":"shanghai","start_date":"2026-12-31","end_date":"2027-01-06"})
        data=weather_data(); response=convert_forecast(data,req,QUERY_TIME)
        dates={c["date"] for c in data["forecasts"][0]["casts"]}
        self.assertEqual(len(response.days),7)
        for day in response.days:
            self.assertEqual(day.status,"available" if day.date.isoformat() in dates else "unavailable")
        self.assertTrue(all(d.day is None for d in response.days if d.status=="unavailable"))

    def test_v3_persists_reopens_idempotently_and_v2_remains_unchanged(self):
        old=modern_snapshot(); before=json.dumps(old,sort_keys=True)
        with tempfile.TemporaryDirectory() as folder:
            path=str(Path(folder)/"duration.sqlite3"); engine=engine_for(path)
            seed_owner(engine)
            records=[]
            for raw in [old,archive_for(controlled_schedule(city_request()))]:
                op=SaveTrip.model_validate_json(json.dumps({"operation_id":uuid4().hex,"name":"duration controlled","snapshot":raw}),strict=True)
                with Session(engine) as session:
                    first=save(session,op,OWNER); second=save(session,op,OWNER)
                    self.assertEqual(first.id,second.id); records.append(first)
            engine.dispose(); engine_for.cache_clear(); reopened=engine_for(path)
            with Session(reopened) as session:
                for row in records: self.assertEqual(detail(session.get(SavedTripRow,row.id)).model_dump(),row.model_dump())
            reopened.dispose(); engine_for.cache_clear()
        self.assertEqual(json.dumps(old,sort_keys=True),before)

    def test_ai_snapshot_and_tool_calendar_seven_days_without_model_calls(self):
        result=controlled_schedule(city_request())
        compact=compact_schedule(result)
        intent=AdjustmentIntentRequest.model_validate_json(json.dumps({"contract_version":"adjustment-intent-v1",
            "request_id":"controlled","draft_version":1,"instruction":"不去可选地点","snapshot":compact}))
        validate_snapshot(intent.snapshot)
        dates=[d.date.isoformat() for d in result.days]
        query={"contract_version":"itinerary-query-v1","request_id":"controlled","draft_version":1,
               "cost_version":1,"question":"看看第七天","dates":dates}
        self.assertEqual(ItineraryQueryRequest.model_validate(query).dates,dates)
        query["dates"] += ["2027-01-04"]
        with self.assertRaises(ValueError): ItineraryQueryRequest.model_validate(query)
        compact["edges"][-1]["status"]="failed"
        # Thirty started outcomes cannot be laundered through the AI compact boundary.
        with self.assertRaises(ValueError): validate_snapshot(ScheduleSnapshot.model_validate_json(json.dumps(compact)))


class DurationBudget(unittest.IsolatedAsyncioTestCase):
    async def test_41_logical_29_started_deterministic_two_modes_and_two_attempts(self):
        for mode in ["walking","transit"]:
            calls={}; order=[]
            async def handle(req):
                pair=(req.url.params["origin"],req.url.params["destination"])
                calls[pair]=calls.get(pair,0)+1
                if calls[pair]==1:
                    order.append(pair); raise httpx.ConnectError("controlled",request=req)
                await asyncio.sleep(0.001 if len(order)%2 else 0.002)
                if mode=="transit":
                    data=transit_data()
                    # Existing adapter's normalized total; not a real API call.
                    data["route"]["transits"][0]["duration"]="600"
                else: data={"status":"1","route":{"paths":[{"distance":"600","duration":"600","steps":[{"polyline":"121.4,31.2;121.41,31.2"}]}]}}
                return httpx.Response(200,json=data)
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
                adapter=AmapWalkingClient(http,"controlled") if mode=="walking" else AmapTransitClient(http,"controlled")
                with patch("app.services.schedule_routes.MIN_ROUTE_START_INTERVAL_SECONDS",0):
                    req=city_request(mode=mode).core(); collector=ScheduleRouteCollector(adapter,mode)
                    first=await collector.collect(required_edges(req)); baseline=build_required_schedule(req,first,NOW)
                    self.assertFalse(baseline.unscheduled)
                    final=await collector.collect(optional_edges(req,baseline.days))
                    self.assertEqual(len(final),41); self.assertEqual(len(collector.started),29)
                    self.assertEqual(sum(calls.values()),58); self.assertEqual(len(order),29)
                    self.assertEqual(collector.started,set(range(29)))
                    self.assertTrue(all(e.status=="budget_exhausted" for e in final[29:]))
                    again=await collector.collect(optional_edges(req,baseline.days))
                    self.assertEqual(again,final); self.assertEqual(sum(calls.values()),58)

    async def test_shared_deadline_not_reset_and_cancel_cleans_pending(self):
        req=city_request().core(); adapter=AsyncMock(spec=AmapWalkingClient); adapter.walking.return_value=ok_route()
        with patch("app.services.schedule_routes.MIN_ROUTE_START_INTERVAL_SECONDS",0):
            collector=ScheduleRouteCollector(adapter)
            edges=await collector.collect(required_edges(req)); baseline=build_required_schedule(req,edges,NOW)
            collector.deadline=0
            all_edges=await collector.collect(optional_edges(req,baseline.days))
            self.assertEqual(adapter.walking.call_count,17)
            self.assertTrue(all(e.status=="budget_exhausted" for e in all_edges[17:]))
            self.assertFalse(build_schedule(req,all_edges,NOW).unscheduled)
            entered=asyncio.Event(); cleaned=asyncio.Event()
            async def waiting(_):
                entered.set()
                try: await asyncio.Event().wait()
                finally: cleaned.set()
            adapter.walking.side_effect=waiting
            collector=ScheduleRouteCollector(adapter)
            task=asyncio.create_task(collector.collect(required_edges(req)))
            await entered.wait(); task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
            self.assertTrue(cleaned.is_set()); self.assertEqual(len(asyncio.all_tasks()),1)

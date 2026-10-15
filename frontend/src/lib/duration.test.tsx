import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { act, render, renderHook, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { PLANNING_LIMITS } from "./planning-limits";
import { isSavedDetail, isSavedSnapshot, createSnapshot, type SavedDetail } from "./saved-trips";
import { isScheduleRequest, isScheduleResponse, scheduleDates } from "@/types/schedule";
import { restoreSavedTrip, applyEditedRequirements } from "./restore-trip";
import { explanationSnapshot } from "./explanation-api";
import { calculateScheduleCosts, emptyCostInputs } from "./schedule-costs";
import { buildRequest, eligibleOptional } from "./schedule-input";
import { useSchedulePreview } from "./use-schedule-preview";
import { getWeatherDates, isWeatherForecastRequest } from "@/types/weather";
import { ScheduleResult } from "@/components/trip/schedule-preview";
import { deferred } from "./weather-schedule-check.test-fixtures";
import { savedFixture, savedCityFixture } from "./restore-trip.test-fixtures";
import { scheduleMapData } from "./schedule-map";
import { useScheduleMap } from "./use-schedule-map";

const api=vi.hoisted(()=>vi.fn());
vi.mock("./schedule-api",async original=>({...await original<typeof import("./schedule-api")>(),querySchedulePreview:api}));
const backend=resolve(process.cwd(),"../backend");
const exported: {limits:typeof PLANNING_LIMITS;rows:SavedDetail[]}=JSON.parse(execFileSync(`${backend}/.venv/bin/python`,["-B","tests/export_duration_contract.py"],
  {cwd:backend,env:{...process.env,PYTHONPATH:backend,PYTHONDONTWRITEBYTECODE:"1"},encoding:"utf8",maxBuffer:8*1024*1024}));
beforeEach(()=>vi.resetAllMocks());

describe("duration-1 shared calendar, budgets and archive contracts",()=>{
  it("uses identical shared current limits without changing optional capacity or network budget",()=>{
    expect(exported.limits).toEqual(PLANNING_LIMITS);
    expect(PLANNING_LIMITS).toMatchObject({max_trip_days:7,max_optional_places:3,max_required_places:6,max_logical_edges:41,max_started_routes:29,max_upstream_attempts:58});
    for(const [start,end,n] of [["2026-01-31","2026-02-06",7],["2026-12-30","2027-01-02",4],["2028-02-28","2028-03-01",3],["0001-12-31","0001-12-31",1]] as const)
      expect(scheduleDates(start,end)).toHaveLength(n);
    for(const [start,end] of [["2026-01-01","2026-01-08"],["2026-02-29","2026-03-01"],["2026-01-02","2026-01-01"],["0000-01-01","0000-01-01"]]) expect(scheduleDates(start,end)).toBeNull();
  });
  it("accepts actual Python v3 disk outputs for six cities × two modes, restores and projects identical snapshots",()=>{
    expect(exported.rows).toHaveLength(24);
    for(const row of exported.rows){
      expect(isSavedDetail(row)).toBe(true);
      const {inputs}=restoreSavedTrip(row,"session"); const s=row.snapshot.schedule;
      expect(buildRequest(inputs.request,inputs.settings,eligibleOptional(inputs.request,inputs.candidates))).toEqual(s.request);
      expect(isScheduleResponse(s,s.request)).toBe(true);
      expect(createSnapshot(inputs.request,s,inputs.costs)).toEqual(row.snapshot);
      expect(explanationSnapshot(s).days).toHaveLength(7);
      expect(getWeatherDates({destination_id:inputs.request.destination_id,start_date:inputs.request.start_date,end_date:inputs.request.end_date})).toHaveLength(7);
      for(const schema_version of ["saved-trip-v1","saved-trip-v2"]){
        expect(isSavedSnapshot({...row.snapshot,schema_version})).toBe(false);
      }
      const broken=structuredClone(s);broken.edges.at(-1)!.queried_at="bad";expect(isScheduleResponse(broken,s.request)).toBe(false);
    }
    expect(isSavedDetail(savedFixture())).toBe(true);expect(isSavedDetail(savedCityFixture())).toBe(true);
  });
  it("rejects an eighth day, phantom 30th started edge, and keeps all seven meal dates including empty days",()=>{
    const s=structuredClone(exported.rows[1].snapshot.schedule);
    expect(s.edges).toHaveLength(41);expect(s.days[6].items).toEqual([]);
    const bad=structuredClone(s);bad.edges[29].status="failed";expect(isScheduleResponse(bad,bad.request)).toBe(false);
    expect(isScheduleRequest({...s.request,end_date:"2027-01-04"})).toBe(false);
    expect(isWeatherForecastRequest({destination_id:"hangzhou",start_date:"2026-12-28",end_date:"2027-01-04"})).toBe(false);
    const costs=calculateScheduleCosts(s,exported.rows[1].snapshot.requirements,emptyCostInputs());
    expect(costs.status).toBe("ready");
    if(costs.status==="ready")expect(costs.user.filter(c=>c.category==="meal").map(c=>c.date)).toEqual(s.days.map(d=>d.date));
  });
  it("seven-day navigation displays real day-four/seven visits, then empty dates without inventing POIs",()=>{
    const response=exported.rows[0].snapshot.schedule;
    const view=render(<ScheduleResult response={response}/>);
    fireEvent.click(screen.getByRole("button",{name:/草案第 4 天/}));
    expect(screen.getByRole("region",{name:"2026-12-31 行程草案"}).textContent).toContain("测试地点-p4");
    fireEvent.click(screen.getByRole("button",{name:/草案第 7 天/}));
    expect(screen.getByRole("region",{name:"2027-01-03 行程草案"}).textContent).toContain("测试地点-o1");
    view.rerender(<ScheduleResult response={exported.rows[1].snapshot.schedule}/>);
    fireEvent.click(screen.getByRole("button",{name:/草案第 7 天/}));
    expect(screen.getByRole("region",{name:"2027-01-03 行程草案"}).textContent).toContain("尚未安排景点");
  });
  it("day 4–7 maps follow actual visits; changing dates clears selection and stale callbacks without a request",()=>{
    const s=exported.rows[1].snapshot.schedule;
    expect(scheduleMapData(s,s.days[3].date).visits.map(v=>v.placeId)).toEqual(["p4"]);
    expect(scheduleMapData(s,s.days[6].date).visits).toEqual([]);
    const {result}=renderHook(()=>useScheduleMap(s,{isCurrent:()=>true}));
    act(()=>result.current.selectDate(s.days[3].date));
    const old=result.current;
    act(()=>result.current.selectDate(s.days[6].date));
    expect(result.current.selection).toBeNull();expect(result.current.data.visits).toEqual([]);
    act(()=>old.selectDate(s.days[4].date));expect(result.current.date).toBe(s.days[6].date);
    expect(api).not.toHaveBeenCalled();
  });
  it("date edits are transactional: cancel untouched, confirm clears all estimates without moving meal values",()=>{
    const {inputs}=restoreSavedTrip(exported.rows[0],"s"); const before=structuredClone(inputs);
    const edit={...inputs.request,end_date:"2026-12-31"};
    expect(inputs).toEqual(before);
    const result=applyEditedRequirements(inputs,edit);
    expect(result.inputs.costs).toEqual(emptyCostInputs());expect(result.notices.join()).toContain("日期或人数已变化");expect(inputs).toEqual(before);
  });
  it.each(["success","error"])("rule ABA cancels seven-day query and blocks old %s/finally, no automatic generation",async kind=>{
    const row=exported.rows[0],{inputs}=restoreSavedTrip(row,"s");const old=deferred();const recent=deferred();api.mockReturnValueOnce(old.promise).mockReturnValueOnce(recent.promise);
    const {result,unmount}=renderHook(()=>useSchedulePreview(inputs.request,undefined,inputs));
    let work:Promise<boolean>;act(()=>{work=result.current.query();});const signal=api.mock.calls[0][1] as AbortSignal;
    act(()=>{result.current.setTransportMode("transit");result.current.setTransportMode("walking");});
    expect(signal.aborted).toBe(true);expect(result.current.state.status).toBe("idle");expect(api).toHaveBeenCalledTimes(1);
    let next:Promise<boolean>;act(()=>{next=result.current.query();});
    await act(async()=>{if(kind==="success")old.resolve(row.snapshot.schedule);else old.reject(new Error("old"));await work!;});
    expect(result.current.state.status).toBe("loading");
    await act(async()=>{recent.resolve(row.snapshot.schedule);await next!;});expect(result.current.capture()?.original.days).toHaveLength(7);
    unmount();expect(api).toHaveBeenCalledTimes(2);
  });
});

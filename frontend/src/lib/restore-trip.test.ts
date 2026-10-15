import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { restoreSavedTrip, applyEditedRequirements, restoredEstimateRows } from "./restore-trip";
import { savedCityFixture as savedFixture } from "./restore-trip.test-fixtures";
import { required as oldRequired, optionals as oldOptionals, home as oldHome } from "./weather-schedule-check.test-fixtures";
import { located } from "./destination.test-fixtures";
const required = located(oldRequired), optionals = oldOptionals.map(p => located(p)), home = located(oldHome);
import { buildRequest, eligibleOptional, validateScheduleInput } from "./schedule-input";
import { isSavedDetail } from "./saved-trips";
import type { SavedDetail } from "./saved-trips";

describe("v1 archives → independent inputs, never a successful result", () => {
  it("restores every existing disk-backed Python business case including transit, prefixes, only optional, no visits and blank dates", () => {
    const backend=resolve(process.cwd(),"../backend");
    const records: SavedDetail[] = JSON.parse(execFileSync(`${backend}/.venv/bin/python`, ["-B","tests/export_saved_contract.py"], {cwd:backend,env:{...process.env,PYTHONPATH:backend,PYTHONDONTWRITEBYTECODE:"1"},encoding:"utf8"}));
    expect(records).toHaveLength(12);
    for (const record of records) {
      expect(isSavedDetail(record)).toBe(true);
      const copy=restoreSavedTrip(record,"new-session"), input=copy.inputs;
      expect(input.request).toEqual({ ...record.snapshot.requirements, destination_id: "shanghai" });expect(input.costs).toEqual(record.snapshot.costs);
      expect(input.candidates.response).toBeNull();expect(input.candidates.lastAttempt).toBeNull();expect(input.candidates.excludedIds.size).toBe(0);
      expect(input.candidates.history?.places).toEqual([]);
      expect(validateScheduleInput(input.request,input.settings,eligibleOptional(input.request,input.candidates),false)).toContain("重新");
      expect(copy.source).toEqual(record);
      expect(Object.keys(input).sort()).toEqual(["candidates","costs","request","settings"]);
    }
  });
  it("deep-copies source/input independently, preserves default/user and disabled non-default lunch clocks",()=>{
    const record=savedFixture({lunch:{enabled:false,start_time:"11:20",end_time:"12:10"},duration_settings:[{place_id:required.id,minutes:75,source:"user"},{place_id:optionals[0].id,minutes:60,source:"default"}]});
    const saved=structuredClone(record),a=restoreSavedTrip(record,"one"),b=restoreSavedTrip(record,"two");
    expect(buildRequest(a.inputs.request,a.inputs.settings,eligibleOptional(a.inputs.request,a.inputs.candidates))?.lunch).toEqual(record.snapshot.schedule.request.lunch);
    a.inputs.request.must_visit_places![0].name="changed";a.inputs.candidates.history!.places[0].address="changed";a.inputs.costs.other="0";
    expect(record).toEqual(saved);expect(a.source).toEqual(saved);expect(b.source).toEqual(saved);expect(a.sessionId).not.toBe(b.sessionId);
    expect(b.inputs.settings.stays[required.id]).toEqual({value:"75",source:"user"});
  });
  it.each(["version","reference","extra"])("refuses %s without partial initialization", kind=>{
    const record=savedFixture();if(kind==="version")Object.assign(record.snapshot,{schema_version:"future"});
    if(kind==="reference")record.snapshot.schedule.days[0].items[0].edge_id="foreign";
    if(kind==="extra")Object.assign(record.snapshot,{weather:{}});
    expect(()=>restoreSavedTrip(record,"session")).toThrow("未创建");
  });
  it.each(["date","travelers"])("clears all estimates on %s confirmation but keeps place order/stays/exclusions",kind=>{
    const {inputs}=restoreSavedTrip(savedFixture(),"one");inputs.candidates.excludedIds=new Set([optionals[0].id]);
    const request={...inputs.request,...(kind==="date"?{start_date:"2026-10-11",end_date:"2026-10-11"}:{travelers:3})};
    const applied=applyEditedRequirements(inputs,request);
    expect(applied.inputs.costs).toEqual({tickets:{},meals:{},accommodation:"",other:""});
    expect(applied.inputs.settings).toEqual(inputs.settings);expect(applied.inputs.candidates).toEqual(inputs.candidates);expect(applied.notices.join()).toContain("全部用户费用估算已清空");
  });
  it.each(["home","required","interests"])("clears incompatible optional pool for %s, retaining only related costs/settings",kind=>{
    const {inputs}=restoreSavedTrip(savedFixture(),"one");inputs.costs.tickets[optionals[0].id]="22";inputs.costs.meals["2026-10-10"]="0";
    inputs.candidates.excludedIds=new Set([optionals[0].id]);
    const changedHome={...home,id:"new-home"}, nextRequired=[required,optionals[1]];
    const request={...inputs.request,...(kind==="home"?{accommodation_place:changedHome,accommodation_location:changedHome.name}:kind==="required"?{must_visit_places:nextRequired,must_visit:nextRequired.map(p=>p.name)}:{interests:["美食"]})};
    const next=applyEditedRequirements(inputs,request).inputs;
    expect(eligibleOptional(next.request,next.candidates)).toEqual([]);expect(next.candidates.excludedIds.size).toBe(0);
    expect(next.costs.tickets).toEqual({[required.id]:"0"});expect(next.costs.other).toBe("bad");expect(next.costs.meals).toEqual(inputs.costs.meals);
    expect(next.costs.accommodation).toBe(kind==="home"?"":"500");expect(next.settings.stays[required.id]).toEqual(inputs.settings.stays[required.id]);
    if(kind==="required")expect(next.settings.stays[optionals[1].id]).toEqual({value:"60",source:"default"});
  });
  it("budget/window edits keep raw money and lunch without clipping; incompatible lunch prevents generation",()=>{
    const {inputs}=restoreSavedTrip(savedFixture(),"one");
    const next=applyEditedRequirements(inputs,{...inputs.request,budget:9000,daily_end_time:"11:00"}).inputs;
    expect(next.costs).toEqual(inputs.costs);expect(next.settings.lunch).toEqual(inputs.settings.lunch);
    expect(validateScheduleInput(next.request,next.settings,eligibleOptional(next.request,next.candidates),false)).toContain("午餐");
    const rows=restoredEstimateRows(next.request,[required,...optionals.slice(0,1)],next.costs);
    expect(rows.find(r=>r.placeId===required.id)?.money).toEqual({status:"known",cents:0});
    expect(rows.find(r=>r.placeId===optionals[0].id)?.money.status).toBe("unknown");expect(rows.at(-1)?.money.status).toBe("invalid");
    expect(()=>applyEditedRequirements(inputs,{...inputs.request,travelers:NaN})).toThrow();
  });
});

import { describe, expect, it } from "vitest";
import { compareScheduleCosts, emptyCostInputs, formatCents } from "./schedule-costs";
import { adjustmentResponse } from "./schedule-adjustment.test-fixtures";
import { optionals, required, scheduleRequest, tripRequest } from "./weather-schedule-check.test-fixtures";
import { isScheduleResponse } from "@/types/schedule";

function fixtures(mode: "walking" | "transit" = "walking") {
  const original = adjustmentResponse({ ...scheduleRequest(), transport_mode: mode });
  const proposed = adjustmentResponse({ ...scheduleRequest(), transport_mode: mode, optional_places: optionals.slice(1), duration_settings: [required, ...optionals.slice(1)].map(p => ({ place_id:p.id, minutes:60, source:"default" })) });
  const inputs = { ...emptyCostInputs(), tickets:{[required.id]:"0",[optionals[0].id]:"20",[optionals[1].id]:"10"}, meals:{"2026-10-10":"0"}, accommodation:"0", other:"0" };
  return { original, proposed, inputs, compare: () => compareScheduleCosts(original, proposed, tripRequest(), inputs) };
}
describe("current shared estimates, independently scoped to each actual timeline", () => {
  it.each([["10",-1000],["30.01",1001],["20",0]])("new ticket %s gives signed new-minus-original %s cents, including a valid partial", (value,delta) => {
    const f=fixtures(); f.inputs.tickets[optionals[1].id]=String(value);
    expect(f.proposed.status).toBe("partial"); expect(f.compare().deltaCents).toBe(delta); expect(f.compare().issues).toEqual([]);
  });
  it.each(["one","both","identical"])("%s-side unknowns never cancel",kind=>{
    const f=fixtures();f.inputs.tickets[optionals[1].id]="";
    if(kind==="both")f.inputs.tickets[optionals[0].id]="";
    if(kind==="identical"){f.inputs.tickets[optionals[1].id]="10";f.inputs.other="";}
    expect(f.compare().deltaCents).toBeNull();expect(f.compare().issues.join()).toContain("未知");
  });
  it.each(["-1","1.001","bad","90071992547409.92"])("invalid current %s removes an earlier valid difference", value=>{
    const f=fixtures();expect(f.compare().deltaCents).toBe(-1000);f.inputs.tickets[optionals[1].id]=value;
    const result=f.compare();expect(result.deltaCents).toBeNull();expect(result.issues.join()).toContain("无效金额");
    expect(result.before.status==="ready"&&result.before.subtotalCents).toBe(2000);
  });
  it("signed safe-cent endpoints stay exact; overflow in either side blocks difference",()=>{
    const f=fixtures();f.inputs.tickets[optionals[0].id]="90071992547409.91";f.inputs.tickets[optionals[1].id]="0";
    expect(f.compare().deltaCents).toBe(-Number.MAX_SAFE_INTEGER);expect(formatCents(f.compare().deltaCents!)).toBe("−¥90,071,992,547,409.91");
    f.inputs.tickets[optionals[0].id]="0";f.inputs.tickets[optionals[1].id]="90071992547409.91";expect(f.compare().deltaCents).toBe(Number.MAX_SAFE_INTEGER);
    f.inputs.other="0.01";expect(f.compare().deltaCents).toBeNull();expect(f.compare().issues.join()).toContain("无效金额");
  });
  it("same name/different IDs do not share estimates; removed cost only belongs to original",()=>{
    const f=fixtures();f.proposed.request.optional_places![0].name=optionals[0].name;
    f.inputs.tickets[optionals[1].id]="";const value=f.compare();
    expect(value.before.status==="ready"&&value.before.userCents).toBe(2000);
    expect(value.after.status==="ready"&&value.after.userCents).toBe(0);expect(value.deltaCents).toBeNull();
  });
  it("shared group totals change both sides without multiplying people; frozen responses remain untouched",()=>{
    const f=fixtures(), snapshot=structuredClone([f.original,f.proposed]);f.inputs.accommodation="500";f.inputs.meals["2026-10-10"]="100";
    const value=f.compare();expect(value.before.status==="ready"&&value.before.subtotalCents).toBe(62000);expect(value.after.status==="ready"&&value.after.subtotalCents).toBe(61000);expect(value.deltaCents).toBe(-1000);expect([f.original,f.proposed]).toEqual(snapshot);
  });
  it("different transit fare batches count actual schemes for all people; unknown fares do not offset",()=>{
    const f=fixtures("transit");f.original.edges.forEach(e=>e.transit_route!.fare_cny=2);f.proposed.edges.forEach(e=>e.transit_route!.fare_cny=3);
    const result=f.compare();expect(result.before.status==="ready"&&result.before.transportCents).toBe(1200);expect(result.after.status==="ready"&&result.after.transportCents).toBe(1800);expect(result.deltaCents).toBe(-400);
    f.original.edges[0].transit_route!.fare_cny=null;f.proposed.edges[0].transit_route!.fare_cny=null;expect(f.compare().deltaCents).toBeNull();
  });
  it("required-incomplete, no-visit and invalid drafts have no full comparison",()=>{
    const f=fixtures();const incomplete=adjustmentResponse({...scheduleRequest(),optional_places:[],duration_settings:scheduleRequest().duration_settings.slice(0,1)});
    incomplete.request.must_visit_places.push(optionals[0]);incomplete.request.duration_settings.push({place_id:optionals[0].id,minutes:60,source:"default"});incomplete.unscheduled=[{place_id:optionals[0].id,reason:"time_window",message:"受控未安排"}];incomplete.status="partial";
    expect(isScheduleResponse(incomplete,incomplete.request)).toBe(true);
    for(const proposed of [incomplete,adjustmentResponse(scheduleRequest(),"empty"),{...f.proposed,edges:[]}])expect(compareScheduleCosts(f.original,proposed,tripRequest(),f.inputs).deltaCents).toBeNull();
  });
});

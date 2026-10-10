import { describe, expect, it } from "vitest";
import { calculateScheduleCosts, emptyCostInputs, formatCents, multiplyCents, numberToCents, parseCostInput, sumCents } from "./schedule-costs";
import { adjustmentResponse } from "./schedule-adjustment.test-fixtures";
import { home, optionals, required, scheduleRequest, tripRequest } from "./weather-schedule-check.test-fixtures";
import { isScheduleResponse, type ScheduleResponse } from "@/types/schedule";

const known = (cents: number) => ({ status: "known", cents });
export function costFixture(mode: "walking" | "transit" = "walking", fare: number | null = 2.5) {
  const response = adjustmentResponse({ ...scheduleRequest(), transport_mode: mode });
  response.edges.forEach((edge) => { if (edge.transit_route) edge.transit_route.fare_cny = fare; });
  return response;
}
function calculate(response = costFixture(), input = emptyCostInputs(), request = tripRequest()) {
  expect(isScheduleResponse(response, response.request)).toBe(true);
  const result = calculateScheduleCosts(response, request, input);
  if (result.status !== "ready") throw new Error(result.message);
  return result;
}
describe("integer-cent money policy", () => {
  it.each([['', 'unknown'], ['  ', 'unknown'], ['0', 'known'], ['0.00', 'known']])("distinguishes blank from explicit %s", (value, status) => expect(parseCostInput(value).status).toBe(status));
  it.each([['12.34',1234],['0.01',1],['001.20',120],[' 2.5 ',250],['90071992547409.91',Number.MAX_SAFE_INTEGER]])("parses exactly %s", (value, cents) => expect(parseCostInput(String(value))).toEqual(known(Number(cents))));
  it.each(['-1','-0','1.001','NaN','Infinity','1e2','.5','1.','1,000','abc','90071992547409.92','9'.repeat(129)])("rejects %s, not zero/old value", value => expect(parseCostInput(value).status).toBe("invalid"));
  it.each([[0,0],[-0,0],[0.29,29],[2.5,250],[123.45,12345],[1e6,100000000],[90071992547409.9,9007199254740990]])("canonical numeric %s converts without float multiplication", (value,cents) => expect(numberToCents(value)).toEqual(known(cents)));
  it.each([NaN,Infinity,-1,1.001,1.005,0.1+0.2,1e-7,1e20,90071992547409.92])("unsafe/sub-cent number %s is explicit error, no rounding", value => expect(numberToCents(value).status).toBe("invalid"));
  it("checks multiplication, accumulation and exact safe-limit formatting", () => {
    expect(multiplyCents(250,3)).toEqual(known(750)); expect(multiplyCents(0,8)).toEqual(known(0));
    for (const [c,n] of [[Number.MAX_SAFE_INTEGER,2],[1,Number.MAX_SAFE_INTEGER+1],[1,0],[1,1.5],[-1,1]]) expect(multiplyCents(c,n).status).toBe("invalid");
    expect(sumCents([Number.MAX_SAFE_INTEGER-1,1])).toEqual(known(Number.MAX_SAFE_INTEGER));
    expect(sumCents([Number.MAX_SAFE_INTEGER,1]).status).toBe("invalid");
    expect(sumCents([0.5]).status).toBe("invalid");
    expect(formatCents(Number.MAX_SAFE_INTEGER)).toBe("¥90,071,992,547,409.91");
    expect(formatCents(-1)).toBe("−¥0.01"); expect(formatCents(0)).toBe("¥0.00");
  });
});
describe("official timeline cost scope", () => {
  it("walking is rule-zero transport only; all empty estimates remain missing", () => {
    const value = calculate(); expect(value.transportCents).toBe(0); expect(value.subtotalCents).toBe(0);
    expect(value.transport).toHaveLength(3); expect(value.missing).toHaveLength(5);
    expect(value.user.map(x=>x.money.status)).toEqual(Array(5).fill("unknown"));
    expect(value.differenceCents).toBe(300000);
  });
  it("charges each transit scheme once per traveler, not its access/ride/transfer legs", () => {
    const response=costFixture("transit"); const route=response.edges[0].transit_route!;
    route.walking_distance_meters=100; route.legs.unshift({ mode:"walking",duration_seconds:10,distance_meters:100,instruction:null,line_name:null,departure_stop:null,arrival_stop:null,geometry:[],geometry_complete:false });
    route.legs.push({ ...route.legs[1], line_name:"另一条受控换乘线" });
    const value=calculate(response); expect(value.transportCents).toBe(1500);
    expect(value.transport.map(r=>r.money)).toEqual([known(500),known(500),known(500)]);
    expect(value.transport[0]).toMatchObject({ date:'2026-10-10',from:home.name,to:required.name,source:'amap',edgeId:'edge-0' });
  });
  it("unknown fare stays missing; explicit zero is known; invalid precision invalidates totals", () => {
    const response=costFixture("transit"); response.edges[0].transit_route!.fare_cny=null; response.edges[1].transit_route!.fare_cny=0;
    const value=calculate(response); expect(value.transportCents).toBe(500); expect(value.missing).toHaveLength(6);
    expect(value.transport[0].fare.status).toBe("unknown"); expect(value.transport[1].money).toEqual(known(0));
    response.edges[2].transit_route!.fare_cny=1.005;
    const invalid=calculate(response); expect(invalid.subtotalCents).toBeNull(); expect(invalid.differenceCents).toBeNull(); expect(invalid.errors.join()).toContain("精确到分");
  });
  it("repeated references are separate occurrences, including outward and return; never unique-used-edge sum", () => {
    const request={ ...scheduleRequest(),optional_places:[],duration_settings:scheduleRequest().duration_settings.slice(0,1),transport_mode:'transit' as const };
    const response=adjustmentResponse(request); response.edges[0].transit_route!.fare_cny=2;response.edges[1].transit_route!.fare_cny=3;
    const [out,visit,back]=response.days[0].items;
    response.days[0].items=[structuredClone(out),structuredClone(back),out,visit,back];
    let minutes=540;const clock=(n:number)=>`${String(Math.floor(n/60)).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`;
    response.days[0].items.forEach(i=>{i.start_time=clock(minutes);minutes+=i.duration_minutes;i.end_time=clock(minutes);});response.days[0].return_time=clock(minutes);
    const value=calculate(response);expect(value.transport.map(r=>r.edgeId)).toEqual(['edge-0','edge-1','edge-0','edge-1']);expect(value.transportCents).toBe(2000);
  });
  it("unused trial edges and unscheduled optional tickets never contribute or become missing costs", () => {
    const request={ ...scheduleRequest(),transport_mode:'transit' as const,optional_places:optionals.slice(0,2),duration_settings:[required,...optionals.slice(0,2)].map(p=>({place_id:p.id,minutes:60,source:'default' as const})) };
    const response=adjustmentResponse(request);response.edges.forEach(e=>{e.transit_route!.fare_cny=2;});
    const extra=structuredClone(response.edges[1]);extra.id='unused';extra.used=false;extra.destination={place_id:optionals[1].id,longitude:optionals[1].longitude,latitude:optionals[1].latitude};extra.transit_route!.fare_cny=999;
    response.edges.push(extra); const input=emptyCostInputs();input.tickets[optionals[1].id]='999999';
    const value=calculate(response,input); expect(value.transportCents).toBe(1200);expect(value.user).toHaveLength(5);expect(value.missing).toHaveLength(5);expect(value.userCents).toBe(0);
  });
  it("same-location transit counts zero without inventing fare or line", () => {
    const response=costFixture('transit');const old=required.id;
    response.request.must_visit_places=[home];response.request.duration_settings[0].place_id=home.id;
    response.edges[0]={...response.edges[0],destination:response.edges[0].origin,status:'same_place',source:'same_place',duration_seconds:0,duration_minutes:0,transit_route:null,selection_rule:null};
    response.edges[1].origin=response.edges[0].origin;
    let time=540;const clock=(n:number)=>`${String(Math.floor(n/60)).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`;
    response.days[0].items.forEach((i,index)=>{if(i.place_id===old)i.place_id=home.id;if(i.from_place_id===old)i.from_place_id=home.id;if(i.to_place_id===old)i.to_place_id=home.id;if(index===0)i.duration_minutes=0;i.start_time=clock(time);time+=i.duration_minutes;i.end_time=clock(time);});response.days[0].return_time=clock(time);
    const value=calculate(response);expect(value.transport[0].money).toEqual(known(0));expect(value.transport[0].samePlace).toBe(true);expect(value.user[0].money.status).toBe('unknown');
  });
  it("ID-based tickets, whole-trip totals and every travel day meals (including empty days), not Mock days/lunch count", () => {
    const response=costFixture();response.request.start_date='2026-12-31';response.request.end_date='2027-01-02';response.days[0].date='2026-12-31';response.optional_results[0].scheduled_date='2026-12-31';response.optional_results[0].attempts[0].date='2026-12-31';response.days.push({date:'2027-01-01',items:[],return_time:null},{date:'2027-01-02',items:[],return_time:null});
    const inputs=emptyCostInputs();inputs.tickets[required.id]='10';inputs.tickets[optionals[0].id]='20';inputs.meals={'2026-12-31':'30','2027-01-01':'40','2027-01-02':'50'};inputs.accommodation='100';inputs.other='0';
    const value=calculate(response,inputs,{...tripRequest(),start_date:'2026-12-31',end_date:'2027-01-02',travelers:9});
    expect(value.userCents).toBe(25000);expect(value.missing).toEqual([]);expect(value.user.filter(r=>r.category==='meal')).toHaveLength(3);
    expect(value.user.slice(0,2).map(r=>[r.placeId,r.money])).toEqual([[required.id,known(1000)],[optionals[0].id,known(2000)]]);
  });
  it("all explicit zeros are estimates, not unknown or verified-free; known partial can already exceed budget", () => {
    const inputs=emptyCostInputs();inputs.tickets[required.id]='3001';let value=calculate(costFixture(),inputs);expect(value.subtotalCents).toBe(300100);expect(value.differenceCents).toBe(-100);expect(value.missing).toHaveLength(4);
    inputs.tickets={ [required.id]:'0',[optionals[0].id]:'0' };inputs.meals={'2026-10-10':'0'};inputs.accommodation='0';inputs.other='0';value=calculate(costFixture(),inputs);expect(value.missing).toEqual([]);expect(value.subtotalCents).toBe(0);
  });
  it("bad current user value replaces old value; per-field, person multiplication and aggregate overflow all suppress conclusion", () => {
    const inputs=emptyCostInputs();inputs.other='100';expect(calculate(costFixture(),inputs).subtotalCents).toBe(10000);
    inputs.other='oops';expect(calculate(costFixture(),inputs).subtotalCents).toBeNull();
    inputs.other='90071992547409.91';inputs.accommodation='0.01';expect(calculate(costFixture(),inputs).subtotalCents).toBeNull();
    inputs.accommodation='';expect(calculate(costFixture('transit'),inputs).subtotalCents).toBeNull();
    expect(calculate(costFixture('transit',90071992547409.9)).subtotalCents).toBeNull();
    expect(calculate(costFixture(),emptyCostInputs(),{...tripRequest(),budget:1.001}).differenceCents).toBeNull();
    expect(calculate(costFixture(),emptyCostInputs(),{...tripRequest(),travelers:1.5}).subtotalCents).toBeNull();
  });
  it("partial required scope is flagged, and null/invalid/no-visit drafts never expose a zero conclusion", () => {
    const r={...scheduleRequest(),optional_places:[],duration_settings:scheduleRequest().duration_settings.slice(0,1)};
    const response=adjustmentResponse(r);response.request.must_visit_places.push(optionals[0]);response.request.duration_settings.push({place_id:optionals[0].id,minutes:60,source:'default'});response.unscheduled=[{place_id:optionals[0].id,reason:'time_window',message:'未安排'}];response.status='partial';
    expect(calculate(response).unarrangedRequired).toBe(1);
    for(const draft of [null,{} as ScheduleResponse,adjustmentResponse(scheduleRequest(),'empty')]) expect(calculateScheduleCosts(draft,tripRequest(),emptyCostInputs()).status).toBe('unavailable');
  });
});

import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { canonical, createSnapshot, isSavedDetail, isSavedSnapshot, listTrips, readTrip, saveTrip, type SavedDetail } from "./saved-trips";
import { calculateScheduleCosts, emptyCostInputs } from "./schedule-costs";
import { schedule, tripRequest, required, optionals } from "./weather-schedule-check.test-fixtures";

export function archiveFixture(): SavedDetail {
  const s=schedule();
  return {id:"a".repeat(32),name:"受控存档",saved_at:"2026-10-12T00:00:00Z",schema_version:"saved-trip-v1",start_date:s.request.start_date,end_date:s.request.end_date,transport_mode:"walking",status:s.status,snapshot:createSnapshot(tripRequest(),s,emptyCostInputs())};
}
afterEach(()=>{vi.unstubAllGlobals();vi.useRealTimers();});
describe("versioned archive projection and disk-backed cross-language contract",()=>{
  it("accepts persisted Python walking/transit/partial/empty-day outputs, keeping cost semantics",()=>{
    const backend=resolve(process.cwd(),"../backend");
    const data=JSON.parse(execFileSync(`${backend}/.venv/bin/python`,["-B","tests/export_saved_contract.py"],{cwd:backend,env:{...process.env,PYTHONPATH:backend,PYTHONDONTWRITEBYTECODE:"1"},encoding:"utf8"}));
    expect(data.length).toBe(12);
    for(const value of data){expect(isSavedDetail(value)).toBe(true);expect(value.snapshot.costs.other).toBe("invalid");expect(value.snapshot.costs.accommodation).toBe("0");}
  });
  it("projects only official visited IDs and all dates; blank/zero/invalid stay distinct",()=>{
    const s=schedule(), costs=emptyCostInputs(); costs.tickets[required.id]="0";costs.tickets[optionals[0].id]="bad";costs.tickets[optionals[3].id]="999";costs.meals[s.request.start_date]="";costs.meals["2030-01-01"]="4";
    const result=createSnapshot(tripRequest(),s,costs);
    expect(Object.keys(result)).toEqual(["schema_version","requirements","schedule","costs"]);
    expect(result.costs.tickets).toEqual({[required.id]:"0",[optionals[0].id]:"bad"});expect(Object.keys(result.costs.meals)).toEqual([s.request.start_date]);
    const before=calculateScheduleCosts(s,tripRequest(),costs),after=calculateScheduleCosts(result.schedule,result.requirements,result.costs);
    expect(after).toEqual(before);costs.tickets[required.id]="20";expect(result.costs.tickets[required.id]).toBe("0");
  });
  it.each(["version","extra","requirement","previewCost","nonfinite","size","invalidReference"])("rejects %s",kind=>{
    const d=archiveFixture();
    if(kind==="version")Object.assign(d.snapshot,{schema_version:"future"});
    if(kind==="extra")Object.assign(d.snapshot,{weather:{}});
    if(kind==="requirement")d.snapshot.requirements.budget=NaN;
    if(kind==="previewCost")d.snapshot.costs.tickets.foreign="1";
    if(kind==="nonfinite")d.snapshot.schedule.edges[0].duration_seconds=Infinity;
    if(kind==="size")d.snapshot.costs.other="x".repeat(257);
    if(kind==="invalidReference")d.snapshot.schedule.days[0].items[0].edge_id="foreign";
    expect(isSavedSnapshot(d.snapshot)).toBe(false);
  });
  it("POST/GET guards metadata, exact echoes, safe errors; no automatic retries",async()=>{
    const detail=archiveFixture(), fetcher=vi.fn().mockImplementation(async()=>new Response(JSON.stringify(detail)));vi.stubGlobal("fetch",fetcher);
    const operation={operation_id:"b".repeat(32),name:detail.name,snapshot:detail.snapshot};
    expect(await saveTrip(operation,new AbortController().signal)).toEqual(detail);
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual(operation);
    expect(await readTrip(detail.id,new AbortController().signal)).toEqual(detail);
    fetcher.mockResolvedValueOnce(new Response(JSON.stringify({...detail,id:"c".repeat(32)})));
    await expect(readTrip(detail.id,new AbortController().signal)).rejects.toThrow("校验");
    fetcher.mockResolvedValueOnce(new Response(JSON.stringify({detail:{code:"storage_unavailable",message:"secret SQL"}}),{status:503}));
    await expect(saveTrip(operation,new AbortController().signal)).rejects.toThrow("无法读写");expect(fetcher).toHaveBeenCalledTimes(4);
    expect(canonical({...detail.snapshot})).toBe(canonical(detail.snapshot));
  });
  it("bounded pagination and 15s ambiguous write timeout",async()=>{
    const detail=archiveFixture();const {snapshot:_,...summary}=detail;void _;
    vi.stubGlobal("fetch",vi.fn().mockResolvedValue(new Response(JSON.stringify({items:[summary],limit:10,offset:0,has_more:false}))));
    expect((await listTrips(0,new AbortController().signal)).items).toHaveLength(1);
    vi.useFakeTimers();const fetcher=vi.fn((_u,i)=>new Promise((_yes,no)=>i.signal.addEventListener("abort",()=>no(new DOMException("cancel","AbortError")))));vi.stubGlobal("fetch",fetcher);
    const pending=saveTrip({operation_id:"b".repeat(32),name:detail.name,snapshot:detail.snapshot},new AbortController().signal);
    const check=expect(pending).rejects.toThrow("可能已保存");await vi.advanceTimersByTimeAsync(15000);await check;expect(fetcher).toHaveBeenCalledTimes(1);
  });
});

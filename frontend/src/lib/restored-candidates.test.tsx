import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useSchedulePreview } from "./use-schedule-preview";
import { useCandidatePool } from "./use-candidate-pool";
import { useWeatherForecast } from "./use-weather-forecast";
import { useWeatherCheckRevision, useWeatherScheduleCheck } from "./use-weather-schedule-check";
import { useScheduleAdjustment } from "./use-schedule-adjustment";
import { restoreSavedTrip } from "./restore-trip";
import { savedFixture } from "./restore-trip.test-fixtures";
import { candidateResponse, forecast, now, optionals, required, deferred } from "./weather-schedule-check.test-fixtures";
import { adjustmentResponse } from "./schedule-adjustment.test-fixtures";
import type { ScheduleResponse } from "@/types/schedule";
import { isCandidateResponse } from "@/types/candidates";
const api=vi.hoisted(()=>({schedule:vi.fn(),candidates:vi.fn(),weather:vi.fn()}));
vi.mock("./schedule-api",async original=>({...await original<typeof import("./schedule-api")>(),querySchedulePreview:api.schedule}));
vi.mock("./candidates-api",async original=>({...await original<typeof import("./candidates-api")>(),queryCandidates:api.candidates}));
vi.mock("./weather-api",async original=>({...await original<typeof import("./weather-api")>(),queryWeatherForecast:api.weather}));
function useHarness(){
  const record=savedFixture();record.snapshot.requirements.interests=["摄影","美食"];
  const {inputs}=restoreSavedTrip(record,"copy"),revision=useWeatherCheckRevision();
  const draft=useSchedulePreview(inputs.request,revision.invalidate,inputs);
  const pool=useCandidatePool(inputs.request,draft.updateCandidates,inputs.candidates);
  const weather=useWeatherForecast({start_date:inputs.request.start_date,end_date:inputs.request.end_date},revision.invalidate);
  const check=useWeatherScheduleCheck({schedule:draft,candidates:pool,weather},revision);
  const adjustment=useScheduleAdjustment({schedule:draft,candidates:pool,weather,check},revision);
  return {draft,pool,weather,check,adjustment};
}
beforeEach(()=>{vi.useFakeTimers();vi.setSystemTime(now);vi.resetAllMocks();api.schedule.mockImplementation(async r=>adjustmentResponse(r));api.weather.mockResolvedValue(forecast());});
afterEach(()=>vi.useRealTimers());
describe("explicit historical provenance through existing shared workflows",()=>{
  it("seeds only inputs; local exclude/restore and prepared ABA transactions never search",()=>{
    const {result}=renderHook(useHarness);expect(result.current.draft.state.response).toBeNull();expect(result.current.pool.state.response).toBeNull();
    expect(result.current.pool.state.history?.places).toEqual([optionals[0]]);expect(result.current.draft.optionalPlaces).toEqual([optionals[0]]);
    const prepared=result.current.pool.prepareExclusion(optionals[0].id)!;act(()=>{result.current.pool.exclude(optionals[0].id);result.current.pool.restore(optionals[0].id);});
    expect(prepared.isCurrent()).toBe(false);expect(prepared.commit()).toBe(false);act(()=>result.current.pool.exclude(required.id));
    expect(result.current.pool.excludedIds.size).toBe(0);expect(api.schedule).not.toHaveBeenCalled();expect(api.candidates).not.toHaveBeenCalled();expect(api.weather).not.toHaveBeenCalled();
  });
  it.each(["network","failed"])("%s refresh keeps history and exclusions, valid partial replaces; IDs remember exclusions",async outcome=>{
    const {result}=renderHook(useHarness);act(()=>result.current.pool.exclude(optionals[0].id));
    if(outcome==="network")api.candidates.mockRejectedValueOnce(new Error("network"));
    else api.candidates.mockImplementationOnce(async request=>{
      const failed=candidateResponse();failed.status="failed";failed.keywords=["公园","餐厅"];
      failed.queries=[{interest:"摄影",keyword:"公园",status:"failed",result_count:0,message:"受控失败"},{interest:"美食",keyword:"餐厅",status:"timeout",result_count:0,message:"受控超时"}];
      failed.candidates=failed.candidates.filter(c=>c.role==="must_visit");expect(isCandidateResponse(failed,request)).toBe(true);return failed;
    });
    await act(async()=>{await result.current.pool.query();});expect(result.current.pool.state.history).toBeTruthy();expect(result.current.pool.state.message).toContain("历史存档");
    expect(result.current.pool.excludedIds.has(optionals[0].id)).toBe(true);
    const next=candidateResponse();next.status="partial";next.keywords.push("餐厅");next.queries.push({interest:"美食",keyword:"餐厅",status:"failed",result_count:0,message:"受控失败"});
    expect(isCandidateResponse(next,api.candidates.mock.calls[0][0])).toBe(true);api.candidates.mockResolvedValue(next);
    await act(async()=>{await result.current.pool.query();});expect(result.current.pool.state.history).toBeUndefined();expect(result.current.pool.state.response).toBe(next);
    expect(result.current.draft.optionalPlaces).toEqual(optionals.slice(1));expect(result.current.pool.excludedIds.has(optionals[0].id)).toBe(true);
  });
  it("historical candidates can drive fresh draft, weather preview, cancel and atomic adopt, without POI or model calls",async()=>{
    const {result}=renderHook(useHarness);await act(async()=>{await result.current.draft.query();await result.current.weather.query();});act(()=>result.current.check.check());
    const original=result.current.draft.state.response;await act(async()=>{expect(await result.current.adjustment.start(optionals[0].id)).toBe(true);});
    expect(result.current.adjustment.canAdopt).toBe(true);expect(result.current.draft.state.response).toBe(original);expect(result.current.pool.excludedIds.size).toBe(0);
    act(()=>result.current.adjustment.cancel());expect(result.current.draft.state.response).toBe(original);
    await act(async()=>{await result.current.adjustment.start(optionals[0].id);});const shown=result.current.adjustment.state.response;
    act(()=>expect(result.current.adjustment.adopt()).toBe(true));expect(result.current.draft.state.response).toEqual(shown);expect(result.current.pool.excludedIds.has(optionals[0].id)).toBe(true);
    await act(async()=>{await result.current.draft.query();});expect(api.schedule.mock.calls.at(-1)![0].optional_places).toEqual([]);expect(api.candidates).not.toHaveBeenCalled();
  });
  it.each(["success","error"])("unmounted restored hooks reject old %s, query callbacks and prepared commits",async outcome=>{
    const pending=deferred<ScheduleResponse>();api.schedule.mockReturnValueOnce(pending.promise);const view=renderHook(useHarness);
    const old=view.result.current,prepared=old.pool.prepareExclusion(optionals[0].id)!;let work!:Promise<boolean>;
    act(()=>{work=old.draft.query();});const request=api.schedule.mock.calls[0][0];view.unmount();
    await act(async()=>{if(outcome==="success")pending.resolve(adjustmentResponse(request));else pending.reject(new Error("old"));expect(await work).toBe(false);expect(await old.draft.query()).toBe(false);expect(await old.pool.query()).toBe(false);});
    expect(prepared.commit()).toBe(false);expect(api.schedule).toHaveBeenCalledTimes(1);expect(api.candidates).not.toHaveBeenCalled();
  });
});

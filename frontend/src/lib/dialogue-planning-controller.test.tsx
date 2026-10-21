import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useDialoguePlanning, resolveDialogueSelector, type PlanningReply } from "./dialogue-planning-controller";
import { newWorkspaceInputs } from "./restore-trip";
import { cityRequest, cityCandidates, cityWeather } from "./destination.test-fixtures";
import { candidateResponse, deferred, forecast, optionals, required, tripRequest } from "./weather-schedule-check.test-fixtures";
import { adjustmentResponse } from "./schedule-adjustment.test-fixtures";
import { savedCityFixture } from "./restore-trip.test-fixtures";
import { calculateScheduleCosts } from "./schedule-costs";
import type { ScheduleRequest, ScheduleResponse } from "@/types/schedule";
import type { SavedDetail } from "./saved-trips";

const api=vi.hoisted(()=>({schedule:vi.fn(),candidates:vi.fn(),weather:vi.fn(),save:vi.fn(),list:vi.fn(),read:vi.fn(),intent:vi.fn(),explain:vi.fn()}));
vi.mock("./schedule-api",async original=>({...await original<typeof import("./schedule-api")>(),querySchedulePreview:api.schedule}));
vi.mock("./candidates-api",async original=>({...await original<typeof import("./candidates-api")>(),queryCandidates:api.candidates}));
vi.mock("./weather-api",async original=>({...await original<typeof import("./weather-api")>(),queryWeatherForecast:api.weather}));
vi.mock("./saved-trips",async original=>({...await original<typeof import("./saved-trips")>(),saveTrip:api.save,listTrips:api.list,readTrip:api.read}));
vi.mock("./adjustment-intent-api",async original=>({...await original<typeof import("./adjustment-intent-api")>(),queryAdjustmentIntent:api.intent}));
vi.mock("./explanation-api",async original=>({...await original<typeof import("./explanation-api")>(),queryExplanation:api.explain}));
beforeEach(()=>{
  vi.resetAllMocks();
  api.schedule.mockImplementation(async(request:ScheduleRequest)=>adjustmentResponse(request));
  api.candidates.mockResolvedValue(cityCandidates(candidateResponse()));
  api.weather.mockResolvedValue(cityWeather(forecast()));
});
function setup(){
  const initial=newWorkspaceInputs(cityRequest(tripRequest()));let allowed=true;
  const canOperate=()=>allowed;
  const view=renderHook(()=>useDialoguePlanning(initial,canOperate));
  const execute=async(name:string,args:Record<string,unknown>={},signal?:AbortSignal)=>{
    let answer!:PlanningReply;await act(async()=>{answer=await view.result.current.execute({name,arguments:args},signal);});return answer;
  };
  return {...view,initial,execute,block:()=>{allowed=false;}};
}
describe("headless dialogue planning reuses formal owners",()=>{
  it("mounting, reading an empty plan and unknown costs do not request business APIs",async()=>{
    const v=setup();
    expect((await v.execute("read_schedule")).text).toContain("没有有效正式草案");
    expect((await v.execute("read_budget")).text).toContain("请先生成");
    expect((await v.execute("read_weather")).text).toContain("尚未查询");
    Object.values(api).forEach(mock=>expect(mock).not.toHaveBeenCalled());
  });
  it("generation reports the newly captured formal response without awaiting a React rerender",async()=>{
    const v=setup();const answer=await v.execute("generate_schedule");
    expect(answer.text).toContain("09:01—10:01");expect(answer.text).toContain("1/1");
    expect(api.schedule).toHaveBeenCalledTimes(1);expect(api.candidates).not.toHaveBeenCalled();
    expect((await v.execute("read_schedule",{day:2})).text).toContain("不在当前旅行范围");
  });
  it("exact same-name selectors require clarification and never silently choose first",()=>{
    expect(resolveDialogueSelector([required,optionals[0]],required.name)).toBeNull();
    expect(resolveDialogueSelector([required,optionals[0]],required.id)).toBe(required);
    expect(resolveDialogueSelector([required,optionals[0]],"2")).toBe(optionals[0]);
    expect(resolveDialogueSelector([required],"同名")).toBeNull();
  });
  it("stay/lunch/mode updates keep existing validated rules and require fresh generation",async()=>{
    const v=setup();await v.execute("generate_schedule");
    await v.execute("set_stay",{selector:required.id,minutes:90});
    await v.execute("set_lunch",{enabled:false,start:"12:00",end:"13:00"});
    await v.execute("set_transport",{mode:"transit"});
    expect(v.result.current.schedule.capture()).toBeNull();expect(api.schedule).toHaveBeenCalledTimes(1);
    await v.execute("generate_schedule");const request=api.schedule.mock.calls[1][0];
    expect(request.transport_mode).toBe("transit");expect(request.duration_settings[0]).toMatchObject({minutes:90,source:"user"});expect(request.lunch.enabled).toBe(false);
  });
  it("costs preserve unknown, zero and invalid text without route/model requests or invalidating draft",async()=>{
    const v=setup();await v.execute("generate_schedule");const before=v.result.current.schedule.capture()!.original;
    expect((await v.execute("set_cost",{kind:"ticket",selector:required.id,value:"0"})).text).toContain("未知");
    expect((await v.execute("set_cost",{kind:"other",selector:null,value:"bad"})).text).toContain("非法金额");
    expect(v.result.current.getCosts().other).toBe("bad");expect(v.result.current.schedule.capture()!.original).toEqual(before);
    expect(api.schedule).toHaveBeenCalledTimes(1);expect(api.intent).not.toHaveBeenCalled();expect(api.explain).not.toHaveBeenCalled();
  });
  it("candidate preparation, exclusions and restoration use the shared pool",async()=>{
    const v=setup();await v.execute("prepare_candidates");
    expect(api.candidates).toHaveBeenCalledTimes(1);
    await v.execute("generate_schedule");
    await v.execute("exclude_candidate",{selector:optionals[0].id});expect(v.result.current.schedule.capture()).toBeNull();
    expect(v.result.current.candidates.excludedIds.has(optionals[0].id)).toBe(true);
    await v.execute("restore_candidate",{selector:optionals[0].id});expect(v.result.current.candidates.excludedIds.size).toBe(0);
    expect(api.schedule).toHaveBeenCalledTimes(1);
  });
  it("explicit preview skips the intent model, preserves formal plan and cancels without networking",async()=>{
    const v=setup();await v.execute("prepare_candidates");await v.execute("generate_schedule");const original=v.result.current.schedule.capture()!.original;
    const answer=await v.execute("preview_exclusion",{selector:optionals[0].id});
    expect(answer.text).toContain("调整预览，尚未采用");expect(v.result.current.schedule.capture()!.original).toEqual(original);
    expect(v.result.current.candidates.excludedIds.size).toBe(0);expect(api.weather).not.toHaveBeenCalled();expect(api.intent).not.toHaveBeenCalled();
    await v.execute("cancel_preview");expect(v.result.current.schedule.capture()!.original).toEqual(original);expect(api.schedule).toHaveBeenCalledTimes(2);
  });
  it("adoption commits seen preview/candidates/settings atomically, costs agree, double adoption rejected",async()=>{
    const v=setup();await v.execute("prepare_candidates");await v.execute("generate_schedule");
    await v.execute("set_cost",{kind:"ticket",selector:optionals[0].id,value:"100"});
    await v.execute("preview_exclusion",{selector:optionals[0].id});const shown=v.result.current.adjustment.state.response!;
    const expected=calculateScheduleCosts(shown,v.initial.request,v.result.current.getCosts());
    expect((await v.execute("adopt_preview")).text).toContain("已采用");
    expect(v.result.current.schedule.capture()!.original).toEqual(shown);expect(v.result.current.budget()).toEqual(expected);
    expect(v.result.current.candidates.excludedIds.has(optionals[0].id)).toBe(true);
    expect((await v.execute("adopt_preview")).text).toContain("已过期");expect(api.schedule).toHaveBeenCalledTimes(2);
    await v.execute("generate_schedule");expect(api.schedule.mock.calls[2][0]).toEqual(shown.request);
  });
  it("required exclusion cannot query or mutate the original plan",async()=>{
    const v=setup();await v.execute("generate_schedule");expect((await v.execute("preview_exclusion",{selector:required.id})).text).toContain("只能预览排除");
    expect(api.schedule).toHaveBeenCalledTimes(1);
  });
  it.each(["success","error"])("cancelled old %s never overwrites the next action; no duplicate generation",async kind=>{
    const v=setup(),late=deferred<ScheduleResponse>();api.schedule.mockReturnValueOnce(late.promise);
    const signal=new AbortController();let task!:Promise<PlanningReply>;
    act(()=>{task=v.result.current.execute({name:"generate_schedule",arguments:{}},signal.signal);});
    expect((await v.execute("generate_schedule")).text).toContain("仍在进行");
    act(()=>signal.abort());await v.execute("set_stay",{selector:required.id,minutes:90});await v.execute("generate_schedule");
    const fresh=v.result.current.schedule.capture()!.original;
    await act(async()=>{if(kind==="success")late.resolve(adjustmentResponse(api.schedule.mock.calls[0][0]));else late.reject(new Error("old"));await task;});
    expect(v.result.current.schedule.capture()!.original).toEqual(fresh);expect(api.schedule).toHaveBeenCalledTimes(2);
  });
  it("cancelling a pending preview preserves formal input and late completion cannot adopt",async()=>{
    const v=setup();await v.execute("prepare_candidates");await v.execute("generate_schedule");const original=v.result.current.schedule.capture()!.original;
    const late=deferred<ScheduleResponse>();api.schedule.mockReturnValueOnce(late.promise);const signal=new AbortController();let task!:Promise<PlanningReply>;
    act(()=>{task=v.result.current.execute({name:"preview_exclusion",arguments:{selector:optionals[0].id}},signal.signal);});
    act(()=>signal.abort());await act(async()=>{late.resolve(adjustmentResponse(api.schedule.mock.calls[1][0]));await task;});
    expect(v.result.current.schedule.capture()!.original).toEqual(original);expect(v.result.current.adjustment.state.status).toBe("idle");expect(v.result.current.candidates.excludedIds.size).toBe(0);
  });
  it("save uncertainty repeats exactly frozen operation despite subsequent fees and never overwrites it",async()=>{
    const v=setup();await v.execute("generate_schedule");api.save.mockRejectedValueOnce(new Error("提交状态未知"));
    expect((await v.execute("save_trip",{name:"首次"})).text).toContain("未知");const op=api.save.mock.calls[0][0];
    await v.execute("set_cost",{kind:"other",selector:null,value:"200"});api.save.mockResolvedValueOnce({name:"首次"} as SavedDetail);
    await v.execute("save_trip",{name:"不要新建"});expect(api.save.mock.calls[1][0]).toBe(op);expect(op.snapshot.costs.other).toBe("");
  });
  it("history read/restoration returns isolated inputs and never injects historical success",async()=>{
    const v=setup(),record=savedCityFixture();api.list.mockResolvedValue({items:[record],offset:0,limit:10,has_more:false});api.read.mockResolvedValue(record);
    await v.execute("list_saved",{offset:0});const answer=await v.execute("restore_saved",{selector:"1"});
    expect(answer.restored?.source).toEqual(record);expect(answer.restored?.inputs.request).not.toBe(record.snapshot.requirements);
    expect(v.result.current.schedule.capture()).toBeNull();expect(api.schedule).not.toHaveBeenCalled();
    answer.restored!.inputs.costs.other="changed";expect(record.snapshot.costs.other).toBe("bad");
  });
  it("history pages use the requested offset and reject illegal offsets without querying or generating",async()=>{
    const v=setup(),record=savedCityFixture();api.list.mockResolvedValue({items:[record],offset:10,limit:10,has_more:false});
    const answer=await v.execute("list_saved",{offset:10});
    expect(api.list).toHaveBeenCalledWith(10,expect.any(AbortSignal));expect(answer.text).toContain("第2页");
    for(const args of [{}, {offset:-1}, {offset:10001}, {offset:1.5}])expect((await v.execute("list_saved",args)).text).toContain("分页参数无效");
    expect(api.list).toHaveBeenCalledTimes(1);expect(api.schedule).not.toHaveBeenCalled();expect(api.save).not.toHaveBeenCalled();
  });
  it("daily window uses existing edit reconciliation and returns a new complete input set",async()=>{
    const v=setup();await v.execute("set_cost",{kind:"other",selector:null,value:"50"});
    const answer=await v.execute("set_daily_window",{start:"10:00",end:"17:00"});
    expect(answer.replacement?.request.daily_start_time).toBe("10:00");expect(answer.replacement?.costs.other).toBe("50");
    expect(v.initial.request.daily_start_time).toBe("09:00");expect(api.schedule).not.toHaveBeenCalled();
  });
  it("late blocked capability and unmounted callbacks cannot request or edit",async()=>{
    const v=setup(),old=v.result.current.execute;v.block();
    await act(async()=>{expect((await old({name:"generate_schedule",arguments:{}})).text).toContain("失效");});
    v.unmount();await old({name:"prepare_candidates",arguments:{}});expect(api.schedule).not.toHaveBeenCalled();expect(api.candidates).not.toHaveBeenCalled();
  });
  it("event-time version advances for candidate, stay and cost ABA changes",async()=>{
    const v=setup();const versions=[v.result.current.readVersion()];
    await v.execute("prepare_candidates");versions.push(v.result.current.readVersion());
    await v.execute("set_stay",{selector:required.id,minutes:90});versions.push(v.result.current.readVersion());
    await v.execute("set_stay",{selector:required.id,minutes:60});versions.push(v.result.current.readVersion());
    await v.execute("set_cost",{kind:"other",selector:null,value:"12"});versions.push(v.result.current.readVersion());
    await v.execute("set_cost",{kind:"other",selector:null,value:""});versions.push(v.result.current.readVersion());
    expect(versions.every((version,index)=>index===0||version>versions[index-1])).toBe(true);
  });
  it("abort during save retains frozen operation and late success cannot replace an explicit retry",async()=>{
    const v=setup();await v.execute("generate_schedule");const first=deferred<SavedDetail>(),second=deferred<SavedDetail>();
    api.save.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const signal=new AbortController();let one!:Promise<PlanningReply>,two!:Promise<PlanningReply>;
    act(()=>{one=v.result.current.execute({name:"save_trip",arguments:{name:"冻结存档"}},signal.signal);});
    const operation=api.save.mock.calls[0][0];act(()=>signal.abort());
    expect(v.result.current.saving.readState().status).toBe("uncertain");
    act(()=>{two=v.result.current.execute({name:"save_trip",arguments:{name:"新名字不会覆盖"}});});
    await act(async()=>{first.resolve({name:"旧完成"} as SavedDetail);await one;});
    expect(v.result.current.saving.readState().status).toBe("saving");expect(api.save.mock.calls[1][0]).toBe(operation);
    await act(async()=>{second.resolve({name:"冻结存档"} as SavedDetail);await two;});
    expect(v.result.current.saving.readState().status).toBe("saved");
  });
  it("weather query reads fresh response immediately, never clears a formal draft or queries routes",async()=>{
    const v=setup();await v.execute("generate_schedule");const before=v.result.current.schedule.capture()!.original;
    expect((await v.execute("query_weather")).text).toContain("上海市");expect(v.result.current.schedule.capture()!.original).toEqual(before);
    expect(api.weather).toHaveBeenCalledTimes(1);expect(api.schedule).toHaveBeenCalledTimes(1);
  });
});

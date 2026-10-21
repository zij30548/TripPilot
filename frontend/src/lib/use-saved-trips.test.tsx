import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useSaveTrip, useSavedHistory } from "./use-saved-trips";
import { useSchedulePreview } from "./use-schedule-preview";
import { emptyCostInputs } from "./schedule-costs";
import { deferred, schedule, tripRequest } from "./weather-schedule-check.test-fixtures";
import type { SavedDetail } from "./saved-trips";
const api=vi.hoisted(()=>({save:vi.fn(),read:vi.fn(),list:vi.fn(),schedule:vi.fn()}));
vi.mock("./saved-trips",async original=>({...await original<typeof import("./saved-trips")>(),saveTrip:api.save,readTrip:api.read,listTrips:api.list}));
vi.mock("./schedule-api",async original=>({...await original<typeof import("./schedule-api")>(),querySchedulePreview:api.schedule}));
beforeEach(()=>{vi.resetAllMocks();api.schedule.mockImplementation(async req=>schedule(req));});
async function setup(){
  const costs=emptyCostInputs();
  const view=renderHook(()=>{const preview=useSchedulePreview(tripRequest());return{preview,saving:useSaveTrip(preview,tripRequest(),()=>costs)};});
  await act(async()=>{await view.result.current.preview.query();});return{...view,costs};
}
describe("frozen saves and independent history reads",()=>{
  it("blocks duplicate clicks, freezes input, old success never claims current content is saved",async()=>{
    const v=await setup(),pending=deferred<SavedDetail>();api.save.mockReturnValue(pending.promise);
    let work!:Promise<void>|undefined;act(()=>{work=v.result.current.saving.save("原快照");void v.result.current.saving.save("双击");});
    const operation=api.save.mock.calls[0][0];v.costs.other="999";act(()=>v.result.current.preview.setStayMinutes(tripRequest().must_visit_places![0].id,"70"));
    expect(operation.snapshot.costs.other).toBe("");expect(api.save).toHaveBeenCalledTimes(1);
    await act(async()=>{pending.resolve({name:"原快照"} as SavedDetail);await work;});
    expect(v.result.current.saving.state.message).toContain("点击时快照");expect(v.result.current.preview.state.response).toBeNull();
  });
  it("response loss retries exactly frozen content and operation, not changed current fees",async()=>{
    const v=await setup();api.save.mockRejectedValueOnce(new Error("未知提交状态"));
    await act(async()=>{await v.result.current.saving.save("存档");});const operation=api.save.mock.calls[0][0];v.costs.other="20";
    api.save.mockResolvedValueOnce({name:"存档"});await act(async()=>{await v.result.current.saving.retry();});
    expect(api.save.mock.calls[1][0]).toBe(operation);expect(api.save.mock.calls[1][0].snapshot.costs.other).toBe("");
  });
  it("current capability blocks retained save and frozen retry callbacks without consuming or replacing their operation",async()=>{
    let allowed=true;const canOperate=()=>allowed,costs=emptyCostInputs();
    const v=renderHook(()=>{const preview=useSchedulePreview(tripRequest(),undefined,undefined,canOperate);return{preview,saving:useSaveTrip(preview,tripRequest(),()=>costs,canOperate)};});
    await act(async()=>{await v.result.current.preview.query();});
    const retainedSave=v.result.current.saving.save,original=v.result.current.preview.state.response;
    allowed=false;await act(async()=>{await retainedSave("拒绝冻结");});
    expect(api.save).not.toHaveBeenCalled();expect(v.result.current.saving.state.status).toBe("idle");
    allowed=true;api.save.mockRejectedValueOnce(new Error("提交未知"));
    await act(async()=>{await v.result.current.saving.save("冻结内容");});
    const operation=v.result.current.saving.state.operation,retainedRetry=v.result.current.saving.retry;
    allowed=false;await act(async()=>{await retainedRetry();await retainedSave("不能覆盖冻结内容");});
    expect(api.save).toHaveBeenCalledTimes(1);expect(v.result.current.saving.state.operation).toBe(operation);
    expect(v.result.current.preview.state.response).toBe(original);
    allowed=true;api.save.mockResolvedValueOnce({name:"冻结内容"});
    await act(async()=>{await retainedRetry();});
    expect(api.save.mock.calls[1][0]).toBe(operation);
  });
  it.each(["resolve","reject"] as const)("cancel then retry isolates old %s and old cancel; unmount aborts",async finish=>{
    const v=await setup(),first=deferred<SavedDetail>(),second=deferred<SavedDetail>();api.save.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    let a!:Promise<void>|undefined,b!:Promise<void>|undefined;act(()=>{a=v.result.current.saving.save("保存");});const oldCancel=v.result.current.saving.cancel;
    act(()=>{v.result.current.saving.cancel();});act(()=>{b=v.result.current.saving.retry();});act(()=>oldCancel());
    await act(async()=>{if(finish==="resolve")first.resolve({} as SavedDetail);else first.reject(new Error("old"));await a;});
    expect(v.result.current.saving.state.status).toBe("saving");expect(api.save.mock.calls[0][1].aborted).toBe(true);
    v.unmount();expect(api.save.mock.calls[1][1].aborted).toBe(true);await act(async()=>{second.reject(new Error("late"));await b;});
  });
  it("rapid history selection and list return cannot show old record or error",async()=>{
    const v=renderHook(()=>useSavedHistory()),a=deferred<SavedDetail>(),b=deferred<SavedDetail>();api.read.mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise);
    let one!:Promise<void>,two!:Promise<void>;act(()=>{one=v.result.current.load("a");two=v.result.current.load("b");});
    await act(async()=>{b.resolve({id:"b"} as SavedDetail);await two;});await act(async()=>{a.reject(new Error("old"));await one;});
    expect(v.result.current.state.detail?.id).toBe("b");expect(api.read.mock.calls[0][1].aborted).toBe(true);
    api.list.mockResolvedValue({items:[],offset:0,limit:10,has_more:false});await act(async()=>{await v.result.current.load(null);});expect(v.result.current.state.detail).toBeNull();
  });
});

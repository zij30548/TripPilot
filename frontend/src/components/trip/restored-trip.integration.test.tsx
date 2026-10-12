import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import RestoredTripPage from "./restored-trip";
import { savedFixture } from "@/lib/restore-trip.test-fixtures";
import { adjustmentResponse } from "@/lib/schedule-adjustment.test-fixtures";
import { deferred, now, required } from "@/lib/weather-schedule-check.test-fixtures";
import type { SavedDetail, SaveOperation } from "@/lib/saved-trips";
import type { ScheduleResponse } from "@/types/schedule";
const api=vi.hoisted(()=>({read:vi.fn(),save:vi.fn(),schedule:vi.fn(),candidates:vi.fn(),weather:vi.fn(),plan:vi.fn(),explain:vi.fn(),intent:vi.fn()}));
vi.mock("@/lib/saved-trips",async original=>({...await original<typeof import("@/lib/saved-trips")>(),readTrip:api.read,saveTrip:api.save}));
vi.mock("@/lib/schedule-api",async original=>({...await original<typeof import("@/lib/schedule-api")>(),querySchedulePreview:api.schedule}));
vi.mock("@/lib/candidates-api",async original=>({...await original<typeof import("@/lib/candidates-api")>(),queryCandidates:api.candidates}));
vi.mock("@/lib/weather-api",async original=>({...await original<typeof import("@/lib/weather-api")>(),queryWeatherForecast:api.weather}));
vi.mock("@/lib/api",async original=>({...await original<typeof import("@/lib/api")>(),planTrip:api.plan}));
vi.mock("@/lib/explanation-api",async original=>({...await original<typeof import("@/lib/explanation-api")>(),queryExplanation:api.explain}));
vi.mock("@/lib/adjustment-intent-api",async original=>({...await original<typeof import("@/lib/adjustment-intent-api")>(),queryAdjustmentIntent:api.intent}));
const click=async(name:string)=>act(async()=>{fireEvent.click(screen.getByRole("button",{name}));});
const change=(label:string|RegExp,value:string)=>fireEvent.change(screen.getByLabelText(label),{target:{value}});
const working=()=>within(screen.getByLabelText("旅行结果"));
const source=savedFixture(), newRecords:SavedDetail[]=[];
const inactive=()=>{for(const mock of [api.candidates,api.weather,api.plan,api.explain,api.intent])expect(mock).not.toHaveBeenCalled();};
beforeEach(()=>{
  vi.resetAllMocks();vi.useFakeTimers({toFake:["Date"]});vi.setSystemTime(now);newRecords.length=0;
  api.read.mockResolvedValue(structuredClone(source));api.schedule.mockImplementation(async r=>adjustmentResponse(r));
  api.save.mockImplementation(async(o:SaveOperation)=>{const s=o.snapshot.schedule;const record:SavedDetail={id:"b".repeat(32),name:o.name,saved_at:"2026-10-12T00:00:00Z",schema_version:"saved-trip-v1",start_date:s.request.start_date,end_date:s.request.end_date,transport_mode:s.request.transport_mode,status:s.status,snapshot:structuredClone(o.snapshot)};newRecords.push(record);return record;});
});
afterEach(()=>vi.useRealTimers());
async function open(){const view=render(<RestoredTripPage recordId={source.id}/>);await act(async()=>{});return view;}
describe("real restore/form/workflow composition (controlled services)",()=>{
  it("reads only once, does not mount a historical success; raw invalid/zero inputs editable without totals or saving",async()=>{
    await open();expect(api.read).toHaveBeenCalledTimes(1);inactive();expect(api.schedule).not.toHaveBeenCalled();expect(api.save).not.toHaveBeenCalled();
    expect(screen.getByRole("button",{name:"保存当前正式草案"})).toHaveProperty("disabled",true);
    expect(screen.getByLabelText("停留分钟：同名地点")).toHaveProperty("value","60");expect(screen.getByLabelText("可选停留分钟：同名地点")).toHaveProperty("value","60");
    const fees=within(screen.getByRole("region",{name:"工作副本费用输入"}));expect(fees.getByRole("textbox",{name:/全程其他/})).toHaveProperty("value","bad");
    expect(fees.getAllByRole("textbox",{name:/门票/})[0]).toHaveProperty("value","0");expect(working().queryByRole("region",{name:"预算核对汇总"})).toBeNull();
    fireEvent.change(fees.getByRole("textbox",{name:/全程其他/}),{target:{value:"0"}});inactive();expect(api.schedule).not.toHaveBeenCalled();expect(source.snapshot.costs.other).toBe("bad");
    const leave=new Event("beforeunload",{cancelable:true});window.dispatchEvent(leave);expect(leave.defaultPrevented).toBe(true);
  });
  it("cancel edits preserves live stays, raw fees and formal result; local confirmation never calls Mock plan",async()=>{
    await open();change("停留分钟：同名地点","75");await click("生成行程草案");expect(api.schedule.mock.calls[0][0].duration_settings[0]).toEqual({place_id:required.id,minutes:75,source:"user"});
    const timeline=working().getByRole("list",{name:"草案时间线"}).textContent;
    await click("修改旅行需求");change(/旅行人数/,"3");await click("取消需求修改");
    expect(working().getByRole("list",{name:"草案时间线"}).textContent).toBe(timeline);expect(screen.getByLabelText("停留分钟：同名地点")).toHaveProperty("value","75");
    await click("修改旅行需求");expect(screen.getByLabelText(/旅行人数/)).toHaveProperty("value","2");change(/总预算/,"4000");await click("确认需求修改");
    expect(screen.getByRole("button",{name:"保存当前正式草案"})).toHaveProperty("disabled",true);expect(working().queryByRole("list",{name:"草案时间线"})).toBeNull();
    expect(within(screen.getByRole("region",{name:"工作副本费用输入"})).getByRole("textbox",{name:/全程其他/})).toHaveProperty("value","bad");
    expect(api.schedule).toHaveBeenCalledTimes(1);inactive();
  });
  it.each(["travelers","date"])("confirmed %s change clears all fees but retains places and stay source, with no auto-generation",async kind=>{
    await open();change("停留分钟：同名地点","75");await click("修改旅行需求");
    if(kind==="travelers")change(/旅行人数/,"3");else {change(/开始日期/,"2026-10-11");change(/结束日期/,"2026-10-11");}
    await click("确认需求修改");expect(screen.getByText(/全部用户费用估算已清空；没有自动缩放/)).toBeTruthy();
    expect(within(screen.getByRole("region",{name:"工作副本费用输入"})).getAllByRole("textbox").every(e=>(e as HTMLInputElement).value==="")).toBe(true);
    expect(screen.getByLabelText("停留分钟：同名地点")).toHaveProperty("value","75");expect(screen.getByLabelText("可选停留分钟：同名地点")).toHaveProperty("value","60");
    expect(api.schedule).not.toHaveBeenCalled();inactive();
  });
  it("interest edit clears optional history and exclusions, preserves required estimate and incompatible lunch until corrected",async()=>{
    await open();await click("修改旅行需求");fireEvent.click(screen.getByRole("checkbox",{name:/美食/}));change(/最晚结束/,"11:00");await click("确认需求修改");
    expect(screen.queryByLabelText("可选停留分钟：同名地点")).toBeNull();expect(screen.getByRole("button",{name:"生成行程草案"})).toHaveProperty("disabled",true);
    expect(working().getAllByText(/午餐.*完整.*每日/).length).toBeGreaterThan(0);expect(api.schedule).not.toHaveBeenCalled();inactive();
  });
  it("failed generation keeps editable inputs/reference, later validated success alone is saved as a new frozen record",async()=>{
    const original=structuredClone(source);await open();change("停留分钟：同名地点","75");api.schedule.mockRejectedValueOnce(new Error("offline"));await click("生成行程草案");
    expect(screen.getByRole("button",{name:"保存当前正式草案"})).toHaveProperty("disabled",true);expect(screen.getByLabelText("停留分钟：同名地点")).toHaveProperty("value","75");
    expect(screen.getByText("只读历史参考 · 不是新正式草案")).toBeTruthy();await click("生成行程草案");await click("保存当前正式草案");
    expect(newRecords).toHaveLength(1);expect(newRecords[0].id).not.toBe(source.id);expect(newRecords[0].snapshot.schedule.request.duration_settings[0].minutes).toBe(75);
    expect(newRecords[0].snapshot.costs.other).toBe("bad");expect(source).toEqual(original);expect(api.save.mock.calls[0][0].operation_id).toHaveLength(32);inactive();
  });
  it.each(["success","error"])("confirming edit while generation is pending isolates old %s/finally without losing the new input",async outcome=>{
    await open();const pending=deferred<ScheduleResponse>();api.schedule.mockReturnValueOnce(pending.promise);await click("生成行程草案");const [request,signal]=api.schedule.mock.calls[0];
    await click("修改旅行需求");change(/总预算/,"5000");await click("确认需求修改");expect(signal.aborted).toBe(true);
    await act(async()=>{if(outcome==="success")pending.resolve(adjustmentResponse(request));else pending.reject(new Error("obsolete"));});
    expect(screen.getByRole("button",{name:"保存当前正式草案"})).toHaveProperty("disabled",true);expect(working().queryByRole("list",{name:"草案时间线"})).toBeNull();
    await click("生成行程草案");await click("保存当前正式草案");expect(newRecords[0].snapshot.requirements.budget).toBe(5000);inactive();
  });
});

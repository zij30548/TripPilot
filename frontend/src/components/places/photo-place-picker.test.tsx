import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import PhotoPlacePicker from "./photo-place-picker";
import TripRequestForm from "../trip-request-form";
import * as requirementPicker from "./requirement-place-picker";
import { parsePhotoResult, type PhotoResult } from "@/lib/photo-api";
import { cityRequest, located } from "@/lib/destination.test-fixtures";
import { tripRequest, deferred } from "@/lib/weather-schedule-check.test-fixtures";
const mock = vi.hoisted(() => ({ recognize: vi.fn(), search: vi.fn(), create: vi.fn(), revoke: vi.fn() }));
vi.mock("@/lib/photo-api", async original => ({ ...await original<typeof import("@/lib/photo-api")>(), recognizePhoto: mock.recognize }));
vi.mock("@/lib/places-api", () => ({ searchPlaces: mock.search }));
const response = (city: string | null = "上海"): PhotoResult => ({ contract_version:"photo-landmark-v1", request_id:"r",destination_id:"shanghai", model:"qwen3-vl-plus",usage:{input_tokens:20,output_tokens:30},status:"possible",clues:[{name:"测试景点",city,basis:"可见塔身"}] });
const place = located({id:"photo-fixture",name:"测试景点",address:"测试地址",source:"amap",category:null,longitude:121,latitude:31});
const file = () => new File(["controlled fixture"], "image.png", {type:"image/png"});
function selectFile(f = file()) { fireEvent.change(screen.getByLabelText(/选择一张照片/), { target: { files:[f] } }); }
function mount() { const controller=new AbortController(), confirm=vi.fn(),cancel=vi.fn(); const view=render(<PhotoPlacePicker destinationId="shanghai" sessionSignal={controller.signal} excludedIds={[]} onConfirm={confirm} onCancel={cancel} />);return {controller,confirm,cancel,...view}; }
beforeEach(() => {
  vi.clearAllMocks(); mock.recognize.mockResolvedValue(response());mock.search.mockResolvedValue([place]);
  mock.create.mockReturnValue("blob:local-photo"); URL.createObjectURL=mock.create;URL.revokeObjectURL=mock.revoke;
});
describe("photo clues are not confirmed POIs", () => {
  it("select only previews, explicit model then explicit search then selection AND confirmation", async () => {
    const {confirm}=mount(); selectFile(); expect(mock.recognize).not.toHaveBeenCalled();expect(mock.search).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button",{name:"识别照片"}));
    await screen.findByText("可能地点：测试景点"); expect(confirm).not.toHaveBeenCalled();expect(mock.search).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button",{name:"用此线索准备搜索"}));expect(mock.search).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button",{name:"搜索"}));await screen.findByText("地址：测试地址");
    expect(screen.getByRole("button",{name:"确认必去地点"}).hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByRole("button",{name:/测试景点.*测试地址/}));expect(confirm).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button",{name:"确认必去地点"})); expect(confirm).toHaveBeenCalledWith(place);
  });
  it.each(["北京","深圳",null])("mismatch/unsupported/unknown city %s never searches", async city => {
    mock.recognize.mockResolvedValue(response(city));mount();selectFile();fireEvent.click(screen.getByRole("button",{name:"识别照片"}));
    await screen.findByText("可能地点：测试景点");expect(screen.getByRole("button",{name:"用此线索准备搜索"}).hasAttribute("disabled")).toBe(true);expect(mock.search).not.toHaveBeenCalled();
  });
  it.each(["success","error"])("file ABA isolates late %s and finally; duplicate clicks and same file reselection", async kind => {
    const pending=deferred<PhotoResult>();mock.recognize.mockReturnValueOnce(pending.promise);const {confirm}=mount();const f=file();selectFile(f);
    fireEvent.click(screen.getByRole("button",{name:"识别照片"}));fireEvent.click(screen.getByRole("button",{name:"正在识别…"}));expect(mock.recognize).toHaveBeenCalledTimes(1);
    selectFile(file());selectFile(f);expect(mock.revoke).toHaveBeenCalledTimes(2);
    const current=deferred<PhotoResult>();mock.recognize.mockReturnValueOnce(current.promise);fireEvent.click(screen.getByRole("button",{name:"识别照片"}));
    await act(async()=>{ if(kind==="success")pending.resolve(response());else pending.reject(new Error("old failure")); });
    expect(screen.queryByText(/可能地点：/)).toBeNull();expect(screen.queryByText("old failure")).toBeNull();expect(screen.getByRole("button",{name:"正在识别…"}).hasAttribute("disabled")).toBe(true);expect(confirm).not.toHaveBeenCalled();
    await act(async()=>current.resolve(response()));await screen.findByText("可能地点：测试景点");
  });
  it("abort/unmount releases URL and prevents late result; cancel allows manual retry", async()=>{
    const pending=deferred<PhotoResult>();mock.recognize.mockReturnValue(pending.promise);const v=mount();selectFile();fireEvent.click(screen.getByRole("button",{name:"识别照片"}));
    fireEvent.click(screen.getByRole("button",{name:"取消识别"}));expect(mock.recognize.mock.calls[0][3].aborted).toBe(true);
    await act(async()=>pending.resolve(response()));expect(screen.queryByText(/可能地点：/)).toBeNull();
    v.unmount();expect(mock.revoke).toHaveBeenCalledWith("blob:local-photo");expect(v.confirm).not.toHaveBeenCalled();
  });
  it("upload error and invalid result do not confirm; strict API rejects fabricated POI and context",()=>{
    for(const bad of [{...response(),status:["possible"]},{...response(),destination_id:"beijing"},{...response(),request_id:"old"},{...response(),clues:[{...response().clues[0],id:"fake"}]},{...response(),clues:response().clues.concat(response().clues)}])expect(()=>parsePhotoResult(bad,"r","shanghai")).toThrow();
    expect(parsePhotoResult(response(),"r","shanghai")).toEqual(response());
    mount();selectFile(new File(["bad"],"bad.svg",{type:"image/svg+xml"}));expect(screen.getByRole("alert").textContent).toContain("5 MiB");expect(mock.recognize).not.toHaveBeenCalled();
  });
  it("form photo entry blocks submission draft; city change aborts; cancel editing leaves original immutable", async()=>{
    const initial=cityRequest(tripRequest(),"shanghai"), original=structuredClone(initial), confirm=vi.fn(),cancel=vi.fn();
    const pending=deferred<PhotoResult>();mock.recognize.mockReturnValue(pending.promise);
    render(<TripRequestForm initialRequest={initial} onConfirm={confirm} onCancel={cancel}/>);
    fireEvent.click(screen.getByRole("button",{name:"从照片发现必去地点"}));selectFile();fireEvent.click(screen.getByRole("button",{name:"识别照片"}));
    fireEvent.click(screen.getByRole("button",{name:"确认需求修改"}));expect(confirm).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole("combobox",{name:"目的地"}),{target:{value:"beijing"}});expect(mock.recognize.mock.calls[0][3].aborted).toBe(true);
    fireEvent.change(screen.getByRole("combobox",{name:"目的地"}),{target:{value:"shanghai"}});
    await act(async()=>pending.resolve(response()));expect(screen.queryByText(/可能地点：/)).toBeNull();
    fireEvent.click(screen.getByRole("button",{name:"取消需求修改"}));expect(cancel).toHaveBeenCalledOnce();expect(initial).toEqual(original);
  });
  it("six required blocks entry without changing existing requirements",()=>{
    const initial=cityRequest(tripRequest(),"shanghai");initial.must_visit_places=Array.from({length:6},(_,i)=>({...place,id:`fixture-${i}`}));initial.must_visit=initial.must_visit_places.map(p=>p.name);
    render(<TripRequestForm initialRequest={initial} onConfirm={vi.fn()}/>);
    expect(screen.getByRole("button",{name:"从照片发现必去地点"}).hasAttribute("disabled")).toBe(true);expect(screen.getByRole("button",{name:"添加必去地点"}).hasAttribute("disabled")).toBe(true);
  });
  it("old search cancel cannot close a later clue session", async()=>{
    let cancel:()=>void=()=>{}, signal:AbortSignal|undefined;
    const spy=vi.spyOn(requirementPicker,"default").mockImplementation(props=>{
      cancel=props.onCancel;signal=props.sessionSignal;return <p>受控搜索选择器</p>;
    });
    try {
      mount();selectFile();fireEvent.click(screen.getByRole("button",{name:"识别照片"}));
      await screen.findByText("可能地点：测试景点");
      fireEvent.click(screen.getByRole("button",{name:"用此线索准备搜索"}));const oldCancel=cancel,oldSignal=signal;
      fireEvent.click(screen.getByRole("button",{name:"用此线索准备搜索"}));const currentSignal=signal;
      expect(oldSignal?.aborted).toBe(true);
      act(()=>oldCancel());expect(currentSignal?.aborted).toBe(false);expect(screen.queryByText("受控搜索选择器")).not.toBeNull();
      act(()=>cancel());expect(currentSignal?.aborted).toBe(true);expect(screen.queryByText("受控搜索选择器")).toBeNull();
    } finally {spy.mockRestore();}
  });
  it("confirmed high德 POI enters only editable required draft; duplicate is refused, no nested form", async()=>{
    const initial=cityRequest(tripRequest(),"shanghai"),confirm=vi.fn(); const original=structuredClone(initial);
    render(<TripRequestForm initialRequest={initial} onConfirm={confirm}/>);
    fireEvent.click(screen.getByRole("button",{name:"从照片发现必去地点"}));selectFile();fireEvent.click(screen.getByRole("button",{name:"识别照片"}));
    await screen.findByText("可能地点：测试景点");fireEvent.click(screen.getByRole("button",{name:"用此线索准备搜索"}));
    expect(document.querySelector("form form")).toBeNull();
    fireEvent.submit(screen.getByLabelText("搜索上海地点").closest("form")!);
    await screen.findByText("地址：测试地址");expect(confirm).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button",{name:/候选 1.*测试景点/}));fireEvent.click(screen.getByRole("button",{name:"确认必去地点"}));
    expect(initial).toEqual(original);expect(confirm).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button",{name:"从照片发现必去地点"}));selectFile();fireEvent.click(screen.getByRole("button",{name:"识别照片"}));
    await screen.findByText("可能地点：测试景点");fireEvent.click(screen.getByRole("button",{name:"用此线索准备搜索"}));fireEvent.click(screen.getByRole("button",{name:"搜索"}));
    await screen.findByText("已在必去地点中，不可重复添加");fireEvent.click(screen.getByRole("button",{name:/候选 1.*测试景点/}));
    expect(screen.getByRole("button",{name:"确认必去地点"}).hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByRole("button",{name:"关闭照片入口"}));fireEvent.click(screen.getByRole("button",{name:"确认需求修改"}));
    expect(confirm).toHaveBeenCalledOnce();expect(confirm.mock.calls[0][0].must_visit_places.at(-1)).toEqual(place);
  });
});

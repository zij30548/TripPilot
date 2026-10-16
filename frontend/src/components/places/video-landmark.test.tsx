import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TripRequestForm from "../trip-request-form";
import { cityRequest, located } from "@/lib/destination.test-fixtures";
import { tripRequest, deferred } from "@/lib/weather-schedule-check.test-fixtures";
import type { PhotoResult } from "@/lib/photo-api";
import * as picker from "./requirement-place-picker";

const m=vi.hoisted(()=>({extract:vi.fn(),photo:vi.fn(),search:vi.fn(),revoke:vi.fn(),create:vi.fn()}));
vi.mock("@/lib/video-api",async original=>({...await original<typeof import("@/lib/video-api")>(),extractVideo:m.extract}));
vi.mock("@/lib/photo-api",async original=>({...await original<typeof import("@/lib/photo-api")>(),recognizePhoto:m.photo}));
vi.mock("@/lib/places-api",()=>({searchPlaces:m.search}));
const p=located({id:"frame-poi",name:"受控景点",address:"受控地址",longitude:121,latitude:31,category:null,source:"amap"});
const result=(city:string|null="Shanghai"):PhotoResult=>({contract_version:"photo-landmark-v2",request_id:"r",destination_id:"shanghai",model:"qwen3-vl-plus",usage:{input_tokens:1,output_tokens:1},status:"possible",clues:[{name:"受控景点",city}]});
const click=(name:string|RegExp)=>fireEvent.click(screen.getByRole("button",{name}));
const choose=()=>fireEvent.change(screen.getByLabelText(/选择一个短视频/),{target:{files:[new File(["video bytes"],"private-title.mp4",{type:"video/mp4"})]}});
function mount(full=false){const initial=cityRequest(tripRequest(),"shanghai");if(full){initial.must_visit_places=Array.from({length:6},(_,i)=>({...p,id:`p${i}`}));initial.must_visit=initial.must_visit_places.map(p=>p.name);}const confirm=vi.fn(),cancel=vi.fn();return{initial,confirm,cancel,...render(<TripRequestForm initialRequest={initial} onConfirm={confirm} onCancel={cancel}/>)};}
async function select(index=1){click("短视频画面准备");choose();click("提取画面");await screen.findByRole("button",{name:/放大画面1/});click(new RegExp(`放大画面${index}`));}
async function recognize(){click("识别选中画面");await screen.findByText("可能地点：受控景点");}
async function search(){click("用此线索准备搜索");expect(m.search).not.toHaveBeenCalled();click("搜索");await screen.findByText("地址：受控地址");click(/候选 1/);}
beforeEach(()=>{vi.clearAllMocks();let i=0;m.create.mockImplementation(()=>`blob:test-${++i}`);URL.createObjectURL=m.create;URL.revokeObjectURL=m.revoke;
  m.extract.mockResolvedValue({contract_version:"video-frames-v1",request_id:"r",duration_seconds:8,width:320,height:180,rotation_degrees:0,sample_count:2,
    frames:[1,2].map(index=>({index,target_seconds:index===1?2:6,width:320,height:180,jpeg_base64:btoa(`frame-${index}-only`)}))});m.photo.mockResolvedValue(result());m.search.mockResolvedValue([p]);});

describe("media-2B one selected frame reuses photo/POI confirmation",()=>{
  it("preview is local; only explicit click sends selected bytes once; independent URL and target time, not video/name",async()=>{
    mount();await select(2);expect(m.photo).not.toHaveBeenCalled();expect(m.search).not.toHaveBeenCalled();
    expect(screen.getByRole("region",{name:"选中画面发现必去地点"}).textContent).toContain("约6.00秒（目标采样时间");
    const pending=deferred<PhotoResult>();m.photo.mockReturnValue(pending.promise);click("识别选中画面");click("正在识别…");expect(m.photo).toHaveBeenCalledOnce();
    const file=m.photo.mock.calls[0][0] as File;expect(file.name).toBe("frame.jpg");expect(file.type).toBe("image/jpeg");
    const bytes=await new Promise<string>(resolve=>{const r=new FileReader();r.onload=()=>resolve(r.result as string);r.readAsText(file);});expect(bytes).toBe("frame-2-only");
    expect(m.create).toHaveBeenCalledTimes(4);click("关闭视频入口");expect(m.revoke).toHaveBeenCalledTimes(4);expect(m.photo.mock.calls[0][3].aborted).toBe(true);
    await act(async()=>pending.resolve(result()));expect(screen.queryByText("可能地点：受控景点")).toBeNull();
  });
  it("preview never saves, explicit POI confirmation only adds to edit draft; cancel leaves original",async()=>{
    const v=mount(),before=structuredClone(v.initial);await select();await recognize();await search();expect(v.confirm).not.toHaveBeenCalled();expect(v.initial).toEqual(before);
    click("确认必去地点");expect(screen.queryByRole("region",{name:"短视频画面准备"})).toBeNull();expect(screen.getByText("受控景点")).toBeTruthy();expect(v.confirm).not.toHaveBeenCalled();
    click("取消需求修改");expect(v.cancel).toHaveBeenCalledOnce();expect(v.initial).toEqual(before);
  });
  it("confirmed frame POI participates in normal requirements, never stores frame/source/blob",async()=>{
    const v=mount();await select();await recognize();await search();click("确认必去地点");click("确认需求修改");
    const request=v.confirm.mock.calls[0][0];expect(request.must_visit_places.at(-1)).toEqual(p);expect(JSON.stringify(request)).not.toMatch(/jpeg_base64|targetSeconds|blob:|private-title|frame.jpg/);
  });
  it.each(["uncertain","unrecognized"] as const)("%s has no search or automatic addition",async status=>{
    const v=mount();m.photo.mockResolvedValue({...result(),status,clues:[]});await select();click("识别选中画面");await screen.findByText(/请改用普通手动搜索/);
    expect(screen.queryByRole("button",{name:"用此线索准备搜索"})).toBeNull();expect(m.search).not.toHaveBeenCalled();expect(v.confirm).not.toHaveBeenCalled();
  });
  it.each(["Beijing",null,"unknown"])("city %s cannot start associated POI search",async city=>{
    mount();m.photo.mockResolvedValue(result(city));await select();await recognize();expect(screen.getByRole("button",{name:"用此线索准备搜索"}).hasAttribute("disabled")).toBe(true);expect(m.search).not.toHaveBeenCalled();
  });
  it("no city or full capacity means no recognition entry, even after extracting",async()=>{
    const v=mount(true);await select();expect(screen.queryByRole("button",{name:"识别选中画面"})).toBeNull();expect(m.photo).not.toHaveBeenCalled();v.unmount();
    render(<TripRequestForm onConfirm={vi.fn()}/>);await select();expect(screen.queryByRole("button",{name:"识别选中画面"})).toBeNull();expect(m.photo).not.toHaveBeenCalled();
  });
  it("same POI ID is rejected, raw model clue cannot bypass real selection",async()=>{
    const v=mount();m.search.mockResolvedValue([v.initial.must_visit_places![0]]);await select();await recognize();click("用此线索准备搜索");click("搜索");
    await screen.findByText("已在必去地点中，不可重复添加");click(/候选 1/);expect(screen.getByRole("button",{name:"确认必去地点"}).hasAttribute("disabled")).toBe(true);expect(v.confirm).not.toHaveBeenCalled();
  });
  it.each(["success","error"])("frame A→B→A invalidates old %s/finally without unlocking new pending request",async kind=>{
    mount();const old=deferred<PhotoResult>(),fresh=deferred<PhotoResult>();m.photo.mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise);
    await select();click("识别选中画面");click(/放大画面2/);click(/放大画面1/);expect(m.photo.mock.calls[0][3].aborted).toBe(true);click("识别选中画面");
    await act(async()=>{if(kind==="success")old.resolve(result());else old.reject(new Error("old failure"));});expect(screen.queryByText("old failure")).toBeNull();expect(screen.queryByText("可能地点：受控景点")).toBeNull();expect(screen.getByRole("button",{name:"正在识别…"}).hasAttribute("disabled")).toBe(true);
    await act(async()=>fresh.resolve(result()));await screen.findByText("可能地点：受控景点");
  });
  it.each(["file","extract","city","close","cancel","unmount"])("%s synchronously invalidates recognition and rejects late success",async action=>{
    const v=mount(),old=deferred<PhotoResult>();m.photo.mockReturnValue(old.promise);await select();click("识别选中画面");
    if(action==="file")choose();else if(action==="extract")click("提取画面");else if(action==="city")fireEvent.change(screen.getByRole("combobox",{name:"目的地"}),{target:{value:"beijing"}});else if(action==="close")click("关闭视频入口");else if(action==="cancel")click("取消需求修改");else v.unmount();
    expect(m.photo.mock.calls[0][3].aborted).toBe(true);await act(async()=>old.resolve(result()));expect(screen.queryByText("可能地点：受控景点")).toBeNull();expect(v.confirm).not.toHaveBeenCalled();
  });
  it.each(["success","error"])("pending POI search %s is isolated on changing frame",async kind=>{
    const v=mount(),pending=deferred<typeof p[]>();m.search.mockReturnValue(pending.promise);await select();await recognize();click("用此线索准备搜索");click("搜索");click(/放大画面2/);
    expect(m.search.mock.calls[0][1].aborted).toBe(true);await act(async()=>{if(kind==="success")pending.resolve([p]);else pending.reject(new Error("old search"));});expect(screen.queryByText("地址：受控地址")).toBeNull();expect(screen.queryByText("old search")).toBeNull();expect(v.confirm).not.toHaveBeenCalled();
  });
  it("old confirmation callback cannot add after frame changed and returned",async()=>{
    let confirm:((place:typeof p)=>void)|undefined;const spy=vi.spyOn(picker,"default").mockImplementation(props=>{confirm=props.onConfirm;return <p>controlled</p>;});
    try{const v=mount();await select();await recognize();click("用此线索准备搜索");const old=confirm!;click(/放大画面2/);click(/放大画面1/);act(()=>old(p));expect(screen.queryByText("受控景点")).toBeNull();expect(v.confirm).not.toHaveBeenCalled();}finally{spy.mockRestore();}
  });
});

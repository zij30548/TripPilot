import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import VideoFramePicker from "./video-frame-picker";
import TripRequestForm from "../trip-request-form";
import type { VideoResult } from "@/lib/video-api";
import { cityRequest } from "@/lib/destination.test-fixtures";
import { tripRequest, deferred } from "@/lib/weather-schedule-check.test-fixtures";

const mock=vi.hoisted(()=>({extract:vi.fn(),create:vi.fn(),revoke:vi.fn(),photo:vi.fn(),search:vi.fn()}));
vi.mock("@/lib/video-api",async original=>({...await original<typeof import("@/lib/video-api")>(),extractVideo:mock.extract}));
vi.mock("@/lib/photo-api",async original=>({...await original<typeof import("@/lib/photo-api")>(),recognizePhoto:mock.photo}));
vi.mock("@/lib/places-api",()=>({searchPlaces:mock.search}));
const result=():VideoResult=>({contract_version:"video-frames-v1",request_id:"r",duration_seconds:12,width:320,height:180,rotation_degrees:0,sample_count:3,
  frames:[1,2,3].map(index=>({index,target_seconds:4*index-2,width:320,height:180,jpeg_base64:btoa("\xff\xd8\xffcontrolled\xff\xd9")}))});
const file=()=>new File(["controlled"],"sample.mp4",{type:"video/mp4"});
function choose(f=file()){fireEvent.change(screen.getByLabelText(/选择一个短视频/),{target:{files:[f]}});}
function start(){fireEvent.click(screen.getByRole("button",{name:"提取画面"}));}
function mount(){const controller=new AbortController(),close=vi.fn();return{controller,close,...render(<VideoFramePicker sessionSignal={controller.signal} onClose={close}/>)};}
beforeEach(()=>{vi.clearAllMocks();let i=0;mock.create.mockImplementation(()=>`blob:test-${++i}`);URL.createObjectURL=mock.create;URL.revokeObjectURL=mock.revoke;mock.extract.mockResolvedValue(result());});

describe("video preview isolated from planning and photo recognition",()=>{
  it("choosing only previews locally; explicit extraction, ordering and enlarge never confirm or query other services",async()=>{
    mount();choose();expect(mock.extract).not.toHaveBeenCalled();expect(screen.getByLabelText("本地视频预览").getAttribute("src")).toBe("blob:test-1");
    start();await screen.findByRole("button",{name:"放大画面3，约10.00秒"});
    fireEvent.click(screen.getByRole("button",{name:"放大画面2，约6.00秒"}));expect(screen.getByAltText("放大画面2")).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByLabelText("放大画面预览"));
    expect(mock.extract).toHaveBeenCalledOnce();expect(mock.photo).not.toHaveBeenCalled();expect(mock.search).not.toHaveBeenCalled();
    expect(screen.queryByRole("button",{name:/识别画面|加入必去/})).toBeNull();
  });
  it.each(["success","error"])("A→B→A and same file reselection reject late %s and finally",async kind=>{
    const old=deferred<VideoResult>(),next=deferred<VideoResult>();mock.extract.mockReturnValueOnce(old.promise).mockReturnValueOnce(next.promise);
    mount();const a=file();choose(a);start();fireEvent.click(screen.getByRole("button",{name:"正在提取画面…"}));expect(mock.extract).toHaveBeenCalledOnce();
    choose(file());choose(a);expect(mock.extract.mock.calls[0][2].aborted).toBe(true);start();
    await act(async()=>{if(kind==="success")old.resolve(result());else old.reject(new Error("old failure"));});
    expect(screen.queryByText("old failure")).toBeNull();expect(screen.queryByRole("button",{name:/放大画面1/})).toBeNull();
    expect(screen.getByRole("button",{name:"正在提取画面…"}).hasAttribute("disabled")).toBe(true);
    await act(async()=>next.resolve(result()));await screen.findByRole("button",{name:/放大画面1/});
  });
  it("cancel, close and unmount release resources; old completion cannot allocate image URLs",async()=>{
    const p=deferred<VideoResult>();mock.extract.mockReturnValue(p.promise);const v=mount();choose();start();
    fireEvent.click(screen.getByRole("button",{name:"取消提取"}));expect(mock.extract.mock.calls[0][2].aborted).toBe(true);
    await act(async()=>p.resolve(result()));expect(mock.create).toHaveBeenCalledOnce();
    mock.extract.mockResolvedValue(result());start();await screen.findByRole("button",{name:/放大画面1/});
    fireEvent.click(screen.getByRole("button",{name:"关闭视频入口"}));expect(v.close).toHaveBeenCalledOnce();expect(mock.revoke).toHaveBeenCalledTimes(4);
    v.unmount();expect(mock.revoke).toHaveBeenCalledTimes(4);
  });
  it("invalid local file and server failure do not change requirement inputs",async()=>{
    mount();choose(new File([],"empty.mp4"));expect(screen.getByRole("alert").textContent).toContain("20 MiB");
    choose(new File(["x"],"playlist.m3u8"));expect(mock.extract).not.toHaveBeenCalled();
    mock.extract.mockRejectedValue(new Error("受控处理失败"));choose();start();await screen.findByText("受控处理失败");
    expect(screen.getByRole("button",{name:"提取画面"}).hasAttribute("disabled")).toBe(false);
  });
  it.each(["success","error"])("city ABA, close/reopen and cancel editing isolate %s without mutation",async kind=>{
    const initial=cityRequest(tripRequest(),"shanghai"),before=structuredClone(initial),confirm=vi.fn(),cancel=vi.fn();
    const p=deferred<VideoResult>();mock.extract.mockReturnValue(p.promise);
    render(<TripRequestForm initialRequest={initial} onConfirm={confirm} onCancel={cancel}/>);
    fireEvent.click(screen.getByRole("button",{name:"短视频画面准备"}));choose();start();
    fireEvent.change(screen.getByRole("combobox",{name:"目的地"}),{target:{value:"beijing"}});
    fireEvent.change(screen.getByRole("combobox",{name:"目的地"}),{target:{value:"shanghai"}});
    expect(mock.extract.mock.calls[0][2].aborted).toBe(true);
    fireEvent.click(screen.getByRole("button",{name:"短视频画面准备"}));
    await act(async()=>{if(kind==="success")p.resolve(result());else p.reject(new Error("old city"));});
    expect(screen.queryByRole("button",{name:/放大画面1/})).toBeNull();expect(screen.queryByText("old city")).toBeNull();
    fireEvent.click(screen.getByRole("button",{name:"关闭视频入口"}));fireEvent.click(screen.getByRole("button",{name:"短视频画面准备"}));
    expect(screen.queryByLabelText("本地视频预览")).toBeNull();fireEvent.click(screen.getByRole("button",{name:"取消需求修改"}));
    expect(cancel).toHaveBeenCalledOnce();expect(confirm).not.toHaveBeenCalled();expect(initial).toEqual(before);expect(mock.photo).not.toHaveBeenCalled();expect(mock.search).not.toHaveBeenCalled();
  });
  it("explicit unchanged confirmation closes video but does not add any frame to requirements; photo entry still opens",async()=>{
    const initial=cityRequest(tripRequest(),"shanghai"),confirm=vi.fn();render(<TripRequestForm initialRequest={initial} onConfirm={confirm}/>);
    fireEvent.click(screen.getByRole("button",{name:"短视频画面准备"}));choose();start();await screen.findByRole("button",{name:/放大画面1/});
    fireEvent.click(screen.getByRole("button",{name:"确认需求修改"}));expect(confirm).toHaveBeenCalledWith(initial);expect(screen.queryByLabelText("本地视频预览")).toBeNull();
    fireEvent.click(screen.getByRole("button",{name:"从照片发现必去地点"}));expect(screen.getByLabelText(/选择一张照片/)).toBeTruthy();expect(mock.photo).not.toHaveBeenCalled();
  });
});

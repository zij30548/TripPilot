import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import DialogueMap from "./dialogue-map";
import { captureFixture } from "@/lib/schedule-adjustment.test-fixtures";
import { drawingFixture } from "@/lib/schedule-drawing.test-fixtures";
import { validateDrawing } from "@/lib/schedule-drawing";
import { deferred, required } from "@/lib/weather-schedule-check.test-fixtures";
import type { AMapSDK } from "@/lib/amap-loader";

const loader=vi.hoisted(()=>({loadAMap:vi.fn(),getShanghaiCenter:vi.fn()}));
vi.mock("@/lib/amap-loader",()=>loader);
const maps:TestMap[]=[],markers:TestMarker[]=[],lines:TestLine[]=[];
class TestMap {setFitView=vi.fn();setZoomAndCenter=vi.fn();destroy=vi.fn();constructor(){maps.push(this);}}
class TestMarker {
  handler=()=>{};on=vi.fn((_event:string,handler:()=>void)=>{this.handler=handler;});off=vi.fn();setMap=vi.fn();setzIndex=vi.fn();
  constructor(readonly options:{content:HTMLElement;position:number[];title:string}){markers.push(this);}
}
class TestLine {setMap=vi.fn();constructor(readonly options:{path:number[][][]}){lines.push(this);}}
const sdk={Map:TestMap,Marker:TestMarker,Polyline:TestLine} as unknown as AMapSDK;
const click=async(name:string|RegExp)=>act(async()=>{fireEvent.click(screen.getByRole("button",{name}));});
function capture(){const value=captureFixture();value.drawing=validateDrawing(drawingFixture(value.original),value.original);return value;}
beforeEach(()=>{maps.length=0;markers.length=0;lines.length=0;loader.loadAMap.mockReset().mockResolvedValue(sdk);loader.getShanghaiCenter.mockReset().mockResolvedValue([121,31]);});

describe("chat map uses the same formal map owner in an explicit overlay",()=>{
  it("opens only when mounted for an explicit action, with existing points and zero business fetch",async()=>{
    expect(loader.loadAMap).not.toHaveBeenCalled();
    render(<DialogueMap capture={capture()} selection={{key:"open"}} isCurrent={()=>true} onClose={()=>{}}/>);
    await waitFor(()=>expect(markers).toHaveLength(3));
    expect(screen.getByRole("dialog",{name:"当前行程地图"})).toBeTruthy();
    expect(markers.map(marker=>marker.options.content.textContent)).toEqual(["住","1","2"]);
    expect(fetch).not.toHaveBeenCalled();
  });
  it("place→marker, marker→numbered visit and home focus are bidirectional without route requests",async()=>{
    render(<DialogueMap capture={capture()} selection={{key:"places"}} isCurrent={()=>true} onClose={()=>{}}/>);
    await waitFor(()=>expect(markers).toHaveLength(3));await click("定位地点 1");
    expect(maps[0].setZoomAndCenter).toHaveBeenLastCalledWith(16,[required.longitude,required.latitude]);
    const n=maps[0].setZoomAndCenter.mock.calls.length;await click("定位地点 1");expect(maps[0].setZoomAndCenter).toHaveBeenCalledTimes(n+1);
    act(()=>markers[2].handler());expect(document.activeElement?.textContent).toContain("2. 同名地点");
    act(()=>markers[0].handler());expect(document.activeElement?.textContent).toContain("住宿参考点");expect(fetch).not.toHaveBeenCalled();
  });
  it("selected route, switching segments, all-points/route fit and clear reuse actual geometry",async()=>{
    const value=capture();render(<DialogueMap capture={value} selection={{key:"route",edgeId:value.original.edges[0].id}} isCurrent={()=>true} onClose={()=>{}}/>);
    await waitFor(()=>expect(lines).toHaveLength(1));expect(screen.getByLabelText("当前交通路线")).toBeTruthy();
    await click("定位地点 1");await click("查看当天全部地点");await click("查看这段路线全貌");expect(lines).toHaveLength(1);
    const movement=value.original.days[0].items.filter(item=>item.kind==="walk")[1];
    await click(`查看这段路线 ${movement.start_time}`);expect(lines).toHaveLength(2);expect(lines[0].setMap).toHaveBeenCalledWith(null);
    await click("清除路线");expect(lines[1].setMap).toHaveBeenCalledWith(null);expect(fetch).not.toHaveBeenCalled();
  });
  it("cost-driven fresh capture objects do not reset markers or view; collapse preserves geometry",async()=>{
    const value=capture(),props={selection:{key:"same"},isCurrent:()=>true,onClose:()=>{}};
    const view=render(<DialogueMap capture={value} {...props}/>);await waitFor(()=>expect(markers).toHaveLength(3));
    await click("定位地点 2");const fits=maps[0].setFitView.mock.calls.length;
    await click("收起当天地图");await click("查看当天地图");view.rerender(<DialogueMap capture={structuredClone(value)} {...props}/>);
    expect(maps).toHaveLength(1);expect(markers).toHaveLength(3);expect(maps[0].setFitView).toHaveBeenCalledTimes(fits);
  });
  it("date selection clears route, empty dates remove markers, old date-ABA events stay inert",async()=>{
    const value=capture();value.original.days.push({date:"2026-10-11",items:[],return_time:null});
    render(<DialogueMap capture={value} selection={{key:"dates"}} isCurrent={()=>true} onClose={()=>{}}/>);await waitFor(()=>expect(markers).toHaveLength(3));
    const old=markers[1].handler;await click(/第 2 天/);expect(screen.getByText("当天没有已安排游览，不用其他日期地点补齐。")).toBeTruthy();
    markers.forEach(marker=>expect(marker.setMap).toHaveBeenCalledWith(null));
    act(old);expect(document.querySelector('[data-map-selected="true"]')).toBeNull();
    await click(/第 1 天/);act(old);expect(document.querySelector('[data-map-selected="true"]')).toBeNull();expect(fetch).not.toHaveBeenCalled();
  });
  it("late SDK and old callbacks cannot restore an invalid draft; existing map is destroyed",async()=>{
    const late=deferred<AMapSDK>();loader.loadAMap.mockReturnValueOnce(late.promise);let current=true;const value=capture();
    const props={capture:value,selection:{key:"late"},isCurrent:()=>current,onClose:()=>{}};
    const view=render(<DialogueMap {...props}/>);current=false;view.rerender(<DialogueMap {...props}/>);
    await act(async()=>late.resolve(sdk));expect(maps).toHaveLength(0);expect(screen.getByRole("status").textContent).toContain("已失效");
    view.unmount();current=true;
    const next=render(<DialogueMap {...props} selection={{key:"fresh"}}/>);await waitFor(()=>expect(markers).toHaveLength(3));
    const old=markers[1].handler;current=false;next.rerender(<DialogueMap {...props} selection={{key:"fresh"}}/>);act(old);
    expect(maps[0].destroy).toHaveBeenCalledOnce();expect(document.querySelector('[data-map-selected="true"]')).toBeNull();
  });
  it("invalid selection never initializes SDK and map error can be retried without changing the draft",async()=>{
    const value=capture(),before=structuredClone(value);
    const invalid=render(<DialogueMap capture={value} selection={{key:"invalid",date:"2026-11-01"}} isCurrent={()=>true} onClose={()=>{}}/>);
    expect(loader.loadAMap).not.toHaveBeenCalled();expect(screen.getByRole("alert").textContent).toContain("不属于");invalid.unmount();
    loader.loadAMap.mockRejectedValueOnce(new Error("受控加载失败"));
    render(<DialogueMap capture={value} selection={{key:"failure"}} isCurrent={()=>true} onClose={()=>{}}/>);
    await waitFor(()=>expect(screen.getByRole("alert").textContent).toContain("受控加载失败"));await click("重试加载地图");await waitFor(()=>expect(markers).toHaveLength(3));
    expect(value).toEqual(before);expect(fetch).not.toHaveBeenCalled();
  });
  it("Escape closes, focus returns, and Tab remains inside the responsive dialog",async()=>{
    const trigger=document.createElement("button");trigger.textContent="打开地图";document.body.append(trigger);trigger.focus();const close=vi.fn();
    const view=render(<DialogueMap capture={capture()} selection={{key:"keyboard"}} isCurrent={()=>true} onClose={close}/>);
    await waitFor(()=>expect(markers).toHaveLength(3));const dialog=screen.getByRole("dialog"),first=screen.getByRole("button",{name:"关闭行程地图"});
    first.focus();fireEvent.keyDown(first,{key:"Tab",shiftKey:true});expect(dialog.contains(document.activeElement)).toBe(true);expect(document.activeElement).not.toBe(first);
    fireEvent.keyDown(document.activeElement!,{key:"Tab"});expect(document.activeElement).toBe(first);
    fireEvent.keyDown(dialog,{key:"Escape"});expect(close).toHaveBeenCalledOnce();view.unmount();expect(document.activeElement).toBe(trigger);trigger.remove();
  });
});

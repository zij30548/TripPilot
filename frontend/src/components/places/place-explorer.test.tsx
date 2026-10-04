import { StrictMode } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PlaceExplorer from "@/components/places/place-explorer";
import PlaceMap from "@/components/places/place-map";
import type { AMapSDK } from "@/lib/amap-loader";
import type { Place } from "@/types/place";

const sdkLoader = vi.hoisted(() => ({ loadAMap: vi.fn(), getShanghaiCenter: vi.fn() }));
vi.mock("@/lib/amap-loader", () => sdkLoader);

// All names and coordinates in this suite are fictional fixtures.
const places: Place[] = [
  { id: "test-poi-one", name: "测试地点一", address: "测试地址一", category: "测试分类", longitude: 120, latitude: 30, source: "amap" },
  { id: "test-poi-two", name: "测试地点二", address: null, category: null, longitude: 121, latitude: 31, source: "amap" },
];

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => { resolve = resolvePromise; });
  return { promise, resolve };
}

const maps: MockMap[] = [];
const markers: MockMarker[] = [];

class MockMap {
  setFitView = vi.fn();
  setZoomAndCenter = vi.fn();
  destroy = vi.fn();

  constructor(readonly container: HTMLElement, readonly options: Record<string, unknown>) {
    maps.push(this);
  }
}

class MockMarker {
  private handlers = new Map<string, () => void>();
  on = vi.fn((name: string, handler: () => void) => { this.handlers.set(name, handler); });
  off = vi.fn((name: string, handler: () => void) => {
    if (this.handlers.get(name) === handler) this.handlers.delete(name);
  });
  setMap = vi.fn();
  setzIndex = vi.fn();

  constructor(readonly options: { position: [number, number]; title: string; content: HTMLElement; map: MockMap }) {
    markers.push(this);
  }

  click() {
    this.handlers.get("click")?.();
  }
}

const sdk = { Map: MockMap, Marker: MockMarker } as unknown as AMapSDK;
let fetchMock: ReturnType<typeof vi.fn<typeof fetch>>;

beforeEach(() => {
  maps.length = 0;
  markers.length = 0;
  sdkLoader.loadAMap.mockReset().mockResolvedValue(sdk);
  sdkLoader.getShanghaiCenter.mockReset().mockResolvedValue([120, 30]);
  fetchMock = vi.fn<typeof fetch>();
  vi.stubGlobal("fetch", fetchMock);
});

function submitSearch(keyword = "测试地点") {
  fireEvent.change(screen.getByRole("textbox", { name: "搜索上海地点" }), { target: { value: keyword } });
  fireEvent.click(screen.getByRole("button", { name: "搜索" }));
}

describe("PlaceExplorer", () => {
  it("searches only on explicit submit and displays results with safe optional-field fallbacks", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify(places)));
    render(<PlaceExplorer />);
    await waitFor(() => expect(maps).toHaveLength(1));

    const input = screen.getByRole("textbox", { name: "搜索上海地点" });
    for (const keyword of ["测", "测试", "测试地点"]) {
      fireEvent.change(input, { target: { value: keyword } });
    }
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByText("尚未搜索地点。")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "搜索" }));
    expect(await screen.findByRole("button", { name: /测试地点一/ })).toBeTruthy();
    expect(screen.getByText("地址：暂无地址")).toBeTruthy();
    expect(screen.getByText("类别：暂无类别")).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(markers).toHaveLength(2);
    expect(markers.map((marker) => marker.options.position)).toEqual([[120, 30], [121, 31]]);
    expect(markers.map((marker) => marker.options.content.textContent)).toEqual(["1", "2"]);
    expect(screen.getByRole("button", { name: /测试地点一/ }).getAttribute("aria-pressed")).toBe("false");
    expect(maps[0].setZoomAndCenter).not.toHaveBeenCalled();
    expect(maps[0].options).toMatchObject({ center: [120, 30], viewMode: "2D" });
  });

  it("shows an empty-result message without markers or an enabled fit-all action", async () => {
    fetchMock.mockResolvedValue(new Response("[]"));
    render(<PlaceExplorer />);
    submitSearch();

    expect(await screen.findByText("没有找到匹配地点，请更换关键词。")).toBeTruthy();
    expect(screen.queryByRole("list", { name: "地点搜索结果" })).toBeNull();
    expect(markers).toHaveLength(0);
    expect((screen.getByRole("button", { name: /查看全部/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows a search error, clears previous markers and allows another search", async () => {
    fetchMock
      .mockResolvedValueOnce(new Response(JSON.stringify(places)))
      .mockResolvedValueOnce(new Response("private upstream detail", { status: 503 }))
      .mockResolvedValueOnce(new Response("[]"));
    render(<PlaceExplorer />);
    submitSearch();
    await screen.findByRole("button", { name: /测试地点一/ });
    await waitFor(() => expect(markers).toHaveLength(2));
    const oldMarkers = [...markers];

    submitSearch("第二次");
    expect(await screen.findByText(/地点搜索服务尚未配置/)).toBeTruthy();
    expect(screen.queryByText("private upstream detail")).toBeNull();
    expect(screen.queryByRole("list", { name: "地点搜索结果" })).toBeNull();
    for (const marker of oldMarkers) {
      expect(marker.setMap).toHaveBeenCalledWith(null);
      expect(marker.off).toHaveBeenCalledWith("click", expect.any(Function));
    }

    submitSearch("第三次");
    expect(await screen.findByText("没有找到匹配地点，请更换关键词。")).toBeTruthy();
  });

  it("keeps list search usable when SDK loading fails", async () => {
    sdkLoader.loadAMap.mockRejectedValue(new Error("地图测试加载失败"));
    fetchMock.mockResolvedValue(new Response(JSON.stringify(places)));
    render(<PlaceExplorer />);
    expect(await screen.findByText("地图测试加载失败")).toBeTruthy();

    submitSearch();
    expect(await screen.findByRole("button", { name: /测试地点一/ })).toBeTruthy();
    expect(maps).toHaveLength(0);
    expect(markers).toHaveLength(0);
  });

  it("keeps the latest search when an older response arrives last", async () => {
    const older = deferred<Response>();
    const newer = deferred<Response>();
    fetchMock.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);
    render(<PlaceExplorer />);

    submitSearch("旧关键词");
    const firstSignal = fetchMock.mock.calls[0][1]?.signal;
    expect(screen.getByText("正在搜索上海地点…")).toBeTruthy();
    submitSearch("新关键词");
    expect(firstSignal?.aborted).toBe(true);

    await act(async () => { newer.resolve(new Response(JSON.stringify([places[1]]))); });
    expect(await screen.findByRole("button", { name: /测试地点二/ })).toBeTruthy();
    await act(async () => { older.resolve(new Response(JSON.stringify([places[0]]))); });
    expect(screen.queryByRole("button", { name: /测试地点一/ })).toBeNull();
    expect(screen.getByRole("button", { name: /测试地点二/ })).toBeTruthy();
    expect(screen.queryByText("正在搜索上海地点…")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("links card and marker selection, scrolls the selected card and fits all results", async () => {
    const scrollIntoView = vi.spyOn(HTMLElement.prototype, "scrollIntoView").mockImplementation(() => undefined);
    fetchMock.mockResolvedValue(new Response(JSON.stringify(places)));
    render(<PlaceExplorer />);
    submitSearch();
    const first = await screen.findByRole("button", { name: /测试地点一/ });
    const second = screen.getByRole("button", { name: /测试地点二/ });
    await waitFor(() => expect(markers).toHaveLength(2));

    fireEvent.click(first);
    expect(first.getAttribute("aria-pressed")).toBe("true");
    expect(second.getAttribute("aria-pressed")).toBe("false");
    expect(maps[0].setZoomAndCenter).toHaveBeenLastCalledWith(16, [120, 30]);
    expect(markers[0].setzIndex).toHaveBeenLastCalledWith(200);

    act(() => { markers[1].click(); });
    expect(first.getAttribute("aria-pressed")).toBe("false");
    expect(second.getAttribute("aria-pressed")).toBe("true");
    expect(maps[0].setZoomAndCenter).toHaveBeenLastCalledWith(16, [121, 31]);
    expect(markers[1].setzIndex).toHaveBeenLastCalledWith(200);
    expect(scrollIntoView).toHaveBeenCalled();
    expect(scrollIntoView.mock.contexts.at(-1)).toBe(second);

    maps[0].setFitView.mockClear();
    fireEvent.click(screen.getByRole("button", { name: /查看全部/ }));
    expect(maps[0].setFitView).toHaveBeenCalledTimes(1);
    expect(maps[0].setFitView.mock.calls[0][0]).toEqual(markers);
    maps[0].setZoomAndCenter.mockClear();
    fireEvent.click(second);
    expect(maps[0].setZoomAndCenter).toHaveBeenCalledWith(16, [121, 31]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("aborts search and destroys map objects when the explorer unmounts", async () => {
    const pending = deferred<Response>();
    fetchMock.mockReturnValue(pending.promise);
    const view = render(<PlaceExplorer />);
    await waitFor(() => expect(maps).toHaveLength(1));
    submitSearch();
    const signal = fetchMock.mock.calls[0][1]?.signal;

    view.unmount();
    expect(signal?.aborted).toBe(true);
    expect(maps[0].destroy).toHaveBeenCalledTimes(1);
    await act(async () => { pending.resolve(new Response(JSON.stringify(places))); });
    expect(markers).toHaveLength(0);
  });
});

describe("PlaceMap lifecycle and coordinate safety", () => {
  it("skips invalid coordinates and only fits valid markers", async () => {
    const invalid = [
      { ...places[0], id: "nan", longitude: Number.NaN },
      { ...places[0], id: "infinite", latitude: Infinity },
      { ...places[0], id: "out-of-range", longitude: 181 },
    ];
    render(<PlaceMap places={[...invalid, places[1]]} selectedPlaceId={null} onSelect={vi.fn()} />);
    await waitFor(() => expect(markers).toHaveLength(1));
    expect(markers[0].options.position).toEqual([121, 31]);
    expect(screen.getByText("3 个地点坐标无效，未在地图上显示。")).toBeTruthy();
    expect(maps[0].setFitView.mock.calls[0][0]).toEqual([markers[0]]);
  });

  it("waits for Shanghai center before constructing the map", async () => {
    const center = deferred<[number, number]>();
    sdkLoader.getShanghaiCenter.mockReturnValue(center.promise);
    render(<PlaceMap places={places} selectedPlaceId={null} onSelect={vi.fn()} />);
    await waitFor(() => expect(sdkLoader.getShanghaiCenter).toHaveBeenCalledTimes(1));
    expect(maps).toHaveLength(0);

    await act(async () => { center.resolve([120, 30]); });
    expect(maps).toHaveLength(1);
    expect(maps[0].options.center).toEqual([120, 30]);
  });

  it("does not construct a map after SDK loading finishes on an unmounted component", async () => {
    const pending = deferred<AMapSDK>();
    sdkLoader.loadAMap.mockReturnValue(pending.promise);
    const view = render(<PlaceMap places={places} selectedPlaceId={null} onSelect={vi.fn()} />);
    view.unmount();
    await act(async () => { pending.resolve(sdk); });
    expect(sdkLoader.getShanghaiCenter).not.toHaveBeenCalled();
    expect(maps).toHaveLength(0);
    expect(markers).toHaveLength(0);
  });

  it("cancels district lookup and ignores its late result after unmount", async () => {
    const center = deferred<[number, number]>();
    sdkLoader.getShanghaiCenter.mockReturnValue(center.promise);
    const view = render(<PlaceMap places={places} selectedPlaceId={null} onSelect={vi.fn()} />);
    await waitFor(() => expect(sdkLoader.getShanghaiCenter).toHaveBeenCalledTimes(1));
    const signal = sdkLoader.getShanghaiCenter.mock.calls[0][1] as AbortSignal;
    view.unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => { center.resolve([120, 30]); });
    expect(maps).toHaveLength(0);
  });

  it("constructs one live map in StrictMode and removes listeners and markers on unmount", async () => {
    const pending = deferred<AMapSDK>();
    sdkLoader.loadAMap.mockReturnValue(pending.promise);
    const onSelect = vi.fn();
    const view = render(<StrictMode><PlaceMap places={places} selectedPlaceId={null} onSelect={onSelect} /></StrictMode>);
    await act(async () => { pending.resolve(sdk); });
    await waitFor(() => expect(markers).toHaveLength(2));
    expect(maps).toHaveLength(1);
    expect(sdkLoader.getShanghaiCenter).toHaveBeenCalledTimes(1);

    view.unmount();
    for (const marker of markers) {
      expect(marker.off).toHaveBeenCalledWith("click", expect.any(Function));
      expect(marker.setMap).toHaveBeenCalledWith(null);
      marker.click();
    }
    expect(maps[0].destroy).toHaveBeenCalledTimes(1);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("shows a timeout and ignores SDK completion after the deadline", async () => {
    vi.useFakeTimers();
    const pending = deferred<AMapSDK>();
    sdkLoader.loadAMap.mockReturnValue(pending.promise);
    render(<PlaceMap places={places} selectedPlaceId={null} onSelect={vi.fn()} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    expect(screen.getByText(/地图加载超时/)).toBeTruthy();
    await act(async () => { pending.resolve(sdk); });
    expect(maps).toHaveLength(0);
    expect(sdkLoader.getShanghaiCenter).not.toHaveBeenCalled();
  });
});

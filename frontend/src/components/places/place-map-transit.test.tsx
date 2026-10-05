import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PlaceMap from "@/components/places/place-map";
import PlaceExplorer from "@/components/places/place-explorer";
import type { AMapSDK } from "@/lib/amap-loader";
import type { Place } from "@/types/place";
import type { TransitRouteResponse } from "@/types/transit";
import type { WalkingRouteResponse } from "@/types/route";

const loader = vi.hoisted(() => ({ loadAMap: vi.fn(), getShanghaiCenter: vi.fn() }));
vi.mock("@/lib/amap-loader", () => loader);
const places: Place[] = [
  { id: "fixture-a", name: "测试甲", address: null, category: null, longitude: 120, latitude: 30, source: "amap" },
  { id: "fixture-b", name: "测试乙", address: null, category: null, longitude: 120.02, latitude: 30.02, source: "amap" },
];
const route: TransitRouteResponse = {
  status: "ok", source: "amap", queried_at: "2026-10-05T01:00:00Z", selection_rule: "first_supported_complete",
  route: { duration_seconds: 901, walking_distance_meters: 320, fare_cny: null, geometry_complete: true, legs: [
    { mode: "walking", distance_meters: 320, duration_seconds: 240, instruction: "测试步行", line_name: null, departure_stop: null, arrival_stop: null,
      geometry: [[[120, 30], [120.001, 30.001]], [[120.002, 30.002], [120.003, 30.003]]], geometry_complete: true },
    { mode: "bus", distance_meters: null, duration_seconds: null, instruction: null, line_name: "测试公交", departure_stop: "甲站", arrival_stop: "乙站",
      geometry: [[[120.004, 30.004], [120.009, 30.009]]], geometry_complete: true },
    { mode: "subway", distance_meters: 2000, duration_seconds: 400, instruction: null, line_name: "测试地铁", departure_stop: "乙站", arrival_stop: "丙站",
      geometry: [[[120.01, 30.01], [120.02, 30.02]]], geometry_complete: true },
  ] },
};
const walking: WalkingRouteResponse = {
  status: "ok", source: "amap", queried_at: route.queried_at,
  route: { distance_meters: 2500, duration_seconds: 1900, segments: [[[120, 30], [120.02, 30.02]]] },
};
const maps: MockMap[] = [], markers: MockMarker[] = [], lines: MockPolyline[] = [];
class MockMap {
  setFitView = vi.fn(); setZoomAndCenter = vi.fn(); destroy = vi.fn();
  constructor() { maps.push(this); }
}
class MockMarker {
  handler?: () => void;
  on = vi.fn((_event: string, handler: () => void) => { this.handler = handler; });
  off = vi.fn(() => { this.handler = undefined; }); setMap = vi.fn(); setzIndex = vi.fn();
  constructor(readonly options: { content: HTMLElement }) { markers.push(this); }
}
class MockPolyline {
  currentMap: MockMap | null = null;
  setMap = vi.fn((map: MockMap | null) => { this.currentMap = map; });
  constructor(readonly options: { path: number[][][]; strokeColor: string; strokeStyle: string }) { lines.push(this); }
}
const sdk = { Map: MockMap, Marker: MockMarker, Polyline: MockPolyline } as unknown as AMapSDK;
const live = () => lines.filter((line) => line.currentMap);
beforeEach(() => {
  maps.length = 0; markers.length = 0; lines.length = 0;
  loader.loadAMap.mockReset().mockResolvedValue(sdk);
  loader.getShanghaiCenter.mockReset().mockResolvedValue([120, 30]);
});

describe("transit map segments and walking regression", () => {
  it("draws ordered, distinct leg styles without bridging missing edges, fits all and retains marker linkage", async () => {
    const onSelect = vi.fn();
    const props = { places, selectedPlaceId: null as string | null, onSelect, transitRoute: route };
    const view = render(<PlaceMap {...props} />);
    await waitFor(() => expect(live()).toHaveLength(3));
    expect(live().map((line) => line.options.path)).toEqual(route.route.legs.map((leg) => leg.geometry));
    expect(live().map((line) => [line.options.strokeColor, line.options.strokeStyle])).toEqual([
      ["#2563eb", "dashed"], ["#7c3aed", "solid"], ["#db2777", "solid"],
    ]);
    expect(screen.getByText(/蓝色虚线：步行接驳/)).toBeTruthy();
    expect(screen.getByText(/紫色实线：公交/)).toBeTruthy();
    expect(screen.getByText(/粉色实线：地铁/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "查看公交方案全貌" }));
    expect(maps[0].setFitView).toHaveBeenLastCalledWith(live(), false, [48, 48, 48, 48], 17);
    act(() => markers[1].handler?.());
    expect(onSelect).toHaveBeenCalledWith(places[1].id);
    view.rerender(<PlaceMap {...props} selectedPlaceId={places[1].id} selectionVersion={1} />);
    expect(maps[0].setZoomAndCenter).toHaveBeenLastCalledWith(16, [120.02, 30.02]);
    expect(markers[1].setzIndex).toHaveBeenLastCalledWith(200);
    expect(lines).toHaveLength(3);
    expect(maps).toHaveLength(1);
  });

  it("labels incomplete geometry, draws only known parts and never invents missing ride coordinates", async () => {
    const partial: TransitRouteResponse = { ...route, route: { ...route.route, geometry_complete: false,
      legs: route.route.legs.map((leg, index) => index === 1 ? { ...leg, geometry: [], geometry_complete: false } : leg),
    } };
    const props = { places, selectedPlaceId: null, onSelect: vi.fn() };
    const view = render(<PlaceMap {...props} transitRoute={partial} />);
    await waitFor(() => expect(live()).toHaveLength(2));
    expect(screen.getByText(/地图不完整/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "查看公交方案全貌" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "查看已知路段" }));
    expect(maps[0].setFitView).toHaveBeenLastCalledWith(live(), false, [48, 48, 48, 48], 17);
    view.rerender(<PlaceMap {...props} transitRoute={{ ...partial, route: { ...partial.route,
      legs: partial.route.legs.map((leg) => ({ ...leg, geometry: [], geometry_complete: false })),
    } }} />);
    expect(live()).toHaveLength(0);
    expect((screen.getByRole("button", { name: "查看已知路段" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("removes every old leg on mode/route clear and unmount without rebuilding markers/map", async () => {
    const props = { places, selectedPlaceId: null, onSelect: vi.fn() };
    const view = render(<PlaceMap {...props} transitRoute={route} />);
    await waitFor(() => expect(live()).toHaveLength(3));
    const old = [...live()];
    view.rerender(<PlaceMap {...props} walkingRoute={walking} />);
    expect(old.every((line) => line.currentMap === null)).toBe(true);
    expect(live()).toHaveLength(1);
    expect(live()[0].options.path).toEqual(walking.route.segments);
    expect(screen.queryByText(/粉色实线/)).toBeNull();
    view.rerender(<PlaceMap {...props} transitRoute={route} walkingRoute={walking} />);
    expect(live()).toHaveLength(3); // Defensive: never overlay both modes even if both props are given.
    view.rerender(<PlaceMap {...props} />);
    expect(live()).toHaveLength(0);
    expect(maps).toHaveLength(1); expect(markers).toHaveLength(2);
    view.rerender(<PlaceMap {...props} transitRoute={route} />);
    view.unmount();
    expect(live()).toHaveLength(0);
    expect(maps[0].destroy).toHaveBeenCalledTimes(1);
  });

  it("rejects malformed coordinates instead of drawing a valid-looking subset", async () => {
    const malformed: TransitRouteResponse = { ...route, route: { ...route.route,
      legs: route.route.legs.map((leg, index) => index === 1 ? { ...leg, geometry: [[[120, 30], [Number.NaN, 31]]] } : leg),
    } };
    render(<PlaceMap places={places} selectedPlaceId={null} onSelect={vi.fn()} transitRoute={malformed} />);
    await waitFor(() => expect(maps).toHaveLength(1));
    expect(screen.getByText("公交方案坐标无效，未绘制路线。")).toBeTruthy();
    expect(live()).toHaveLength(0);
  });

  it("does not leak bound-route geometry into search candidates", async () => {
    const props = { transitRoute: route, onViewChange: vi.fn(), itinerary: { places, activityCount: 2, selectedPlaceId: null, selectionVersion: 0, onSelect: vi.fn() } };
    const view = render(<PlaceExplorer {...props} view="itinerary" />);
    await waitFor(() => expect(live()).toHaveLength(3));
    view.rerender(<PlaceExplorer {...props} view="search" />);
    expect(live()).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "查看公交方案全貌" })).toBeNull();
    view.rerender(<PlaceExplorer {...props} transitRoute={null} view="itinerary" />);
    expect(live()).toHaveLength(0);
  });

  it("cannot restore cleared transit geometry when SDK initialization resolves late", async () => {
    let resolve!: (sdk: AMapSDK) => void;
    loader.loadAMap.mockReturnValue(new Promise<AMapSDK>((done) => { resolve = done; }));
    const props = { places, selectedPlaceId: null, onSelect: vi.fn() };
    const view = render(<PlaceMap {...props} transitRoute={route} />);
    view.rerender(<PlaceMap {...props} transitRoute={null} />);
    await act(async () => resolve(sdk));
    await waitFor(() => expect(maps).toHaveLength(1));
    expect(lines).toHaveLength(0);
  });
});

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PlaceMap from "@/components/places/place-map";
import PlaceExplorer from "@/components/places/place-explorer";
import type { AMapSDK } from "@/lib/amap-loader";
import type { Place } from "@/types/place";
import type { WalkingRouteResponse } from "@/types/route";

const loader = vi.hoisted(() => ({ loadAMap: vi.fn(), getShanghaiCenter: vi.fn() }));
vi.mock("@/lib/amap-loader", () => loader);

// Fictional fixtures only; no real SDK, routing request or POI data.
const places: Place[] = [
  { id: "fixture-a", name: "测试甲", address: null, category: null, longitude: 120, latitude: 30, source: "amap" },
  { id: "fixture-b", name: "测试乙", address: null, category: null, longitude: 120.01, latitude: 30.01, source: "amap" },
];
const route: WalkingRouteResponse = {
  status: "ok", source: "amap", queried_at: "2026-10-05T01:00:00Z",
  route: {
    distance_meters: 1250, duration_seconds: 960,
    // Disconnected steps deliberately verify that no connecting edge is invented.
    segments: [[[120, 30], [120.002, 30.002]], [[120.008, 30.008], [120.01, 30.01]]],
  },
};
const maps: MockMap[] = [];
const markers: MockMarker[] = [];
const lines: MockPolyline[] = [];

class MockMap {
  setFitView = vi.fn();
  setZoomAndCenter = vi.fn();
  destroy = vi.fn();
  constructor() { maps.push(this); }
}
class MockMarker {
  handler?: () => void;
  on = vi.fn((_event: string, handler: () => void) => { this.handler = handler; });
  off = vi.fn(() => { this.handler = undefined; });
  setMap = vi.fn();
  setzIndex = vi.fn();
  constructor(readonly options: { content: HTMLElement }) { markers.push(this); }
}
class MockPolyline {
  setMap = vi.fn();
  constructor(readonly options: { path: number[][][] }) { lines.push(this); }
}
const sdk = { Map: MockMap, Marker: MockMarker, Polyline: MockPolyline } as unknown as AMapSDK;

beforeEach(() => {
  maps.length = 0;
  markers.length = 0;
  lines.length = 0;
  loader.loadAMap.mockReset().mockResolvedValue(sdk);
  loader.getShanghaiCenter.mockReset().mockResolvedValue([120, 30]);
});

describe("real walking route map overlays", () => {
  it("uses only upstream coordinate segments, fits the whole route and keeps marker selection working", async () => {
    const select = vi.fn();
    const props = { places, selectedPlaceId: null as string | null, onSelect: select, walkingRoute: route };
    const view = render(<PlaceMap {...props} />);
    await waitFor(() => expect(lines).toHaveLength(1));
    expect(lines[0].options.path).toEqual(route.route.segments);
    expect(lines[0].setMap).toHaveBeenCalledWith(maps[0]);
    expect(maps[0].setFitView).toHaveBeenLastCalledWith([lines[0]], false, [48, 48, 48, 48], 17);

    act(() => markers[1].handler?.());
    expect(select).toHaveBeenCalledWith(places[1].id);
    view.rerender(<PlaceMap {...props} selectedPlaceId={places[1].id} selectionVersion={1} />);
    expect(maps[0].setZoomAndCenter).toHaveBeenLastCalledWith(16, [120.01, 30.01]);
    expect(markers[1].setzIndex).toHaveBeenLastCalledWith(200);
    expect(lines).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "查看完整步行路线" }));
    expect(maps[0].setFitView).toHaveBeenLastCalledWith([lines[0]], false, [48, 48, 48, 48], 17);
    maps[0].setFitView.mockClear();
    view.rerender(<PlaceMap {...props} selectedPlaceId={places[1].id} routeSelectionVersion={2} />);
    expect(maps[0].setFitView).toHaveBeenCalledWith([lines[0]], false, [48, 48, 48, 48], 17);
    expect(maps).toHaveLength(1);
    expect(lines).toHaveLength(1);
  });

  it("removes the previous polyline on route replacement, invalidation and unmount without rebuilding the map", async () => {
    const props = { places, selectedPlaceId: null, onSelect: vi.fn() };
    const view = render(<PlaceMap {...props} walkingRoute={route} />);
    await waitFor(() => expect(lines).toHaveLength(1));
    const next: WalkingRouteResponse = { ...route, route: { ...route.route, segments: [[[120, 30], [120.02, 30.02]]] } };
    view.rerender(<PlaceMap {...props} walkingRoute={next} />);
    expect(lines[0].setMap).toHaveBeenCalledWith(null);
    expect(lines).toHaveLength(2);
    expect(lines[1].options.path).toEqual(next.route.segments);
    view.rerender(<PlaceMap {...props} walkingRoute={null} />);
    expect(lines[1].setMap).toHaveBeenCalledWith(null);
    expect(screen.queryByRole("button", { name: "查看完整步行路线" })).toBeNull();
    expect(maps).toHaveLength(1);
    expect(markers).toHaveLength(2);
    view.rerender(<PlaceMap {...props} walkingRoute={route} />);
    expect(lines).toHaveLength(3);
    view.unmount();
    expect(lines[2].setMap).toHaveBeenCalledWith(null);
    expect(maps[0].destroy).toHaveBeenCalledTimes(1);
  });

  it("does not draw missing, no-route or malformed coordinates", async () => {
    const props = { places, selectedPlaceId: null, onSelect: vi.fn() };
    const view = render(<PlaceMap {...props} walkingRoute={{ status: "no_route", source: "amap", queried_at: route.queried_at, route: null }} />);
    await waitFor(() => expect(maps).toHaveLength(1));
    expect(lines).toHaveLength(0);
    view.rerender(<PlaceMap {...props} walkingRoute={{ ...route, route: { ...route.route, segments: [[[120, 30], [Number.NaN, 31]]] } }} />);
    expect(screen.getByText("步行路线坐标无效，未绘制路线。")).toBeTruthy();
    expect(lines).toHaveLength(0);
    expect((screen.getByRole("button", { name: "查看完整步行路线" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("removes the route in search-candidate view and restores only the current route in daily-bound view", async () => {
    const props = {
      walkingRoute: route,
      itinerary: { places, activityCount: 2, selectedPlaceId: null, selectionVersion: 0, onSelect: vi.fn() },
      onViewChange: vi.fn(),
    };
    const view = render(<PlaceExplorer {...props} view="itinerary" />);
    await waitFor(() => expect(lines).toHaveLength(1));
    view.rerender(<PlaceExplorer {...props} view="search" />);
    expect(lines[0].setMap).toHaveBeenCalledWith(null);
    expect(screen.queryByRole("button", { name: "查看完整步行路线" })).toBeNull();
    expect(screen.getByRole("textbox", { name: "搜索上海地点" })).toBeTruthy();
    view.rerender(<PlaceExplorer {...props} walkingRoute={null} view="itinerary" />);
    expect(lines).toHaveLength(1);
    expect(maps).toHaveLength(1);
  });

  it("cannot draw an old route after it is cleared while the SDK is still loading", async () => {
    let resolve!: (value: AMapSDK) => void;
    loader.loadAMap.mockReturnValue(new Promise<AMapSDK>((finish) => { resolve = finish; }));
    const props = { places, selectedPlaceId: null, onSelect: vi.fn() };
    const view = render(<PlaceMap {...props} walkingRoute={route} />);
    view.rerender(<PlaceMap {...props} walkingRoute={null} />);
    await act(async () => resolve(sdk));
    await waitFor(() => expect(maps).toHaveLength(1));
    expect(lines).toHaveLength(0);
    expect(markers).toHaveLength(2);
  });
});

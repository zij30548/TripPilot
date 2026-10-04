"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { Place } from "@/types/place";
import type { WalkingRouteResponse } from "@/types/route";
import { queryWalkingRoute, WalkingRouteError } from "@/lib/routes-api";

export type WalkingRouteSelection = {
  key: string;
  fromActivityKey: string;
  toActivityKey: string;
  origin: Place;
  destination: Place;
};

export type WalkingRouteState =
  | { status: "idle" }
  | { status: "loading"; selection: WalkingRouteSelection }
  | { status: "success" | "no_route" | "same_place"; selection: WalkingRouteSelection; response: WalkingRouteResponse }
  | { status: "timeout" | "error"; selection: WalkingRouteSelection; message: string };

export function walkingSegmentKey(fromActivityKey: string, toActivityKey: string): string {
  return JSON.stringify([fromActivityKey, toActivityKey]);
}

export function isSameWalkingPlace(origin: Place, destination: Place): boolean {
  return origin.id === destination.id ||
    (origin.longitude === destination.longitude && origin.latitude === destination.latitude);
}

export function useWalkingRoute() {
  const [state, setState] = useState<WalkingRouteState>({ status: "idle" });
  const version = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const selection = useRef<WalkingRouteSelection | null>(null);

  const cancelPending = useCallback(() => {
    // Monotonic generation, not endpoint equality: A → B → A cannot revive A's old response.
    version.current += 1;
    controller.current?.abort();
    controller.current = null;
  }, []);

  const invalidate = useCallback(() => {
    cancelPending();
    selection.current = null;
    setState({ status: "idle" });
  }, [cancelPending]);

  const invalidateActivity = useCallback((activityKey: string) => {
    if (selection.current?.fromActivityKey === activityKey || selection.current?.toActivityKey === activityKey) invalidate();
  }, [invalidate]);

  useEffect(() => cancelPending, [cancelPending]);

  const query = useCallback(async (next: WalkingRouteSelection): Promise<boolean> => {
    cancelPending();
    const requestVersion = version.current;
    const requestController = new AbortController();
    controller.current = requestController;
    selection.current = next;
    if (isSameWalkingPlace(next.origin, next.destination)) {
      controller.current = null;
      setState({ status: "idle" });
      return false;
    }
    setState({ status: "loading", selection: next });
    const endpoint = (place: Place) => ({ place_id: place.id, longitude: place.longitude, latitude: place.latitude });
    try {
      const response = await queryWalkingRoute({ origin: endpoint(next.origin), destination: endpoint(next.destination) }, requestController.signal);
      if (version.current !== requestVersion || requestController.signal.aborted) return false;
      setState({ status: response.status === "ok" ? "success" : response.status, selection: next, response });
      return response.status === "ok";
    } catch (error) {
      if (version.current !== requestVersion || requestController.signal.aborted) return false;
      setState({ status: error instanceof WalkingRouteError ? error.kind : "error", selection: next,
        message: error instanceof WalkingRouteError ? error.message : "步行路线查询失败，请稍后重试。" });
      return false;
    } finally {
      if (version.current === requestVersion) controller.current = null;
    }
  }, [cancelPending]);

  return { state, query, invalidate, invalidateActivity };
}

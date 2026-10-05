"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { Place } from "@/types/place";
import type { TransitRouteResponse } from "@/types/transit";
import { queryTransitRoute, TransitRouteError } from "@/lib/transit-api";
import { isSameWalkingPlace, type WalkingRouteSelection } from "@/lib/use-walking-route";

export type TransitRouteState =
  | { status: "idle" }
  | { status: "loading"; selection: WalkingRouteSelection }
  | { status: "success" | "no_route" | "unsupported" | "same_place"; selection: WalkingRouteSelection; response: TransitRouteResponse }
  | { status: "timeout" | "error"; selection: WalkingRouteSelection; message: string };

export function useTransitRoute() {
  const [state, setState] = useState<TransitRouteState>({ status: "idle" });
  const version = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const selection = useRef<WalkingRouteSelection | null>(null);
  const cancelPending = useCallback(() => {
    // The parent invalidates both modes before every mode switch. A monotonic
    // generation also isolates endpoint A → B → A and transports ignoring abort.
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
      const response = await queryTransitRoute({ origin: endpoint(next.origin), destination: endpoint(next.destination) }, requestController.signal);
      if (version.current !== requestVersion || requestController.signal.aborted) return false;
      setState({ status: response.status === "ok" ? "success" : response.status, selection: next, response });
      return response.status === "ok";
    } catch (error) {
      if (version.current !== requestVersion || requestController.signal.aborted) return false;
      setState({ status: error instanceof TransitRouteError ? error.kind : "error", selection: next,
        message: error instanceof TransitRouteError ? error.message : "公交／地铁查询失败，请稍后重试。" });
      return false;
    } finally {
      if (version.current === requestVersion) controller.current = null;
    }
  }, [cancelPending]);
  return { state, query, invalidate, invalidateActivity };
}

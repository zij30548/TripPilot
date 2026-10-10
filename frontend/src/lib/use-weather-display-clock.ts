"use client";

import { useEffect, useState } from "react";
import { getShanghaiDate, type WeatherForecastResponse } from "@/types/weather";

// Shared local display mechanism. There is deliberately no network operation.
export function useWeatherDisplayClock(response: WeatherForecastResponse | null, onTick?: (now: number) => void) {
  const [clock, setClock] = useState(() => ({ response, now: Date.now() }));
  if (clock.response !== response) setClock(() => ({ response, now: Date.now() }));
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    function schedule() {
      const current = Date.now();
      const midnight = Date.parse(`${getShanghaiDate(current)}T00:00:00+08:00`) + 86_400_000;
      const staleBoundary = response?.reported_at ? Date.parse(response.reported_at) + 86_400_000 + 1 : Infinity;
      const boundary = Math.min(midnight, staleBoundary > current ? staleBoundary : Infinity);
      timer = setTimeout(update, Math.max(1, Math.min(60_000, boundary - current)));
    }
    function update() {
      clearTimeout(timer);
      const now = Date.now();
      onTick?.(now);
      setClock({ response, now });
      schedule();
    }
    function onVisibility() { if (document.visibilityState === "visible") update(); }
    schedule();
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("focus", update);
    return () => { clearTimeout(timer); document.removeEventListener("visibilitychange", onVisibility); window.removeEventListener("focus", update); };
  }, [response, onTick]);
  return clock.now;
}

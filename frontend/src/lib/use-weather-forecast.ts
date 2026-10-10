"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { WeatherError, queryWeatherForecast } from "@/lib/weather-api";
import { isWeatherForecastRequest, type WeatherForecastRequest, type WeatherForecastResponse } from "@/types/weather";

export type WeatherForecastState = {
  status: "idle" | "loading" | "success" | "failed";
  response: WeatherForecastResponse | null;
  message: string | null;
  showingPrevious: boolean;
};
const emptyState = (): WeatherForecastState => ({ status: "idle", response: null, message: null, showingPrevious: false });

export function useWeatherForecast(request: WeatherForecastRequest, onInvalidate?: () => void) {
  const { start_date, end_date } = request;
  const valid = isWeatherForecastRequest(request);
  const requestKey = JSON.stringify([start_date, end_date, valid]);
  const [inputKey, setInputKey] = useState(requestKey);
  const [state, setState] = useState<WeatherForecastState>(emptyState);
  const input = useRef<WeatherForecastRequest | null>(valid ? { start_date, end_date } : null);
  const version = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const pending = useRef(false);
  const lastValid = useRef<WeatherForecastResponse | null>(null);

  const cancelPending = useCallback(() => {
    onInvalidate?.();
    version.current++;
    controller.current?.abort();
    controller.current = null;
    pending.current = false;
  }, [onInvalidate]);
  const invalidate = useCallback(() => {
    cancelPending();
    lastValid.current = null;
    setState(emptyState());
  }, [cancelPending]);
  // Clear rendered state immediately. The layout commit below synchronously
  // invalidates asynchronous work before paint/events, not in a passive effect.
  if (inputKey !== requestKey) {
    setInputKey(requestKey);
    setState(emptyState());
  }
  useLayoutEffect(() => {
    cancelPending();
    lastValid.current = null;
    input.current = valid ? { start_date, end_date } : null;
  }, [start_date, end_date, valid, cancelPending]);
  useEffect(() => cancelPending, [cancelPending]);

  const query = useCallback(async (): Promise<boolean> => {
    if (pending.current) return false;
    if (!isWeatherForecastRequest(input.current)) {
      setState({ status: "failed", response: null, message: "请确认旅行日期为连续的 1～3 天后再查询天气。", showingPrevious: false });
      return false;
    }
    const snapshot = { ...input.current };
    cancelPending();
    const requestVersion = version.current;
    const requestController = new AbortController();
    controller.current = requestController;
    pending.current = true;
    setState({ status: "loading", response: lastValid.current, message: null, showingPrevious: lastValid.current !== null });
    try {
      const response = await queryWeatherForecast(snapshot, requestController.signal);
      if (requestVersion !== version.current || requestController.signal.aborted) return false;
      lastValid.current = response;
      setState({ status: "success", response, message: null, showingPrevious: false });
      return true;
    } catch (error) {
      if (requestVersion !== version.current || requestController.signal.aborted) return false;
      setState({ status: "failed", response: lastValid.current, showingPrevious: lastValid.current !== null,
        message: `${error instanceof WeatherError ? error.message : "查询天气失败，请主动重试。"}${lastValid.current ? " 当前展示上次成功查询的整批结果。" : ""}` });
      return false;
    } finally {
      if (requestVersion === version.current) { controller.current = null; pending.current = false; }
    }
  }, [cancelPending]);
  return { state, query, invalidate, canQuery: valid };
}

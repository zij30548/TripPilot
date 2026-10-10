"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { useCandidatePool } from "@/lib/use-candidate-pool";
import type { useSchedulePreview } from "@/lib/use-schedule-preview";
import type { useWeatherForecast } from "@/lib/use-weather-forecast";
import { useWeatherDisplayClock } from "@/lib/use-weather-display-clock";
import { checkWeatherSchedule, scheduledVisits, weatherCheckTimeKey, type VisitExposure, type WeatherScheduleReport } from "@/lib/weather-schedule-check";

// Result-local action revision, shared ONLY by inputs to this check. Each input
// hook calls invalidate in the originating event, before React renders (ABA safe).
export function useWeatherCheckRevision() {
  const counter = useRef(0);
  const [version, setVersion] = useState(0);
  const listeners = useRef(new Set<() => void>());
  const invalidate = useCallback(() => { counter.current++; listeners.current.forEach((listener) => listener()); setVersion(counter.current); }, []);
  const subscribe = useCallback((listener: () => void) => { listeners.current.add(listener); return () => { listeners.current.delete(listener); }; }, []);
  const read = useCallback(() => counter.current, []);
  useEffect(() => () => { counter.current++; }, []);
  return { version, invalidate, read, subscribe };
}
type Context = { schedule: ReturnType<typeof useSchedulePreview>; weather: ReturnType<typeof useWeatherForecast>; candidates: ReturnType<typeof useCandidatePool> };
type SavedReport = { version: number; data: WeatherScheduleReport };

export function useWeatherScheduleCheck({ schedule, weather, candidates }: Context, revision: ReturnType<typeof useWeatherCheckRevision>) {
  const [annotations, setAnnotations] = useState<ReadonlyMap<string, VisitExposure>>(() => new Map());
  const annotationsRef = useRef(annotations);
  const [report, setReport] = useState<SavedReport | null>(null);
  const reportRef = useRef<SavedReport | null>(null);
  const { read, invalidate, version } = revision;
  const onTick = useCallback((now: number) => {
    const active = reportRef.current;
    if (active && active.version === read()) {
      // This captures the weather snapshot which authorized the report, not a
      // mutable forecast guess. The report cannot revive if the clock goes back.
      const old = weather.state.response;
      if (!old || active.data.timeKey !== weatherCheckTimeKey(old, now)) invalidate();
    }
  }, [read, invalidate, weather.state.response]);
  const now = useWeatherDisplayClock(weather.state.response, onTick);
  const visits = scheduledVisits(schedule.state.response);
  const canCheck = schedule.state.status === "success" && visits.length > 0 && weather.state.response !== null && weather.state.status !== "loading" && candidates.state.status !== "loading";
  const valid = report !== null && report.version === version && weather.state.response !== null && report.data.timeKey === weatherCheckTimeKey(weather.state.response, now) && canCheck;
  const check = () => {
    if (version !== read() || !canCheck || !schedule.state.response || !weather.state.response) return false;
    const data = checkWeatherSchedule(schedule.state.response, weather.state.response, annotationsRef.current, Date.now(), {
      showingPrevious: weather.state.showingPrevious, refreshError: weather.state.status === "failed" ? weather.state.message : null,
    });
    // Replacing a report is a new authorization, even with identical input values.
    invalidate();
    const next = { version: read(), data }; reportRef.current = next; setReport(next); return true;
  };
  const annotate = (id: string, exposure: VisitExposure) => {
    if (version !== read() || !visits.some((visit) => visit.place.id === id) || !["indoor", "outdoor", "unknown"].includes(exposure)) return;
    invalidate();
    const next = new Map(annotationsRef.current); next.set(id, exposure); annotationsRef.current = next; setAnnotations(next);
  };
  const canAdjustNow = (id: string) => {
    // Re-check current time and event/report identity at the action boundary;
    // delayed event handlers cannot act on a new report or an expired forecast.
    if (!report || reportRef.current !== report || report.version !== read() || !canCheck || !weather.state.response || !schedule.state.response) return false;
    if (report.data.timeKey !== weatherCheckTimeKey(weather.state.response, Date.now())) { invalidate(); return false; }
    const currentVisit = visits.find((visit) => visit.place.id === id && visit.role === "optional");
    const checked = report.data.days.flatMap((day) => day.visits).find((visit) => visit.place.id === id);
    if (!currentVisit || !checked?.canAdjust || checked.role !== "optional" || candidates.excludedIds.has(id) || !candidates.candidates.some((candidate) => candidate.role === "optional" && candidate.place.id === id)) return false;
    return true;
  };
  const clear = () => { invalidate(); annotationsRef.current = new Map(); setAnnotations(new Map()); reportRef.current = null; setReport(null); };
  useEffect(() => () => { reportRef.current = null; }, []);
  return { annotations, visits, report: report?.data ?? null, valid, canCheck, check, annotate, canAdjustNow, clear };
}

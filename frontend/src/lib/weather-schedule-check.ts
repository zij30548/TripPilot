import type { Place } from "@/types/place";
import type { ScheduleResponse } from "@/types/schedule";
import { classifyWeatherPrecipitation, getShanghaiDate, getWeatherFreshness, type WeatherForecastResponse, type WeatherPeriod } from "@/types/weather";

export type VisitExposure = "outdoor" | "indoor" | "unknown";
export const exposureLabels: Record<VisitExposure, string> = { outdoor: "户外为主", indoor: "室内为主", unknown: "不确定" };
export type ScheduledVisit = { date: string; place: Place; role: "must_visit" | "optional"; start: string; end: string };
export type PeriodAssessment = { period: "白天" | "夜间"; description: string | null; status: "precipitation" | "no_trigger" | "unassessed"; explanation: string };
export type VisitAssessment = ScheduledVisit & { exposure: VisitExposure; attention: boolean; canAdjust: boolean; periods: PeriodAssessment[] };
export type WeatherScheduleReport = {
  checkedAt: number; timeKey: string; queriedAt: string; reportedAt: string | null;
  showingPrevious: boolean; refreshError: string | null;
  days: { date: string; visits: VisitAssessment[] }[];
};

// No Mock activities, candidate names, map bindings or name-based identity.
export function scheduledVisits(schedule: ScheduleResponse | null): ScheduledVisit[] {
  if (!schedule) return [];
  const required = new Set(schedule.request.must_visit_places.map((place) => place.id));
  const places = new Map([...schedule.request.must_visit_places, ...(schedule.request.optional_places ?? [])].map((place) => [place.id, place]));
  return schedule.days.flatMap((day) => day.items.flatMap((item) => {
    const place = item.kind === "visit" && item.place_id ? places.get(item.place_id) : undefined;
    return place ? [{ date: day.date, place, role: required.has(place.id) ? "must_visit" as const : "optional" as const, start: item.start_time, end: item.end_time }] : [];
  }));
}
export function weatherCheckTimeKey(weather: WeatherForecastResponse, now: number): string {
  return `${getShanghaiDate(now)}|${getWeatherFreshness(weather, now)}`;
}
function assessPeriod(period: "白天" | "夜间", value: WeatherPeriod | null, unavailable: string | null): PeriodAssessment {
  const description = value?.weather ?? null;
  if (unavailable) return { period, description, status: "unassessed", explanation: unavailable };
  const precipitation = classifyWeatherPrecipitation(description);
  if (precipitation === "unknown") return { period, description, status: "unassessed", explanation: "天气描述未知或未纳入规则，未评估。" };
  if (precipitation === "none") return { period, description, status: "no_trigger", explanation: "未触发雨雪规则，不代表保证适合出行。" };
  return { period, description, status: "precipitation", explanation: `同日${period}预报有${precipitation === "rain" ? "雨" : precipitation === "snow" ? "雪" : "雨雪"}；尚未确认活动具体时段的天气。` };
}

// Explicit clock and immutable inputs make the rule independently testable.
export function checkWeatherSchedule(schedule: ScheduleResponse, weather: WeatherForecastResponse, annotations: ReadonlyMap<string, VisitExposure>, now: number,
  previous: { showingPrevious: boolean; refreshError: string | null } = { showingPrevious: false, refreshError: null }): WeatherScheduleReport {
  const freshness = getWeatherFreshness(weather, now);
  const visits = scheduledVisits(schedule);
  return {
    checkedAt: now, timeKey: weatherCheckTimeKey(weather, now), queriedAt: weather.queried_at, reportedAt: weather.reported_at, ...previous,
    days: schedule.days.map(({ date }) => {
      const forecast = weather.days.find((day) => day.date === date);
      const unavailable = date < getShanghaiDate(now) ? "日期已过去；旧预报不是历史实际天气，未评估。"
        : freshness === "stale" ? "来源发布时间已超过 24 小时，预报陈旧，未评估。"
        : freshness === "unknown" ? "来源发布时间或新鲜度未知，未评估。"
        : forecast?.status !== "available" ? "暂未取得该日期预报，未评估。" : null;
      return { date, visits: visits.filter((visit) => visit.date === date).map((visit) => {
        const exposure = annotations.get(visit.place.id) ?? "unknown";
        const periods = [assessPeriod("白天", forecast?.day ?? null, unavailable), assessPeriod("夜间", forecast?.night ?? null, unavailable)];
        return { ...visit, exposure, periods, attention: exposure === "outdoor" && periods.some((period) => period.status === "precipitation"), canAdjust: periods.some((period) => period.status !== "unassessed") };
      }) };
    }),
  };
}

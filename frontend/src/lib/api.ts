import type { TripPlan, TripRequest } from "@/types/trip";

const PLAN_URL = "http://127.0.0.1:8000/trips/plan";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isAmount(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function isStringList(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isTripRequest(value: unknown): value is TripRequest {
  return isRecord(value) &&
    typeof value.start_date === "string" && typeof value.end_date === "string" &&
    Number.isFinite(Date.parse(value.start_date)) && Number.isFinite(Date.parse(value.end_date)) &&
    isAmount(value.budget) && value.budget > 0 &&
    typeof value.travelers === "number" && Number.isInteger(value.travelers) && value.travelers > 0 &&
    typeof value.accommodation_location === "string" &&
    ["relaxed", "balanced", "packed"].includes(String(value.pace)) &&
    isStringList(value.interests) && isStringList(value.must_visit) && isStringList(value.avoid_places) &&
    typeof value.daily_start_time === "string" && typeof value.daily_end_time === "string";
}

// TypeScript types alone do not validate JSON received over the network.
function isTripPlan(value: unknown): value is TripPlan {
  return (
    isRecord(value) &&
    typeof value.destination === "string" &&
    isTripRequest(value.request) &&
    isRecord(value.budget_breakdown) &&
    isAmount(value.budget_breakdown.transport) &&
    isAmount(value.budget_breakdown.food) &&
    isAmount(value.budget_breakdown.tickets) &&
    isAmount(value.budget_breakdown.other) &&
    typeof value.estimated_cost === "number" &&
    Number.isFinite(value.estimated_cost) &&
    value.estimated_cost >= 0 &&
    value.currency === "CNY" &&
    value.is_mock === true &&
    typeof value.notice === "string" &&
    Array.isArray(value.days) &&
    value.days.length > 0 &&
    value.days.every(
      (day: unknown) =>
        isRecord(day) &&
        typeof day.day === "number" &&
        Number.isInteger(day.day) &&
        day.day >= 1 &&
        typeof day.date === "string" && Number.isFinite(Date.parse(day.date)) &&
        isRecord(day.weather) && day.weather.date === day.date &&
        typeof day.weather.condition === "string" &&
        typeof day.weather.min_temperature === "number" && Number.isFinite(day.weather.min_temperature) &&
        typeof day.weather.max_temperature === "number" && Number.isFinite(day.weather.max_temperature) &&
        day.weather.min_temperature <= day.weather.max_temperature &&
        isAmount(day.weather.rain_risk) && day.weather.rain_risk <= 100 &&
        Array.isArray(day.transports) && day.transports.every((segment: unknown) =>
          isRecord(segment) && typeof segment.from_activity_id === "string" &&
          typeof segment.to_activity_id === "string" &&
          ["walking", "metro", "taxi"].includes(String(segment.mode)) &&
          isAmount(segment.duration_minutes) && segment.duration_minutes > 0 &&
          isAmount(segment.estimated_cost) && typeof segment.description === "string",
        ) &&
        typeof day.title === "string" &&
        Array.isArray(day.activities) &&
        day.activities.length > 0 &&
        day.activities.every(
          (activity: unknown) =>
            isRecord(activity) &&
            typeof activity.id === "string" &&
            ["sightseeing", "food", "museum", "shopping"].includes(String(activity.category)) &&
            isAmount(activity.estimated_cost) &&
            typeof activity.name === "string" &&
            typeof activity.description === "string" &&
            typeof activity.start_time === "string" &&
            typeof activity.end_time === "string",
        ),
    )
  );
}

export async function planTrip(request: TripRequest): Promise<TripPlan> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15_000);

  try {
    const response = await fetch(PLAN_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
      signal: controller.signal,
    });

    if (!response.ok) {
      if (response.status === 422) {
        throw new Error("旅行需求校验未通过，请检查日期、预算、人数、住宿位置和时间。住宿位置不能只有空格。");
      }
      throw new Error(`暂时无法获取行程（${response.status}），请稍后重试。`);
    }

    const data: unknown = await response.json();
    if (!isTripPlan(data)) {
      throw new Error("返回的行程格式不正确，请稍后重试。");
    }
    return data;
  } catch (error) {
    if (controller.signal.aborted) {
      throw new Error("请求超时，请稍后重试。");
    }
    if (error instanceof TypeError) {
      throw new Error("无法连接行程服务，请确认后端已启动后重试。");
    }
    if (error instanceof SyntaxError) {
      throw new Error("返回的行程格式不正确，请稍后重试。");
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

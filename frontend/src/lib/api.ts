import type { TripPlan, TripRequest } from "@/types/trip";

const PLAN_URL = "http://127.0.0.1:8000/trips/plan";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

// TypeScript types alone do not validate JSON received over the network.
function isTripPlan(value: unknown): value is TripPlan {
  return (
    isRecord(value) &&
    typeof value.destination === "string" &&
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
        typeof day.title === "string" &&
        Array.isArray(day.activities) &&
        day.activities.length > 0 &&
        day.activities.every(
          (activity: unknown) =>
            isRecord(activity) &&
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

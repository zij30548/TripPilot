import { isScheduleResponse, scheduleDates, type ScheduleResponse } from "@/types/schedule";
import type { TripRequest } from "@/types/trip";

export type Money = { status: "known"; cents: number } | { status: "unknown" } | { status: "invalid"; message: string };
export type CostInputs = { tickets: Record<string, string>; meals: Record<string, string>; accommodation: string; other: string };
export const emptyCostInputs = (): CostInputs => ({ tickets: {}, meals: {}, accommodation: "", other: "" });
const zero = BigInt(0), hundred = BigInt(100), max = BigInt(Number.MAX_SAFE_INTEGER);
const invalid = (message: string): Money => ({ status: "invalid", message });
function checked(value: bigint): Money {
  return value < zero || value > max ? invalid("金额超出可安全计算范围，请减少金额。") : { status: "known", cents: Number(value) };
}

// No binary floating-point money multiplication/rounding. Blank != explicit 0.
export function parseCostInput(value: string): Money {
  const text = value.trim();
  if (!text) return { status: "unknown" };
  if (text.length > 128 || !/^\d+(?:\.\d{1,2})?$/.test(text)) return invalid("请输入非负金额，最多两位小数，不接受科学计数法。 ");
  const [whole, fraction = ""] = text.split(".");
  return checked(BigInt(whole) * hundred + BigInt(fraction.padEnd(2, "0")));
}

// For already-decoded JSON numbers, use their canonical decimal representation.
// Accept only an EXACT integral cent; never round a sub-cent budget/fare.
export function numberToCents(value: number): Money {
  if (!Number.isFinite(value) || value < 0) return invalid("金额不是有效的非负数，无法核算。 ");
  const [mantissa, exp = "0"] = String(value).split("e");
  const [whole, fraction = ""] = mantissa.split(".");
  const coefficient = BigInt(whole + fraction), shift = 2 + Number(exp) - fraction.length;
  if (shift >= 0) return checked(coefficient * BigInt(10) ** BigInt(shift));
  const divisor = BigInt(10) ** BigInt(-shift);
  return coefficient % divisor === zero ? checked(coefficient / divisor) : invalid("金额无法精确到分，未作四舍五入，请核对。 ");
}
export function multiplyCents(cents: number, count: number): Money {
  if (!Number.isSafeInteger(cents) || cents < 0 || !Number.isSafeInteger(count) || count < 1) return invalid("金额或人数超出可安全计算范围。 ");
  return checked(BigInt(cents) * BigInt(count));
}
export function sumCents(values: number[]): Money {
  if (values.some((value) => !Number.isSafeInteger(value) || value < 0)) return invalid("存在无法安全累计的金额。 ");
  return checked(values.reduce((sum, value) => sum + BigInt(value), zero));
}
export function formatCents(value: number): string {
  if (!Number.isSafeInteger(value)) throw new Error("Cannot format unsafe cents");
  const cents = BigInt(Math.abs(value));
  return `${value < 0 ? "−" : ""}¥${String(cents / hundred).replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${String(cents % hundred).padStart(2, "0")}`;
}
export type UserCost = { key: string; category: "ticket" | "meal" | "accommodation" | "other"; label: string; detail: string; placeId?: string; date?: string; value: string; money: Money };
export type TransportCost = { key: string; date: string; start: string; end: string; from: string; to: string; edgeId: string; queriedAt: string; source: "amap" | "same_place"; kind: "walk" | "transit"; samePlace: boolean; fare: Money; money: Money };
type ReadyCosts = {
  status: "ready"; generatedAt: string; dates: string[]; travelers: number; budget: Money;
  transport: TransportCost[]; user: UserCost[]; missing: string[]; errors: string[];
  transportCents: number | null; userCents: number | null; subtotalCents: number | null; differenceCents: number | null;
  unarrangedRequired: number;
};
export type ScheduleCosts = ReadyCosts | { status: "unavailable"; message: string };

export function calculateScheduleCosts(response: ScheduleResponse | null, request: Pick<TripRequest, "budget" | "travelers" | "start_date" | "end_date">, inputs: CostInputs): ScheduleCosts {
  if (!response) return { status: "unavailable", message: "请先生成有效的正式草案，再核对费用。本次结果中的用户估算会保留，尚不显示预算结论。" };
  if (!isScheduleResponse(response, response.request) || request.start_date !== response.request.start_date || request.end_date !== response.request.end_date) return { status: "unavailable", message: "正式草案与需求不一致或数据无效，暂不核算费用。" };
  const visits = response.days.flatMap((day) => day.items.filter((item) => item.kind === "visit").map((item) => ({ id: item.place_id!, date: day.date })));
  if (!visits.length) return { status: "unavailable", message: "当前正式草案没有实际安排的游览，暂不核算费用；这不代表全程没有支出或预算充足。" };
  const dates = scheduleDates(request.start_date, request.end_date)!;
  const places = new Map([response.request.accommodation_place, ...response.request.must_visit_places, ...(response.request.optional_places ?? [])].map((p) => [p.id, p]));
  const edges = new Map(response.edges.map((e) => [e.id, e]));
  const name = (id: string) => places.get(id)!.name;
  const transport: TransportCost[] = response.days.flatMap((day) => day.items.flatMap((item, index) => {
    if (item.kind !== "walk" && item.kind !== "transit") return [];
    const edge = edges.get(item.edge_id!)!; // Full response guard above checked references/status/mode.
    const fare: Money = item.kind === "walk" || edge.status === "same_place" ? { status: "known", cents: 0 }
      : edge.transit_route!.fare_cny === null ? { status: "unknown" } : numberToCents(edge.transit_route!.fare_cny);
    return [{ key: `${day.date}-${index}`, date: day.date, start: item.start_time, end: item.end_time, from: name(edge.origin.place_id), to: name(edge.destination.place_id), edgeId: edge.id,
      queriedAt: edge.queried_at, source: edge.source, kind: item.kind, samePlace: edge.status === "same_place", fare,
      money: fare.status === "known" ? multiplyCents(fare.cents, request.travelers) : fare }];
  }));
  const row = (info: Omit<UserCost, "money">): UserCost => ({ ...info, money: parseCostInput(info.value) });
  const user: UserCost[] = visits.map(({ id, date }) => row({ key: `ticket-${id}`, category: "ticket", placeId: id, date, label: `${name(id)} · 门票／入场费`, detail: `${date} · ${places.get(id)!.address ?? "地址未知"} · POI ${id}`, value: Object.hasOwn(inputs.tickets, id) ? inputs.tickets[id] : "" }));
  dates.forEach((date) => user.push(row({ key: `meal-${date}`, category: "meal", date, label: `${date} · 全天餐饮费`, detail: "包括未安排景点的日期，与是否预留午餐无关。", value: inputs.meals[date] ?? "" })));
  user.push(row({ key: "accommodation", category: "accommodation", label: "全程住宿总额", detail: "住宿参考点不提供房价；不推算住宿晚数。", value: inputs.accommodation }), row({ key: "other", category: "other", label: "全程其他费用", detail: "填写全程合计，不再乘人数或天数。", value: inputs.other }));
  const budget = numberToCents(request.budget), missing: string[] = [], errors: string[] = [];
  if (budget.status === "invalid") errors.push(`总预算：${budget.message}`);
  if (!Number.isSafeInteger(request.travelers) || request.travelers < 1) errors.push("旅行人数无效，无法核算。 ");
  for (const item of [...transport.map((r) => ({ label: `${r.date} ${r.start} ${r.from} → ${r.to} 参考票价`, money: r.money })), ...user]) {
    if (item.money.status === "unknown") missing.push(item.label);
    if (item.money.status === "invalid") errors.push(`${item.label}：${item.money.message}`);
  }
  const total = (label: string, rows: { money: Money }[]): number | null => {
    if (rows.some((r) => r.money.status === "invalid")) return null;
    const result = sumCents(rows.flatMap((r) => r.money.status === "known" ? [r.money.cents] : []));
    if (result.status === "invalid") { errors.push(`${label}：${result.message}`); return null; }
    return result.status === "known" ? result.cents : null;
  };
  const transportCents = total("交通参考小计", transport), userCents = total("用户估算小计", user);
  let subtotalCents: number | null = null;
  if (!errors.length && transportCents !== null && userCents !== null) {
    const sum = sumCents([transportCents, userCents]);
    if (sum.status === "known") subtotalCents = sum.cents;
    else if (sum.status === "invalid") errors.push(`当前小计：${sum.message}`);
  }
  return { status: "ready", generatedAt: response.generated_at, dates, travelers: request.travelers, budget, transport, user, missing, errors, transportCents, userCents, subtotalCents,
    differenceCents: subtotalCents !== null && budget.status === "known" ? budget.cents - subtotalCents : null, unarrangedRequired: response.unscheduled.length };
}

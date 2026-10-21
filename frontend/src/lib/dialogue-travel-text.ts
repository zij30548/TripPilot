import type { ScheduleResponse } from "@/types/schedule";
import type { WeatherForecastResponse } from "@/types/weather";
import type { TripRequest } from "@/types/trip";
import { calculateScheduleCosts, compareScheduleCosts, formatCents, type CostInputs, type ScheduleCosts } from "./schedule-costs";
import { compareSchedules } from "./schedule-adjustment";
import { scheduleBlocker } from "./schedule-blocker";
import type { SavedDetail, SavedList } from "./saved-trips";
import { exposureLabels, type WeatherScheduleReport } from "./weather-schedule-check";
import { isDialogueAction } from "./dialogue-action";

const mode = (response: ScheduleResponse) => response.request.transport_mode === "transit" ? "公交／地铁参考" : "步行参考";
const names = (response: ScheduleResponse) => new Map([response.request.accommodation_place, ...response.request.must_visit_places,
  ...(response.request.optional_places ?? [])].map(place => [place.id, place.name]));

// Presentation only. All facts come from validated responses and the shared
// calculators; no language model invents route times, totals, or place identity.
export function scheduleDialogueText(response: ScheduleResponse | null, date?: string): string {
  if (!response) return "当前还没有有效正式草案，请先确认需求与真实地点，再主动生成。";
  const places = names(response);
  const days = date ? response.days.filter(day => day.date === date) : response.days;
  if (!days.length) return "这个日期不在当前正式草案内，请提供完整旅行日期或第几天。";
  const lines = [`${mode(response)} · 草案生成于 ${response.generated_at}`, `必去已安排 ${response.request.must_visit_places.length - response.unscheduled.length}/${response.request.must_visit_places.length}。`];
  const blocker = scheduleBlocker(response);
  if (blocker) {
    lines.push(`首先未能安排：${places.get(blocker.first.place_id)}。${blocker.first.message}`);
    for (const road of blocker.roads) lines.push(`受阻路段：${places.get(road.from)} → ${places.get(road.to)}；${road.edge?.message ?? "未取得可用交通参考"}。`);
    if (blocker.followingCount) lines.push(`按固定必去顺序，后续 ${blocker.followingCount} 个地点未继续安排；这不表示这些地点自身无路线。`);
  }
  const required = new Set(response.request.must_visit_places.map(place => place.id));
  for (const day of days) {
    lines.push(`\n${day.date}`);
    if (!day.items.some(item => item.kind === "visit")) { lines.push("当天未安排游览，没有可展示的返程时间。"); continue; }
    for (const item of day.items) {
      const time = `${item.start_time}—${item.end_time}`;
      if (item.kind === "visit") lines.push(`${time} ${places.get(item.place_id!)}（${required.has(item.place_id!) ? "必去" : "可选"}，停留 ${item.duration_minutes} 分钟，${item.duration_source === "user" ? "用户设置" : "默认设置"}）`);
      else if (item.kind === "lunch") lines.push(`${time} 午餐预留（不是已确认的餐厅）`);
      else if (item.kind === "wait") lines.push(`${time} 等待／时间预留`);
      else {
        const edge = response.edges.find(edge => edge.id === item.edge_id)!;
        lines.push(`${time} ${item.kind === "transit" ? "公交／地铁" : "步行"} ${places.get(item.from_place_id!)} → ${places.get(item.to_place_id!)}，参考 ${item.duration_minutes} 分钟；查询于 ${edge.queried_at}`);
        if (edge.transit_route) {
          lines.push(`整条方案每人参考票价：${edge.transit_route.fare_cny === null ? "未知" : `¥${edge.transit_route.fare_cny}`}。`);
          edge.transit_route.legs.forEach(leg => lines.push(`  ${leg.mode === "walking" ? "接驳步行" : leg.line_name ?? "乘车段"}：${leg.instruction ?? "说明未知"}${leg.departure_stop || leg.arrival_stop ? `（${leg.departure_stop ?? "未知站点"} → ${leg.arrival_stop ?? "未知站点"}）` : ""}`));
        }
      }
    }
    lines.push(`预计返回住宿参考点：${day.return_time ?? "未安排"}。`);
  }
  for (const result of response.unscheduled) lines.push(`未安排必去：${places.get(result.place_id)}；${result.message}`);
  for (const result of response.optional_results.filter(result => !result.scheduled_date)) {
    lines.push(`未安排可选：${places.get(result.place_id)}；${result.not_attempted_reason === "must_incomplete" ? "必去未排齐，因此未尝试" : result.attempts.map(attempt => `${attempt.date} ${attempt.message}`).join("；")}`);
  }
  lines.push("交通为查询批次的参考估时，不保证旅行当天班次、营业或预约；本方案不承诺最优。", ...response.unknowns);
  return lines.join("\n");
}

export function costsDialogueText(costs: ScheduleCosts, label = "当前正式草案费用"): string {
  if (costs.status !== "ready") return costs.message;
  const amount = (value: number | null) => value === null ? "无法安全核算" : formatCents(value);
  const lines = [`${label} · 生成于 ${costs.generatedAt}`, `人民币，${costs.travelers} 位旅客全程合计。`,
    `交通参考已知小计：${amount(costs.transportCents)}`, `用户估算已填小计：${amount(costs.userCents)}`, `当前可计算小计：${amount(costs.subtotalCents)}`];
  for (const row of costs.transport) lines.push(`${row.date} ${row.start} ${row.from} → ${row.to}：${row.fare.status === "known" ? `${formatCents(row.fare.cents)} × ${costs.travelers} 人` : row.fare.status === "unknown" ? "参考票价未知" : row.fare.message}；来源 ${row.source === "amap" ? "高德" : "同地点零移动"}，${row.queriedAt}`);
  lines.push("公交暂按每位旅客均按整条方案的参考票价计费；未核实儿童优惠、通票或旅行当天价格。步行票费为零不表示景点免费。");
  for (const row of costs.user) lines.push(`${row.label}（用户估算）：${row.money.status === "known" ? formatCents(row.money.cents) : row.money.status === "unknown" ? "未填写／未知" : `无效：${row.money.message}`}`);
  if (costs.errors.length) lines.push("存在非法金额，不能提供有效总额：", ...costs.errors);
  if (costs.missing.length) lines.push("尚未核算：", ...costs.missing);
  if (costs.differenceCents !== null) lines.push(`预算减当前小计：${formatCents(costs.differenceCents)}。${costs.differenceCents < 0 ? "已计入部分已超过预算。" : costs.missing.length ? "仍有未知费用，不能作为可自由支配余额。" : "仅按当前参考票价和用户估算计算，不保证实际支出。"}`);
  if (costs.unarrangedRequired) lines.push(`还有 ${costs.unarrangedRequired} 个必去地点未安排，以上仅是已安排部分费用。`);
  return lines.join("\n");
}

export function adjustmentDialogueText(before: ScheduleResponse, after: ScheduleResponse, targetName: string, request: TripRequest, costs: CostInputs): string {
  const delta = compareSchedules(before, after), money = compareScheduleCosts(before, after, request, costs);
  const lines = [`调整预览，尚未采用。拟排除：${targetName}。`, `原方案 ${before.generated_at} · ${mode(before)}；预览 ${after.generated_at} · ${mode(after)}。`,
    "不同查询批次可能改变参考估时与票价，差异不一定全部由排除地点造成。两边均使用当前费用估算。",
    ...delta.removed.map(visit => `移除：${visit.place.name}（${visit.place.id}） ${visit.date} ${visit.start}—${visit.end}`),
    ...delta.added.map(visit => `新增：${visit.place.name}（${visit.place.id}） ${visit.date} ${visit.start}—${visit.end}；未填写门票估算时仍未知，不是自动室内替代。`),
    ...delta.retained.map(({before: old, after: next, changed}) => `保留：${old.place.name}；${changed ? `${old.date} ${old.start}—${old.end} → ${next.date} ${next.start}—${next.end}` : "日期和时间不变"}`),
    ...delta.returns.map(day => `${day.date} 返回住宿：${day.before ?? "无返回时间"} → ${day.after ?? "无返回时间"}`)];
  if (!delta.added.length) lines.push("此次没有补入新地点。");
  lines.push(`\n原方案费用\n${costsDialogueText(money.before, "原正式方案费用")}`, `\n预览费用\n${costsDialogueText(money.after, "调整预览费用（尚未采用）")}`);
  lines.push(money.deltaCents === null ? `完整费用增减暂不可计算。${money.issues.join(" ")}` : money.deltaCents === 0 ? "按当前参考票价和用户估算，两方案金额相同。" : `按当前参考票价和用户估算，新方案${money.deltaCents > 0 ? "增加" : "减少"} ${formatCents(Math.abs(money.deltaCents))}。`);
  lines.push("\n预览完整安排：", scheduleDialogueText(after));
  return lines.join("\n");
}

export function weatherDialogueText(response: WeatherForecastResponse | null): string {
  if (!response) return "当前尚未查询天气，请主动查询；没有预报不能当作晴天。";
  return [`${response.city} · 高德天气，查询于 ${response.queried_at}，来源发布时间 ${response.reported_at ?? "未知"}。`,
    ...response.days.map(day => day.status === "unavailable" ? `${day.date}：无覆盖，未用其他日期替代。` : `${day.date}：白天 ${day.day?.weather ?? "未知"} ${day.day?.temperature_celsius ?? "未知"}℃；夜间 ${day.night?.weather ?? "未知"} ${day.night?.temperature_celsius ?? "未知"}℃。`),
    "预报不是旅行当天实际天气，不保证逐小时情况。"].join("\n");
}

export function weatherCheckDialogueText(report: WeatherScheduleReport | null, valid: boolean): string {
  if (!report || !valid) return "当前天气检查未完成或已过期，请先主动查询天气，再检查当前正式草案。";
  return [`天气关联检查，${report.queriedAt} 查询批次。`, ...report.days.flatMap(day => day.visits.length ? day.visits.map(visit =>
    `${day.date} ${visit.place.name}（${exposureLabels[visit.exposure]}）：${visit.periods.map(period => `${period.period} ${period.explanation}`).join("；")}`) : [`${day.date} 没有已安排的游览。`])].join("\n");
}

export function historyDialogueText(value: SavedList | SavedDetail): string {
  if ("items" in value) {
    const page = Math.floor(value.offset / value.limit) + 1, next = value.offset + value.limit;
    if (!value.items.length) return value.offset === 0 ? "当前账号没有已保存行程。" : `历史第${page}页没有记录；可回复“查看历史”返回第一页。`;
    const more = value.has_more ? [value.limit === 10 && next % 10 === 0 && isDialogueAction({ name: "list_saved", arguments: { offset: next } })
      ? `还有下一页，请回复“查看第${page + 1}页历史”。` : "还有历史记录，但已达到本聊天入口分页范围；不会自动继续读取。"] : [];
    return [`历史快照第${page}页（未重新核验；编号只对应本页）：`, ...value.items.map((item, index) => `${index + 1}. ${item.name} · ${item.start_date}—${item.end_date} · ${item.transport_mode === "walking" ? "步行" : "公交／地铁"} · 保存于 ${item.saved_at} · 记录 ${item.id}`), ...more].join("\n");
  }
  return [`历史快照“${value.name}”，保存于 ${value.saved_at}。不会覆盖当前正式草案，原数据未经重新核验。`,
    scheduleDialogueText(value.snapshot.schedule), costsDialogueText(calculateScheduleCosts(value.snapshot.schedule, value.snapshot.requirements, value.snapshot.costs), "历史快照费用（未重新核验）")].join("\n");
}

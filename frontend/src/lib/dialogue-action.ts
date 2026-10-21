import registry from "../../../shared/dialogue-actions.json";
import { readDialogueActionReceipt } from "./assistant";

export type DialogueAction = { name: string; arguments: Record<string, unknown> };
export type DialogueReceipt = { schema_version: "dialogue-action-v1"; conversation_id: string; revision: number; operation_id: string | null; action: DialogueAction | null };
type Schema = { type?: string; properties?: Record<string, Schema>; required?: string[]; additionalProperties?: boolean;
  anyOf?: Schema[]; enum?: unknown[]; const?: unknown; minLength?: number; maxLength?: number; minimum?: number; maximum?: number; pattern?: string; format?: string };
const schemas = new Map(registry.actions.map(row => [row.name, row.arguments as unknown as Schema]));
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const exact = (v: Record<string, unknown>, keys: string[]) => Object.keys(v).length === keys.length && keys.every(key => Object.hasOwn(v, key));

// Deliberately implements only this reviewed registry's finite schema subset.
// New schema features fail closed instead of quietly broadening arguments.
function matches(value: unknown, schema: Schema): boolean {
  if (Object.keys(schema).some(key => !["type", "properties", "required", "additionalProperties", "anyOf", "enum", "const", "minLength", "maxLength", "minimum", "maximum", "pattern", "format"].includes(key))) return false;
  if (schema.anyOf) return schema.anyOf.some(branch => matches(value, branch));
  if (schema.enum && !schema.enum.includes(value) || Object.hasOwn(schema, "const") && value !== schema.const) return false;
  switch (schema.type) {
    case "null": return value === null;
    case "boolean": return typeof value === "boolean";
    case "integer": return typeof value === "number" && Number.isSafeInteger(value) && value >= (schema.minimum ?? -Infinity) && value <= (schema.maximum ?? Infinity);
    case "string": {
      if (typeof value !== "string" || Array.from(value).length < (schema.minLength ?? 0) || Array.from(value).length > (schema.maxLength ?? Infinity) || schema.pattern && !new RegExp(schema.pattern).test(value)) return false;
      if (schema.format && schema.format !== "date") return false;
      return schema.format !== "date" || /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
    }
    case "object": return object(value) && schema.additionalProperties === false && !!schema.properties &&
      exact(value, schema.required ?? []) && Object.entries(schema.properties).every(([key, definition]) => Object.hasOwn(value, key) && matches(value[key], definition));
    default: return false;
  }
}

export function isDialogueAction(value: unknown): value is DialogueAction {
  if (!object(value) || !exact(value, ["name", "arguments"]) || typeof value.name !== "string" || !object(value.arguments)) return false;
  const schema = schemas.get(value.name), args = value.arguments;
  if (!schema || !matches(args, schema)) return false;
  if (Object.entries(args).some(([key, value]) => key !== "value" && typeof value === "string" && (!value.trim() || value !== value.trim() || /[\u0000-\u001f\u007f]/.test(value)))) return false;
  if (value.name === "read_schedule" && args.date !== null && args.day !== null) return false;
  if (["set_lunch", "set_daily_window"].includes(value.name) && String(args.start) >= String(args.end)) return false;
  if (value.name === "set_cost" && (["ticket", "meal"].includes(String(args.kind)) !== (args.selector !== null))) return false;
  return true;
}

export async function readDialogueAction(id: string, revision: number, operation: string, signal: AbortSignal): Promise<DialogueAction | null> {
  const v = await readDialogueActionReceipt(id, signal);
  if (!object(v) || !exact(v, ["schema_version", "conversation_id", "revision", "operation_id", "action"]) ||
    v.schema_version !== "dialogue-action-v1" || v.conversation_id !== id || v.revision !== revision || v.operation_id !== operation ||
    v.action !== null && !isDialogueAction(v.action)) throw new Error("这项操作不属于当前回复，未执行。请重新读取对话。");
  if (signal.aborted) throw new DOMException("已取消", "AbortError");
  return v.action as DialogueAction | null;
}

const labels: Record<string, string> = {
  search_places: "搜索真实地点", preview_place: "预览地点", confirm_place: "确认这个真实地点", remove_place: "移除已确认地点",
  prepare_candidates: "查询可选景点", exclude_candidate: "排除可选地点", restore_candidate: "恢复可选地点",
  confirm_requirements: "确认当前需求", prepare_planning: "准备规划输入", generate_schedule: "生成正式草案",
  read_schedule: "查看行程", set_stay: "修改停留时间", set_lunch: "修改午餐设置", set_transport: "修改交通方式", set_daily_window: "修改每日活动时间",
  read_budget: "核对费用", set_cost: "修改用户费用估算", query_weather: "查询天气", check_weather: "检查天气影响", read_weather: "查看已有天气",
  set_visit_mode: "标注游览方式", preview_exclusion: "生成排除地点后的预览", adopt_preview: "采用当前预览", cancel_preview: "取消预览", read_preview: "查看预览对比",
  save_trip: "保存为新行程记录", list_saved: "查看历史行程", read_saved: "读取历史快照", restore_saved: "从历史创建规划副本",
  explain_schedule: "请求 AI 解读当前草案", show_map: "查看正式行程地图", open_photo: "选择照片", open_video: "选择视频",
  read_preferences: "查看已记住的偏好", save_preference: "同意保存长期偏好", delete_preference: "撤回长期偏好", search_materials: "检索已收录资料", cancel_operation: "停止当前操作",
};
export function actionDescription(action: DialogueAction): string {
  const a = action.arguments;
  const details = Object.entries(a).filter(([, value]) => value !== null).map(([key, value]) => {
    const names: Record<string, string> = { keyword: "地点", role: "用途", number: "候选序号", selector: "目标", date: "日期", day: "第几天", minutes: "分钟", enabled: "午餐", start: "开始", end: "结束", mode: "方式", kind: "费用类型", value: "内容", name: "名称", key: "偏好项", venue_id: "馆区", question: "问题", edge_id: "交通段" };
    return `${names[key] ?? key}：${value === "accommodation" ? "住宿" : value === "required" ? "必去" : value === "walking" ? "步行" : value === "transit" ? "公交／地铁" : String(value)}`;
  });
  return `${labels[action.name] ?? "操作"}${details.length ? `\n${details.join("；")}` : ""}`;
}

export function needsActionConfirmation(action: DialogueAction): boolean {
  return !["preview_place", "read_schedule", "read_budget", "read_weather", "check_weather", "read_preview", "list_saved", "read_saved", "read_preferences", "search_materials", "cancel_preview", "cancel_operation", "open_photo", "open_video"].includes(action.name);
}

/** Only an exact, standalone answer consumes the currently displayed proposal. */
export function confirmationReply(text: string): "confirm" | "cancel" | null {
  const clean = text.trim().replace(/[。！!]+$/, "");
  if (["确认", "确认执行", "确认此次操作"].includes(clean)) return "confirm";
  if (["取消", "取消此次操作"].includes(clean)) return "cancel";
  return null;
}

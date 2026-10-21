import { isDialogueAction, type DialogueAction } from "./dialogue-action";

export type DialogueBinding = { conversation: string; revision: number; epoch: number; workspace: string };
type Pending = { token: string; binding: DialogueBinding; action: DialogueAction };

/** Confirmations belong to the displayed proposal, never to a reused name. */
export class DialogueConfirmation {
  private pending: Pending | null = null;
  private generation = 0;
  offer(action: DialogueAction, binding: DialogueBinding): string {
    if (!isDialogueAction(action)) throw new Error("操作格式无效，未准备。");
    const token = `${++this.generation}:${crypto.randomUUID()}`;
    this.pending = { token, binding: structuredClone(binding), action: structuredClone(action) };
    return token;
  }
  take(token: string, current: DialogueBinding): DialogueAction | null {
    const p = this.pending;
    if (!p || token !== p.token) return null;
    // Consume before any await or network operation. Double clicks cannot retry.
    this.pending = null;
    if (Object.keys(current).some(key => current[key as keyof DialogueBinding] !== p.binding[key as keyof DialogueBinding])) return null;
    return structuredClone(p.action);
  }
  clear(): void { this.generation++; this.pending = null; }
  currentToken(): string | null { return this.pending?.token ?? null; }
}

/** Small explicit commands avoid a model call for confirmation and readbacks.
 * Everything else remains a natural-language assistant turn, not fuzzy regex execution.
 */
export function localDialogueAction(text: string): DialogueAction | null {
  const s = text.trim();
  const fixed: Record<string, DialogueAction> = {
    "查看行程": { name: "read_schedule", arguments: { date: null, day: null } },
    "核对费用": { name: "read_budget", arguments: {} },
    "查看费用": { name: "read_budget", arguments: {} },
    "查看已有天气": { name: "read_weather", arguments: {} },
    "查看预览": { name: "read_preview", arguments: {} },
    "取消预览": { name: "cancel_preview", arguments: {} },
    "采用预览": { name: "adopt_preview", arguments: {} },
    "生成行程": { name: "generate_schedule", arguments: {} },
    "准备规划": { name: "prepare_planning", arguments: {} },
    "确认需求": { name: "confirm_requirements", arguments: {} },
    "查看历史": { name: "list_saved", arguments: { offset: 0 } },
    "查看偏好": { name: "read_preferences", arguments: {} },
    "查询天气": { name: "query_weather", arguments: {} },
    "检查天气": { name: "check_weather", arguments: {} },
    "准备可选景点": { name: "prepare_candidates", arguments: {} },
    "查看地图": { name: "show_map", arguments: { date: null, edge_id: null } },
    "添加照片": { name: "open_photo", arguments: {} },
    "添加视频": { name: "open_video", arguments: {} },
  };
  if (fixed[s]) return structuredClone(fixed[s]);
  const historyPage = /^查看第([1-9]\d{0,3})页历史$/.exec(s);
  if (historyPage) {
    const action = { name: "list_saved", arguments: { offset: (Number(historyPage[1]) - 1) * 10 } };
    return isDialogueAction(action) ? action : null;
  }
  const candidate = /^(预览|确认)地点\s*([1-9]|1\d|20)$/.exec(s);
  if (candidate) return { name: candidate[1] === "预览" ? "preview_place" : "confirm_place", arguments: { number: Number(candidate[2]) } };
  const day = /^查看第([1-7一二三四五六七])天$/.exec(s);
  if (day) return { name: "read_schedule", arguments: { date: null, day: /^[1-7]$/.test(day[1]) ? Number(day[1]) : "一二三四五六七".indexOf(day[1]) + 1 } };
  const search = /^搜索(住宿|必去)\s+(.{1,80})$/u.exec(s);
  if (search) return { name: "search_places", arguments: { role: search[1] === "住宿" ? "accommodation" : "required", keyword: search[2].trim() } };
  const save = /^保存行程\s+(.{1,80})$/u.exec(s);
  if (save) return { name: "save_trip", arguments: { name: save[1].trim() } };
  return null;
}

import { isTripRequest } from "./api";
import { isChatConversation, type ChatConversation } from "./chat";
import { chatCity } from "./chat-cities";
import { isPlacesWorkspace, type PlacesWorkspace } from "./chat-places";
import { DESTINATIONS } from "./destinations";
import { newWorkspaceInputs, validEditedRequirements, type WorkspaceInputs } from "./restore-trip";
import { numberToCents, parseCostInput } from "./schedule-costs";
import { canonical } from "./saved-trips";
import type { TripRequest } from "@/types/trip";

export type PlanningWindow = { start: string; end: string };
export const DEFAULT_ASSISTANT_WINDOW: PlanningWindow = { start: "09:00", end: "18:00" };

/** One current-version gate, before even synchronizing dependent POI inputs. */
export function assertConfirmedPlanningRequirements(conversation: ChatConversation): void {
  if (conversation.confirmed_revision !== conversation.revision) throw new Error("当前是待确认的需求草稿。请先回复“确认需求”，核对并明确确认当前版本，再准备规划；不会自动确认或生成。");
}

/** Actual input identity only: a read-only chat turn must not discard a draft. */
export function assistantPlanningKey(conversation: ChatConversation, places: PlacesWorkspace | null, window: PlanningWindow): string {
  return canonical({ conversation: conversation.id, requirements: conversation.requirements,
    places: places && { conversation_id: places.conversation_id, city_id: places.city_id,
      accommodation: places.accommodation, required_places: places.required_places }, window });
}

/** Pure conversion. No inferred Place, network call, response or automatic save. */
export function prepareAssistantPlanning(conversation: ChatConversation, places: PlacesWorkspace | null, window: PlanningWindow = DEFAULT_ASSISTANT_WINDOW): WorkspaceInputs {
  if (!isChatConversation(conversation)) throw new Error("当前会话需求无效，请重新读取会话。");
  assertConfirmedPlanningRequirements(conversation);
  if (conversation.issues.some(issue => issue !== "unimplemented_preference")) throw new Error("当前需求仍有歧义、跨范围或不支持事项，请先在聊天中明确确认；不会静默只规划其中一座城市。");
  const r = conversation.requirements;
  const city = chatCity(r.destination_id);
  const destination = DESTINATIONS.find(item => item.adcode === city?.id);
  if (!destination) throw new Error("当前城市仅支持地点准备，行程排程仍限上海、杭州、北京、广州、成都、西安；不会自动改用其他城市。");
  if (!places || !isPlacesWorkspace(places) || places.conversation_id !== conversation.id || places.intent_revision !== conversation.revision || places.stale) {
    throw new Error("请展开地点准备，重新读取并同步当前需求，再核对已确认地点。");
  }
  if (places.city_id !== destination.adcode) throw new Error("地点准备与旅行需求城市不一致，请先明确确认同一城市。");
  if (!places.accommodation) throw new Error("请先在地点准备中搜索、预览并明确确认住宿参考点。");
  if (!r.start_date || !r.end_date || !r.travelers || !r.budget_cny || !r.transport_mode) throw new Error("请先补全旅行日期、人数、全程预算和交通方式，再准备行程。");
  const parsed = parseCostInput(r.budget_cny);
  const budget = Number(r.budget_cny);
  const roundTrip = numberToCents(budget);
  if (parsed.status !== "known" || parsed.cents <= 0 || roundTrip.status !== "known" || roundTrip.cents !== parsed.cents) {
    throw new Error("预算无法按现有整数分规则安全表示，请核对金额；没有四舍五入或截断。");
  }
  const request: TripRequest = {
    destination_id: destination.id, start_date: r.start_date, end_date: r.end_date, budget, travelers: r.travelers,
    accommodation_location: places.accommodation.name, accommodation_place: structuredClone(places.accommodation),
    must_visit: places.required_places.map(place => place.name), must_visit_places: structuredClone(places.required_places),
    pace: "balanced", interests: [], avoid_places: [], daily_start_time: window.start, daily_end_time: window.end,
  };
  if (!isTripRequest(request) || !validEditedRequirements(request)) throw new Error("地点归属、旅行日期或每日时间窗口无效，请核对后再准备；没有替换或补造输入。");
  const inputs = newWorkspaceInputs(request);
  inputs.settings.transportMode = r.transport_mode;
  return inputs;
}

import { isTripRequest } from "./api";
import { isScheduleResponse, scheduleClock, scheduleDates, type ScheduleResponse } from "@/types/schedule";
import type { TripRequest } from "@/types/trip";
import type { CostInputs } from "./schedule-costs";
import { LEGACY_MAX_TRIP_DAYS, MAX_TRIP_DAYS, PLANNING_LIMITS } from "./planning-limits";
import { API_ENDPOINTS } from "./api-endpoints";

export const ARCHIVE_VERSION = "saved-trip-v1";
export const CITY_ARCHIVE_VERSION = "saved-trip-v2";
export const CURRENT_ARCHIVE_VERSION = "saved-trip-v3";
const versions = [ARCHIVE_VERSION, CITY_ARCHIVE_VERSION, CURRENT_ARCHIVE_VERSION];
export type ArchiveVersion = typeof ARCHIVE_VERSION | typeof CITY_ARCHIVE_VERSION | typeof CURRENT_ARCHIVE_VERSION;
export const ARCHIVE_BYTES = 2 * 1024 * 1024;
export type SavedSnapshot = { schema_version: ArchiveVersion; requirements: TripRequest; schedule: ScheduleResponse; costs: CostInputs };
export type SaveOperation = { operation_id: string; name: string; snapshot: SavedSnapshot };
export type SavedSummary = { id: string; name: string; saved_at: string; schema_version: ArchiveVersion; start_date: string; end_date: string; transport_mode: "walking" | "transit"; status: ScheduleResponse["status"] };
export type SavedDetail = SavedSummary & { snapshot: SavedSnapshot };
export type SavedList = { items: SavedSummary[]; offset: number; limit: number; has_more: boolean };
const record = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const exact = (v: Record<string, unknown>, keys: string[]) => Object.keys(v).length === keys.length && keys.every(k => Object.hasOwn(v,k));
const id = (v: unknown): v is string => typeof v === "string" && /^[0-9a-f]{32}$/.test(v);
export const validSaveName = (v: unknown): v is string => typeof v === "string" && !!v.trim() && Array.from(v).length <= 80 && !/[\u0000-\u001f]/.test(v);
const reqKeys = ["start_date", "end_date", "budget", "travelers", "accommodation_location", "accommodation_place", "pace", "interests", "must_visit", "must_visit_places", "avoid_places", "daily_start_time", "daily_end_time"];
const summaryKeys = ["id", "name", "saved_at", "schema_version", "start_date", "end_date", "transport_mode", "status"];
export function canonical(value: unknown): string {
  return JSON.stringify(value, (_key, v) => record(v) ? Object.fromEntries(Object.keys(v).sort().map(k => [k,v[k]])) : v);
}
function bounded(value: unknown, depth = 0, count = { n: 0 }): boolean {
  if (++count.n > 200000 || depth > 24) return false;
  if (typeof value === "string") return Array.from(value).length <= 1024;
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) return value.length <= 10000 && value.every(v => bounded(v,depth+1,count));
  return !record(value) || Object.keys(value).length <= 64 && Object.values(value).every(v => bounded(v,depth+1,count));
}
export function isSavedSnapshot(value: unknown): value is SavedSnapshot {
  if (!record(value) || !exact(value,["schema_version","requirements","schedule","costs"]) || !versions.includes(value.schema_version as string) || !bounded(value) ||
      !record(value.requirements) || !exact(value.requirements,[...reqKeys, ...(value.schema_version !== ARCHIVE_VERSION ? ["destination_id"] : [])]) || !isTripRequest(value.requirements) ||
      !record(value.schedule) || !record(value.schedule.request) || !isScheduleResponse(value.schedule,value.schedule.request as ScheduleResponse["request"])) return false;
  const r=value.requirements, s=value.schedule, q=s.request;
  if (r.destination_id !== q.destination_id || (value.schema_version !== ARCHIVE_VERSION && !r.destination_id)) return false;
  if (value.schema_version !== CURRENT_ARCHIVE_VERSION && (!scheduleDates(q.start_date,q.end_date,LEGACY_MAX_TRIP_DAYS) || s.edges.length > PLANNING_LIMITS.legacy_max_logical_edges)) return false;
  if (!Number.isSafeInteger(r.travelers) || r.travelers > 1000 || r.interests.length > 32 || r.avoid_places.length > 32 ||
      r.start_date!==q.start_date || r.end_date!==q.end_date || scheduleClock(r.daily_start_time)!==q.daily_start_time || scheduleClock(r.daily_end_time)!==q.daily_end_time ||
      canonical(r.accommodation_place)!==canonical(q.accommodation_place) || canonical(r.must_visit_places)!==canonical(q.must_visit_places) ||
      canonical(r.must_visit)!==canonical(q.must_visit_places.map(p=>p.name)) || r.accommodation_location!==q.accommodation_place.name) return false;
  const places=[q.accommodation_place,...q.must_visit_places,...(q.optional_places??[])];
  if (value.schema_version === ARCHIVE_VERSION && ("destination_id" in q || places.some(p => "region" in p) || [r.accommodation_place, ...r.must_visit_places ?? []].some(p => p && "region" in p))) return false;
  if (places.some(p=>p.name.length>160 || /[\u0000-\u001f]/.test(p.name)) || !Object.hasOwn(q,"optional_places") ||
      s.edges.some(e=>(e.duration_seconds??0)>604800 || e.transit_route && (e.transit_route.legs.length>60 || e.transit_route.legs.some(l=>l.geometry.length>256))) || s.days.some(d=>d.items.length>40)) return false;
  const c=value.costs, visits=s.days.flatMap(d=>d.items.filter(i=>i.kind==="visit").map(i=>i.place_id!));
  const text=(v:unknown)=>typeof v==="string" && Array.from(v).length<=256;
  return record(c) && exact(c,["tickets","meals","accommodation","other"]) && record(c.tickets) && exact(c.tickets,visits) &&
    record(c.meals) && exact(c.meals,s.days.map(d=>d.date)) && [...Object.values(c.tickets),...Object.values(c.meals),c.accommodation,c.other].every(text);
}
export function createSnapshot(requirements: TripRequest, schedule: ScheduleResponse, inputs: CostInputs): SavedSnapshot {
  // Only the validated official result and explicit white-listed requirement fields.
  if (!isScheduleResponse(schedule, schedule.request)) throw new Error("当前正式草案未通过校验，未保存。");
  // Live drawing delivery is separate; never serialize a controller or delivery.
  // Keep transit_route inside edges exactly as v1 has always stored it.
  const core = { status: schedule.status, generated_at: schedule.generated_at, request: schedule.request,
    days: schedule.days, unscheduled: schedule.unscheduled, edges: schedule.edges,
    rules: schedule.rules, unknowns: schedule.unknowns, optional_results: schedule.optional_results };
  const req = Object.fromEntries([...reqKeys, ...(requirements.destination_id ? ["destination_id"] : [])].map(k=>[k,requirements[k as keyof TripRequest]]));
  req.daily_start_time = `${scheduleClock(requirements.daily_start_time)}:00`;
  req.daily_end_time = `${scheduleClock(requirements.daily_end_time)}:00`;
  const snapshot = { schema_version: requirements.destination_id ? CURRENT_ARCHIVE_VERSION : ARCHIVE_VERSION, requirements:req, schedule: core,
    costs: { tickets: Object.fromEntries(schedule.days.flatMap(d=>d.items.filter(i=>i.kind==="visit").map(i=>[i.place_id!,Object.hasOwn(inputs.tickets,i.place_id!)?inputs.tickets[i.place_id!]:""]))),
      meals:Object.fromEntries(schedule.days.map(d=>[d.date,inputs.meals[d.date]??""])), accommodation:inputs.accommodation, other:inputs.other } };
  if (!isSavedSnapshot(snapshot) || new TextEncoder().encode(JSON.stringify(snapshot)).length>ARCHIVE_BYTES-256) throw new Error("当前快照不完整、不一致或超过存档大小限制，未保存。费用文本每项最多 256 字。");
  return structuredClone(snapshot);
}
export function isSavedSummary(v: unknown, detail = false): v is SavedSummary {
  return record(v) && exact(v,[...summaryKeys,...(detail?["snapshot"]:[])]) && id(v.id) && validSaveName(v.name) && versions.includes(v.schema_version as string) &&
    typeof v.saved_at==="string" && /^\d{4}-\d{2}-\d{2}T.*(?:Z|\+00:00)$/.test(v.saved_at) && Number.isFinite(Date.parse(v.saved_at)) &&
    typeof v.start_date==="string" && typeof v.end_date==="string" && !!scheduleDates(v.start_date,v.end_date,v.schema_version === CURRENT_ARCHIVE_VERSION ? MAX_TRIP_DAYS : LEGACY_MAX_TRIP_DAYS) &&
    (v.transport_mode==="walking" || v.transport_mode==="transit") && ["complete","partial","unscheduled"].includes(v.status as string);
}
export function isSavedDetail(v: unknown): v is SavedDetail {
  if (!record(v)) return false;
  const snapshot=v.snapshot;
  if (!isSavedSummary(v,true) || !isSavedSnapshot(snapshot) || v.schema_version !== snapshot.schema_version) return false;
  const s=snapshot.schedule;
  return v.start_date===s.request.start_date && v.end_date===s.request.end_date && v.transport_mode===s.request.transport_mode && v.status===s.status;
}
const messages: Record<string,string> = { not_found:"没有找到这份历史快照。", invalid_record:"存档版本不受支持或内容损坏，未展示。", invalid_snapshot:"快照未通过服务端一致性校验，未保存。", too_large:"快照超过 2 MiB，未保存；不会截断路线数据。", operation_conflict:"这次保存标识与原内容冲突，请勿将新内容作为旧操作重试。", storage_unavailable:"本机存档暂时无法读写，请稍后主动重试。" };
async function requestArchive(path: string, signal: AbortSignal, operation?: SaveOperation): Promise<unknown> {
  if (signal.aborted) throw new DOMException("取消","AbortError");
  const controller=new AbortController(); let timeout=false;
  const cancel=()=>controller.abort(); signal.addEventListener("abort",cancel,{once:true});
  const timer=setTimeout(()=>{timeout=true;controller.abort();},15000);
  try {
    const response=await fetch(`${API_ENDPOINTS.savedTrips}${path}`,{method:operation?"POST":"GET",signal:controller.signal,
      ...(operation?{headers:{"Content-Type":"application/json"},body:JSON.stringify(operation)}:{})});
    const text=await response.text();
    if (controller.signal.aborted) throw new DOMException("取消","AbortError");
    if (new TextEncoder().encode(text).length>ARCHIVE_BYTES+4096) throw new Error("存档响应过大，未展示。");
    const data:unknown=JSON.parse(text);
    if (!response.ok) throw new Error(record(data)&&record(data.detail)?messages[String(data.detail.code)]??"存档操作失败，请主动重试。":"存档操作失败，请主动重试。");
    return data;
  } catch(error) {
    if(signal.aborted) throw new DOMException("取消","AbortError");
    if(timeout) throw new Error(operation?"保存等待超时，服务端可能已保存。请重试同一份冻结快照。":"读取历史超时，请主动重试。");
    if(error instanceof Error && error.name==="Error") throw error;
    throw new Error(operation?"未能确认保存结果，可能已保存。可重试同一份冻结快照。":"无法读取历史行程。");
  } finally {clearTimeout(timer);signal.removeEventListener("abort",cancel);}
}
export async function saveTrip(operation:SaveOperation,signal:AbortSignal):Promise<SavedDetail> {
  const data=await requestArchive("",signal,operation);
  if(!isSavedDetail(data) || data.name!==operation.name || canonical(data.snapshot)!==canonical(operation.snapshot)) throw new Error("保存响应与冻结快照不一致，请重试同一次操作确认。");
  return data;
}
export async function readTrip(id:string,signal:AbortSignal):Promise<SavedDetail> {
  if(!/^[0-9a-f]{32}$/.test(id)) throw new Error("无效的历史记录标识。");
  const data=await requestArchive(`/${id}`,signal);
  if(!isSavedDetail(data)||data.id!==id) throw new Error("历史快照未通过校验，未展示。");
  return data;
}
export async function listTrips(offset:number,signal:AbortSignal):Promise<SavedList> {
  const data=await requestArchive(`?offset=${offset}&limit=10`,signal);
  if(!record(data)||!exact(data,["items","offset","limit","has_more"])||data.offset!==offset||data.limit!==10||typeof data.has_more!=="boolean"||!Array.isArray(data.items)||data.items.length>10||!data.items.every(v=>isSavedSummary(v))||new Set(data.items.map(v=>v.id)).size!==data.items.length) throw new Error("历史列表未通过校验。");
  return data as SavedList;
}

import { isKnowledgeResponse, type KnowledgeResponse, type VenueId } from "./knowledge";

export const missingLabels = {
  not_in_materials: "所选馆区的已收录资料不足以确认问题中的全部事项。",
  live_availability: "未收录实时剩余名额，不能从静态资料推算。",
  weather_not_collected: "未收录天气资料，无法判断天气情况。",
  current_applicability: "无法确认今天仍适用，请核对馆方最新说明。",
  conflicting_materials: "资料中存在差异，尚不能确定应采用哪种说法，请向馆方核实。",
  other_venue: "所选馆区资料不能支持其他馆区的问题，请重新选择馆区并检索。",
} as const;
export type AnswerRequest = { venue_id: VenueId; question: string; request_id: string; corpus_version: string; corpus_hash: string };
export type AnswerResponse = {
  schema_version: "knowledge-answer-v1"; prompt_version: "knowledge-answer-6d2-v5";
  request: AnswerRequest; retrieval: KnowledgeResponse;
  status: "answered" | "partial" | "insufficient";
  answers: { text: string; evidence_ids: string[] }[];
  missing: (keyof typeof missingLabels)[];
  citations: { evidence_id: string; chunk_id: string; source_id: string; url: string; source_version: string; content_hash: string }[];
  origin: "qwen" | "local_no_match"; model: string | null;
  usage: { input_tokens: number | null; output_tokens: number | null };
};
const exact = (v: unknown, keys: string): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v) && Object.keys(v).sort().join(",") === keys.split(" ").sort().join(",");
const text = (v: unknown, max: number): v is string => typeof v === "string" && v.length > 0 && [...v].length <= max && v === v.trim() && !/[\u0000-\u001f]/.test(v);
const same = (a: unknown, b: unknown): boolean => {
  if (a === b) return true;
  if (!a || !b || typeof a !== "object" || typeof b !== "object" || Array.isArray(a) !== Array.isArray(b)) return false;
  const x = a as Record<string, unknown>, y = b as Record<string, unknown>;
  return Object.keys(x).length === Object.keys(y).length && Object.keys(x).every(k => Object.hasOwn(y, k) && same(x[k], y[k]));
};
export function makeAnswerRequest(source: KnowledgeResponse, id: string): AnswerRequest {
  return { venue_id: source.request.venue_id, question: source.request.question, request_id: id,
    corpus_version: source.corpus_version, corpus_hash: source.corpus_hash };
}
export function isAnswerResponse(v: unknown, request: AnswerRequest, source: KnowledgeResponse): v is AnswerResponse {
  if (!exact(v, "schema_version prompt_version request retrieval status answers missing citations origin model usage") || v.schema_version !== "knowledge-answer-v1" || v.prompt_version !== "knowledge-answer-6d2-v5" || !same(v.request, request)) return false;
  if (!isKnowledgeResponse(v.retrieval, { venue_id: request.venue_id, question: request.question, limit: 5 }) || !same(v.retrieval, source) || v.retrieval.corpus_hash !== request.corpus_hash || v.retrieval.corpus_version !== request.corpus_version) return false;
  if (!Array.isArray(v.answers) || v.answers.length > 3 || !Array.isArray(v.missing) || v.missing.length > 4 || new Set(v.missing).size !== v.missing.length || !v.missing.every(x => typeof x === "string" && Object.hasOwn(missingLabels, x))) return false;
  if (v.status === "answered" ? !v.answers.length || !!v.missing.length : v.status === "partial" ? !v.answers.length || !v.missing.length : v.status === "insufficient" ? !!v.answers.length || !v.missing.length : true) return false;
  const used = new Set<string>(), prose = new Set<string>();
  for (const a of v.answers) {
    if (!exact(a, "text evidence_ids") || !text(a.text, 180) || /https?:\/\/|www\.|[<>]/i.test(a.text) || prose.has(a.text) || !Array.isArray(a.evidence_ids) || !a.evidence_ids.length || a.evidence_ids.length > 5 || new Set(a.evidence_ids).size !== a.evidence_ids.length) return false;
    prose.add(a.text);
    for (const id of a.evidence_ids) { if (typeof id !== "string" || !/^E[1-5]$/.test(id) || !source.matches[Number(id.slice(1))-1]) return false; used.add(id); }
    // Inline numeric references are not owned citations. Never strip and salvage.
    const itemIds = a.evidence_ids;
    if (/\[\s*\d+(?:\s*[,，-]\s*\d+)*\s*\]|【\s*\d+\s*】/.test(a.text) || (a.text.match(/E\d+/g) ?? []).some(id => !itemIds.includes(id))) return false;
  }
  if (!Array.isArray(v.citations) || v.citations.length !== used.size) return false;
  const seen = new Set<string>();
  for (const c of v.citations) {
    if (!exact(c, "evidence_id chunk_id source_id url source_version content_hash") || typeof c.evidence_id !== "string" || !used.has(c.evidence_id) || seen.has(c.evidence_id)) return false;
    seen.add(c.evidence_id); const m = source.matches[Number(c.evidence_id.slice(1))-1];
    if (c.chunk_id !== m.chunk_id || c.source_id !== m.source.id || c.url !== m.source.url || c.source_version !== m.source.content_version || c.content_hash !== m.content_hash) return false;
  }
  if (!exact(v.usage, "input_tokens output_tokens") || ![v.usage.input_tokens, v.usage.output_tokens].every(x => x === null || typeof x === "number" && Number.isInteger(x) && x >= 0 && x <= 1_000_000) || typeof v.usage.output_tokens === "number" && v.usage.output_tokens > 1024) return false;
  if (v.origin === "local_no_match") return !source.matches.length && v.status === "insufficient" && v.model === null && v.usage.input_tokens === null && v.usage.output_tokens === null;
  return v.origin === "qwen" && !!source.matches.length && ["qwen3.7-flash", "qwen3.7-flash-2026-07-15"].includes(String(v.model));
}

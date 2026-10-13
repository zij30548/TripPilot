export const venues = [
  { id: "museum_people", name: "上海博物馆人民广场馆" },
  { id: "museum_east", name: "上海博物馆东馆" },
  { id: "natural", name: "上海自然博物馆" },
] as const;
export type VenueId = typeof venues[number]["id"];
export type KnowledgeRequest = { venue_id: VenueId; question: string; limit: number };
export type KnowledgeSource = {
  id: string; title: string; venue_id: VenueId; url: string; publisher: string;
  kind: "standing" | "temporary" | "historical" | "catalog";
  fetched_at: string; published_at: string | null; updated_at: string | null;
  effective_from: string | null; effective_until: string | null;
  content_version: string; applicability: string; reuse_basis: string;
};
export type KnowledgeMatch = { chunk_id: string; content_hash: string;
  section: { id: string; heading: string; excerpt: string; summary: string };
  source: KnowledgeSource; score: number; matched_terms: string[] };
export type KnowledgeResponse = {
  schema_version: "knowledge-search-v1"; corpus_version: string; corpus_hash: string;
  document_count: number; chunk_count: number; request: KnowledgeRequest;
  venue: { id: VenueId; name: string; aliases: string[] };
  status: "matches" | "no_match"; matches: KnowledgeMatch[];
};
const sources: Record<string, [VenueId, string]> = {
  "museum-west": ["museum_people", "https://www.shanghaimuseum.cn/mu/frontend/pg/service/visit-west"],
  "museum-east": ["museum_east", "https://www.shanghaimuseum.cn/mu/frontend/pg/service/visit-east"],
  "natural-visit": ["natural", "https://www.snhm.org.cn/cgfw/cgzx.htm"],
  "natural-school": ["natural", "https://www.snhm.org.cn/cgfw/ttyy.htm"],
  "natural-education": ["natural", "https://www.snhm.org.cn/jyhd/index.htm"],
  "people-2025": ["museum_people", "https://www.meet-in-shanghai.net/tc/news/the-peoples-square-pavilion-of-the-shanghai-museum-will-be-available-for-trial-visits-without-an-appointment-from-september-1-421453/"],
};
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const exact = (v: unknown, keys: string): v is Record<string, unknown> => object(v) && Object.keys(v).sort().join(",") === keys.split(" ").sort().join(",");
const text = (v: unknown, max = 600, min = 1): v is string => typeof v === "string" && v.length >= min && v.length <= max;
const integer = (v: unknown, min: number, max: number): v is number => typeof v === "number" && Number.isInteger(v) && v >= min && v <= max;
const hash = (v: unknown) => typeof v === "string" && /^[a-f0-9]{64}$/.test(v);
const date = (v: unknown): v is string => typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v) && Number.isFinite(Date.parse(v)) && new Date(v).toISOString().slice(0, 10) === v;
const nullableDate = (v: unknown) => v === null || date(v);
export function isKnowledgeRequest(v: unknown): v is KnowledgeRequest {
  return exact(v, "venue_id question limit") && venues.some(x => x.id === v.venue_id) && text(v.question, 160, 2) && v.question === v.question.trim() && !/[\u0000-\u001f]/.test(v.question) && integer(v.limit, 1, 5);
}
export function isKnowledgeResponse(v: unknown, request: KnowledgeRequest): v is KnowledgeResponse {
  if (!exact(v, "schema_version corpus_version corpus_hash document_count chunk_count request venue status matches") || v.schema_version !== "knowledge-search-v1" || v.corpus_version !== "2026-10-13.1" || !hash(v.corpus_hash) || !integer(v.document_count, 6, 10) || !integer(v.chunk_count, 6, 120)) return false;
  if (!isKnowledgeRequest(v.request) || v.request.venue_id !== request.venue_id || v.request.question !== request.question || v.request.limit !== request.limit) return false;
  if (!exact(v.venue, "id name aliases") || v.venue.id !== request.venue_id || v.venue.name !== venues.find(x => x.id === request.venue_id)?.name || !Array.isArray(v.venue.aliases) || v.venue.aliases.length < 1 || v.venue.aliases.length > 5 || !v.venue.aliases.every(x => text(x))) return false;
  if (!Array.isArray(v.matches) || v.matches.length > request.limit || v.status !== (v.matches.length ? "matches" : "no_match")) return false;
  const ids = new Set<string>();
  let previous = Infinity;
  return v.matches.every(m => {
    if (!exact(m, "chunk_id content_hash section source score matched_terms") || !text(m.chunk_id, 121) || ids.has(m.chunk_id) || !hash(m.content_hash) || typeof m.score !== "number" || !Number.isFinite(m.score) || m.score <= 0 || m.score > previous) return false;
    ids.add(m.chunk_id); previous = m.score;
    if (!exact(m.section, "id heading excerpt summary") || !text(m.section.id, 60) || !/^[a-z][a-z0-9-]*$/.test(m.section.id) || !text(m.section.heading, 100) || !text(m.section.excerpt, 40, 0) || !text(m.section.summary, 360)) return false;
    const s = m.source;
    if (!exact(s, "id title venue_id url publisher kind fetched_at published_at updated_at effective_from effective_until content_version applicability reuse_basis") || !text(s.id, 60) || sources[s.id]?.[0] !== request.venue_id || sources[s.id]?.[1] !== s.url || s.venue_id !== request.venue_id || m.chunk_id !== `${s.id}/${m.section.id}`) return false;
    if (!text(s.title, 100) || !text(s.publisher) || !text(s.applicability) || !text(s.reuse_basis) || s.content_version !== v.corpus_version || !["standing", "temporary", "historical", "catalog"].includes(String(s.kind))) return false;
    if (!text(s.fetched_at, 40) || !/^\d{4}-\d{2}-\d{2}T.+(?:Z|\+00:00)$/.test(s.fetched_at) || !Number.isFinite(Date.parse(s.fetched_at)) || ![s.published_at, s.updated_at, s.effective_from, s.effective_until].every(nullableDate)) return false;
    if (date(s.effective_from) && date(s.effective_until) && s.effective_until < s.effective_from || date(s.published_at) && date(s.updated_at) && s.updated_at < s.published_at) return false;
    if ([s.published_at, s.updated_at].some(x => date(x) && x > (s.fetched_at as string).slice(0, 10))) return false;
    return Array.isArray(m.matched_terms) && m.matched_terms.length > 0 && m.matched_terms.length <= 24 && m.matched_terms.every(x => text(x));
  });
}

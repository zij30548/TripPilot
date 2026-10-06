import { isConfirmedPlace, type Place } from "./place";

export const CANDIDATE_INTERESTS = ["摄影", "Citywalk", "美食", "建筑", "博物馆", "购物"] as const;
export type CandidateInterest = typeof CANDIDATE_INTERESTS[number];
export const CANDIDATE_KEYWORDS: Record<CandidateInterest, string> = {
  摄影: "公园", Citywalk: "步行街", 美食: "餐厅", 建筑: "历史建筑", 博物馆: "博物馆", 购物: "商场",
};
export type RetrievalSource = { interest: CandidateInterest | null; keyword: string };
export type CandidateRequest = {
  accommodation_place: Place;
  must_visit_places: Place[];
  interests: CandidateInterest[];
};
export type Candidate = { place: Place; role: "must_visit" | "optional"; retrieval_sources: RetrievalSource[] };
export type CandidateQueryResult = RetrievalSource & {
  status: "success" | "failed" | "timeout";
  result_count: number;
  message: string | null;
};
export type CandidateResponse = {
  status: "success" | "partial" | "failed";
  queried_at: string;
  keywords: string[];
  queries: CandidateQueryResult[];
  candidates: Candidate[];
};

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function keys(value: Record<string, unknown>, expected: string[]): boolean {
  return Object.keys(value).length === expected.length && expected.every((key) => key in value);
}
export function isCandidateInterest(value: unknown): value is CandidateInterest {
  return typeof value === "string" && CANDIDATE_INTERESTS.some((interest) => interest === value);
}
export function candidateSearches(interests: readonly CandidateInterest[]): RetrievalSource[] {
  const selected = CANDIDATE_INTERESTS.filter((interest) => interests.includes(interest));
  return selected.length ? selected.map((interest) => ({ interest, keyword: CANDIDATE_KEYWORDS[interest] })) :
    [{ interest: null, keyword: "旅游景点" }];
}
export function isCandidateRequest(value: unknown): value is CandidateRequest {
  if (!object(value) || !keys(value, ["accommodation_place", "must_visit_places", "interests"]) ||
      !isConfirmedPlace(value.accommodation_place) || !Array.isArray(value.must_visit_places) ||
      !value.must_visit_places.every(isConfirmedPlace) || !Array.isArray(value.interests) ||
      !value.interests.every(isCandidateInterest)) return false;
  return new Set(value.must_visit_places.map((place) => place.id.trim())).size === value.must_visit_places.length;
}
function samePlace(actual: Place, expected: Place): boolean {
  return actual.id.trim() === expected.id.trim() && actual.name.trim() === expected.name.trim() &&
    actual.address === expected.address && actual.latitude === expected.latitude && actual.longitude === expected.longitude &&
    actual.category === expected.category && actual.source === expected.source;
}

// Validate metadata separately: a confirmed Place stays the exact 5A-1 contract.
export function isCandidateResponse(value: unknown, request: CandidateRequest): value is CandidateResponse {
  if (!object(value) || !keys(value, ["status", "queried_at", "keywords", "queries", "candidates"]) ||
      (value.status !== "success" && value.status !== "partial" && value.status !== "failed") ||
      typeof value.queried_at !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$/.test(value.queried_at) ||
      !Number.isFinite(Date.parse(value.queried_at)) || new Date(value.queried_at).toISOString().slice(0, 19) !== value.queried_at.slice(0, 19) ||
      !Array.isArray(value.queries) || !Array.isArray(value.keywords) ||
      !Array.isArray(value.candidates)) return false;
  const expected = candidateSearches(request.interests);
  if (value.queries.length !== expected.length || value.keywords.length !== expected.length) return false;
  const queries: CandidateQueryResult[] = [];
  for (let index = 0; index < expected.length; index += 1) {
    const query: unknown = value.queries[index];
    const source = expected[index];
    if (!object(query) || !keys(query, ["keyword", "interest", "status", "result_count", "message"]) ||
        query.keyword !== source.keyword || query.interest !== source.interest || value.keywords[index] !== source.keyword ||
        (query.status !== "success" && query.status !== "failed" && query.status !== "timeout") ||
        typeof query.result_count !== "number" || !Number.isInteger(query.result_count) || query.result_count < 0 || query.result_count > 20 ||
        (query.status === "success" ? query.message !== null :
          query.result_count !== 0 || typeof query.message !== "string" || !query.message.trim())) return false;
    queries.push(query as CandidateQueryResult);
  }
  const successCount = queries.filter((query) => query.status === "success").length;
  if (value.status !== (successCount === queries.length ? "success" : successCount ? "partial" : "failed")) return false;
  const ids = new Set<string>();
  const required = new Map(request.must_visit_places.map((place) => [place.id.trim(), place]));
  const seenRequired = new Set<string>();
  const sourceCounts = new Map<string, number>();
  let optionalCount = 0;
  for (const entry of value.candidates) {
    if (!object(entry) || !keys(entry, ["place", "role", "retrieval_sources"]) || !isConfirmedPlace(entry.place) ||
        (entry.role !== "must_visit" && entry.role !== "optional") || !Array.isArray(entry.retrieval_sources)) return false;
    const id = entry.place.id.trim();
    if (ids.has(id)) return false;
    ids.add(id);
    const original = required.get(id);
    if (entry.role === "must_visit") {
      if (!original || !samePlace(entry.place, original)) return false;
      seenRequired.add(id);
    } else {
      if (original || id === request.accommodation_place.id.trim() || !successCount || ++optionalCount > 18 || !entry.retrieval_sources.length) return false;
    }
    const sourceKeys = new Set<string>();
    for (const source of entry.retrieval_sources) {
      if (!object(source) || !keys(source, ["interest", "keyword"])) return false;
      const sourceKey = JSON.stringify([source.interest, source.keyword]);
      const query = queries.find((query) => query.interest === source.interest && query.keyword === source.keyword);
      const count = (sourceCounts.get(sourceKey) ?? 0) + 1;
      if (sourceKeys.has(sourceKey) || !query || query.status !== "success" || count > query.result_count) return false;
      sourceKeys.add(sourceKey);
      sourceCounts.set(sourceKey, count);
    }
  }
  return seenRequired.size === required.size;
}

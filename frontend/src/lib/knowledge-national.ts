/** Versioned local-reference contracts. These do not widen travel destinations. */
export type KnowledgeVenueKind = "museum" | "amusement_park";
export type NationalKnowledgeCity = { id: string; name: string };
export type NationalKnowledgeVenue = { id: string; city_id: string; name: string; kind: KnowledgeVenueKind; aliases: string[] };
export type NationalKnowledgeSourceBinding = { id: string; venue_id: string; url: string; content_version: string; reviewed_text_sha256: string };
export type NationalKnowledgeCatalog = {
  schema_version: "knowledge-catalog-v2"; corpus_version: string; corpus_hash: string;
  method: "cjk-bigram-bm25-national-v1"; document_count: number; chunk_count: number;
  cities: NationalKnowledgeCity[]; venues: NationalKnowledgeVenue[]; sources: NationalKnowledgeSourceBinding[];
};
export type NationalKnowledgeRequest = { city_id: string; venue_id: string; question: string; limit: number; visit_date: string | null };
export type NationalKnowledgeSource = NationalKnowledgeSourceBinding & {
  title: string; publisher: string; kind: "standing" | "temporary" | "historical" | "catalog";
  fetched_at: string; published_at: string | null; updated_at: string | null;
  effective_from: string | null; effective_until: string | null; applicability: string; reuse_basis: string;
};
export type ApplicabilityStatus = "no_visit_date" | "historical_reference" | "outside_declared_range" | "within_declared_range_unverified" | "current_applicability_unknown";
export type NationalKnowledgeMatch = {
  chunk_id: string; content_hash: string;
  section: { id: string; heading: string; excerpt: string; summary: string; topics: string[] };
  source: NationalKnowledgeSource; score: number; matched_terms: string[];
  applicability_check: { status: ApplicabilityStatus; notice: string };
};
export type NationalKnowledgeResponse = {
  schema_version: "knowledge-search-v2"; corpus_version: string; corpus_hash: string;
  method: "cjk-bigram-bm25-national-v1"; document_count: number; chunk_count: number;
  request: NationalKnowledgeRequest; city: NationalKnowledgeCity; venue: NationalKnowledgeVenue;
  status: "matches" | "no_match"; matches: NationalKnowledgeMatch[];
};
const METHOD = "cjk-bigram-bm25-national-v1";
const TOPICS = new Set(["opening", "reservation", "ticket", "children", "accessibility", "luggage", "transport", "safety", "services", "other"]);
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const exact = (value: unknown, keys: string): value is Record<string, unknown> => {
  const fields = keys.split(" ");
  return object(value) && Object.keys(value).length === fields.length && fields.every(field => Object.hasOwn(value, field));
};
const text = (value: unknown, max: number, min = 1): value is string => typeof value === "string" && Array.from(value).length >= min && Array.from(value).length <= max && value === value.trim() && !/[\u0000-\u001f\u007f]/.test(value);
const integer = (value: unknown, min: number, max: number): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= min && value <= max;
const hash = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const version = (value: unknown): value is string => typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$/.test(value);
const id = (value: unknown): value is string => typeof value === "string" && /^[a-z][a-z0-9_-]{0,79}$/.test(value);
const cityId = (value: unknown): value is string => typeof value === "string" && /^\d{6}$/.test(value);
export const isNationalKnowledgeDate = (value: unknown): value is string => typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
const nullableDate = (value: unknown) => value === null || isNationalKnowledgeDate(value);
const strings = (value: unknown, maxCount: number, maxLength: number, minCount = 0): value is string[] => Array.isArray(value) && value.length >= minCount && value.length <= maxCount && value.every(item => text(item, maxLength)) && new Set(value).size === value.length;
function reviewedUrl(value: unknown): value is string {
  if (typeof value !== "string" || value.length > 500 || !/^[\x21-\x7e]+$/.test(value)) return false;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password && !url.port && !url.hash && url.href === value && !value.includes("\\") && url.hostname.includes(".") && !/^\d+(?:\.\d+){3}$/.test(url.hostname) && url.hostname.split(".").every(label => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label));
  } catch { return false; }
}
function city(value: unknown): value is NationalKnowledgeCity {
  return exact(value, "id name") && cityId(value.id) && text(value.name, 80);
}
function venue(value: unknown): value is NationalKnowledgeVenue {
  return exact(value, "id city_id name kind aliases") && id(value.id) && cityId(value.city_id) && text(value.name, 160) && ["museum", "amusement_park"].includes(String(value.kind)) && strings(value.aliases, 10, 160, 1);
}
function sourceBinding(value: unknown): value is NationalKnowledgeSourceBinding {
  return exact(value, "id venue_id url content_version reviewed_text_sha256") && id(value.id) && id(value.venue_id) && reviewedUrl(value.url) && version(value.content_version) && hash(value.reviewed_text_sha256);
}
export function isNationalKnowledgeCatalog(value: unknown): value is NationalKnowledgeCatalog {
  if (!exact(value, "schema_version corpus_version corpus_hash method document_count chunk_count cities venues sources") || value.schema_version !== "knowledge-catalog-v2" || value.method !== METHOD || !version(value.corpus_version) || !hash(value.corpus_hash) || !integer(value.document_count, 1, 500) || !integer(value.chunk_count, 1, 3000)) return false;
  if (!Array.isArray(value.cities) || !value.cities.length || value.cities.length > 100 || !value.cities.every(city) || new Set(value.cities.map(row => row.id)).size !== value.cities.length) return false;
  const cities = value.cities;
  if (!Array.isArray(value.venues) || !value.venues.length || value.venues.length > 200 || !value.venues.every(venue) || new Set(value.venues.map(row => row.id)).size !== value.venues.length || !value.venues.every(row => cities.some(item => item.id === row.city_id)) || !cities.every(row => value.venues instanceof Array && value.venues.some(item => item.city_id === row.id))) return false;
  const venues = value.venues;
  if (!Array.isArray(value.sources) || value.sources.length !== value.document_count || !value.sources.every(sourceBinding) || new Set(value.sources.map(row => row.id)).size !== value.sources.length) return false;
  const sources = value.sources;
  return sources.every(row => venues.some(item => item.id === row.venue_id)) && venues.every(row => sources.some(item => item.venue_id === row.id));
}
export function isNationalKnowledgeRequest(value: unknown, catalog?: NationalKnowledgeCatalog): value is NationalKnowledgeRequest {
  if (!exact(value, "city_id venue_id question limit visit_date") || !cityId(value.city_id) || !id(value.venue_id) || !text(value.question, 160, 2) || value.question !== value.question.trim() || /[\u0000-\u001f\u007f]/.test(value.question) || !integer(value.limit, 1, 5) || !nullableDate(value.visit_date)) return false;
  return !catalog || catalog.venues.some(row => row.id === value.venue_id && row.city_id === value.city_id);
}
function applicableStatus(source: NationalKnowledgeSource, visitDate: string | null): ApplicabilityStatus {
  if (source.kind === "historical") return "historical_reference";
  if (!visitDate) return "no_visit_date";
  if (source.effective_from && visitDate < source.effective_from || source.effective_until && visitDate > source.effective_until) return "outside_declared_range";
  return source.effective_from && source.effective_until ? "within_declared_range_unverified" : "current_applicability_unknown";
}
export function isNationalKnowledgeResponse(value: unknown, request: NationalKnowledgeRequest, catalog: NationalKnowledgeCatalog): value is NationalKnowledgeResponse {
  if (!exact(value, "schema_version corpus_version corpus_hash method document_count chunk_count request city venue status matches") || value.schema_version !== "knowledge-search-v2" || value.method !== METHOD || value.corpus_version !== catalog.corpus_version || value.corpus_hash !== catalog.corpus_hash || value.document_count !== catalog.document_count || value.chunk_count !== catalog.chunk_count || !isNationalKnowledgeRequest(value.request, catalog)) return false;
  const echoed = value.request;
  if (Object.keys(request).some(key => request[key as keyof NationalKnowledgeRequest] !== echoed[key as keyof NationalKnowledgeRequest])) return false;
  const knownCity = catalog.cities.find(row => row.id === request.city_id), knownVenue = catalog.venues.find(row => row.id === request.venue_id);
  if (!knownCity || !knownVenue || !city(value.city) || value.city.id !== knownCity.id || value.city.name !== knownCity.name || !venue(value.venue)) return false;
  const returnedVenue = value.venue;
  if (Object.entries(knownVenue).some(([key, item]) => JSON.stringify(item) !== JSON.stringify(returnedVenue[key as keyof NationalKnowledgeVenue]))) return false;
  if (!Array.isArray(value.matches) || value.matches.length > request.limit || value.status !== (value.matches.length ? "matches" : "no_match")) return false;
  const ids = new Set<string>();
  let previous = Infinity;
  return value.matches.every(match => {
    if (!exact(match, "chunk_id content_hash section source score matched_terms applicability_check") || !text(match.chunk_id, 161) || ids.has(match.chunk_id) || !hash(match.content_hash) || typeof match.score !== "number" || !Number.isFinite(match.score) || match.score <= 0 || match.score > previous) return false;
    ids.add(match.chunk_id); previous = match.score;
    const section = match.section, source = match.source;
    if (!exact(section, "id heading excerpt summary topics") || !id(section.id) || !text(section.heading, 160) || !text(section.excerpt, 80, 0) || !text(section.summary, 700) || !strings(section.topics, 10, 30, 1) || !section.topics.every(topic => TOPICS.has(topic))) return false;
    if (!exact(source, "id title venue_id url publisher kind fetched_at published_at updated_at effective_from effective_until content_version reviewed_text_sha256 applicability reuse_basis") || !id(source.id) || !id(source.venue_id) || source.venue_id !== request.venue_id || !reviewedUrl(source.url) || !text(source.title, 160) || !text(source.publisher, 160) || !["standing", "temporary", "historical", "catalog"].includes(String(source.kind)) || !version(source.content_version) || !hash(source.reviewed_text_sha256) || !text(source.applicability, 700) || !text(source.reuse_basis, 700)) return false;
    const knownSource = catalog.sources.find(row => row.id === source.id);
    if (!knownSource || Object.entries(knownSource).some(([key, item]) => source[key] !== item) || match.chunk_id !== `${source.id}/${section.id}`) return false;
    if (!text(source.fetched_at, 40) || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$/.test(source.fetched_at) || !isNationalKnowledgeDate(source.fetched_at.slice(0, 10)) || !Number.isFinite(Date.parse(source.fetched_at)) || ![source.published_at, source.updated_at, source.effective_from, source.effective_until].every(nullableDate)) return false;
    if (isNationalKnowledgeDate(source.published_at) && isNationalKnowledgeDate(source.updated_at) && source.updated_at < source.published_at || isNationalKnowledgeDate(source.effective_from) && isNationalKnowledgeDate(source.effective_until) && source.effective_until < source.effective_from || [source.published_at, source.updated_at].some(date => isNationalKnowledgeDate(date) && date > String(source.fetched_at).slice(0, 10))) return false;
    if (!strings(match.matched_terms, 24, 160, 1) || !exact(match.applicability_check, "status notice") || match.applicability_check.status !== applicableStatus(source as NationalKnowledgeSource, request.visit_date) || !text(match.applicability_check.notice, 300)) return false;
    return true;
  });
}

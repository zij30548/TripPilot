import type { NationalKnowledgeCatalog, NationalKnowledgeResponse } from "./knowledge-national";

/** Synthetic contract/stale-response material, not newly fetched official data. */
export function nationalCatalogFixture(): NationalKnowledgeCatalog {
  return {
    schema_version: "knowledge-catalog-v2", corpus_version: "controlled-national-v1", corpus_hash: "a".repeat(64), method: "cjk-bigram-bm25-national-v1", document_count: 2, chunk_count: 2,
    cities: [{ id: "310000", name: "上海" }, { id: "110000", name: "北京" }],
    venues: [{ id: "museum_people", city_id: "310000", name: "受控上海馆", kind: "museum", aliases: ["受控上馆"] }, { id: "controlled-park", city_id: "110000", name: "受控北京园", kind: "amusement_park", aliases: ["受控园"] }],
    sources: [{ id: "controlled-shanghai", venue_id: "museum_people", url: "https://www.shanghaimuseum.cn/controlled-reference", content_version: "controlled-v1", reviewed_text_sha256: "b".repeat(64) }, { id: "controlled-beijing", venue_id: "controlled-park", url: "https://www.dpm.org.cn/controlled-reference", content_version: "controlled-v1", reviewed_text_sha256: "c".repeat(64) }],
  };
}
export function nationalResponseFixture(index = 0): NationalKnowledgeResponse {
  const catalog = nationalCatalogFixture(), source = catalog.sources[index], venue = catalog.venues[index];
  return {
    schema_version: "knowledge-search-v2", corpus_version: catalog.corpus_version, corpus_hash: catalog.corpus_hash, method: catalog.method, document_count: catalog.document_count, chunk_count: catalog.chunk_count,
    request: { city_id: venue.city_id, venue_id: venue.id, question: "预约说明", limit: 5, visit_date: null }, city: catalog.cities[index], venue,
    status: "matches", matches: [{ chunk_id: `${source.id}/reservation`, content_hash: "d".repeat(64),
      section: { id: "reservation", heading: "预约条件", excerpt: "受控短摘录", summary: "受控完整摘要：请核对预约对象、条件与例外。", topics: ["reservation"] },
      source: { ...source, title: "受控官方来源", publisher: "受控机构", kind: "standing", fetched_at: "2026-10-24T01:00:00Z", published_at: null, updated_at: null, effective_from: null, effective_until: null, applicability: "资料日期未知，不能视为参观当天已确认。", reuse_basis: "仅用于受控契约与界面测试。" },
      score: 1, matched_terms: ["预约"], applicability_check: { status: "no_visit_date", notice: "尚未指定参观日期，不能确认当天适用。" },
    }],
  };
}

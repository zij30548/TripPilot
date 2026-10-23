import { describe, expect, it } from "vitest";
import { isNationalKnowledgeCatalog, isNationalKnowledgeDate, isNationalKnowledgeRequest, isNationalKnowledgeResponse, type NationalKnowledgeCatalog, type NationalKnowledgeResponse } from "./knowledge-national";
import { nationalCatalogFixture, nationalResponseFixture } from "./knowledge-national.test-fixtures";

describe("national reference v2 strict catalogue and response", () => {
  it("accepts dynamic cities/venue types, reviewed source binding and legacy venue IDs", () => {
    const catalog = nationalCatalogFixture(); expect(isNationalKnowledgeCatalog(catalog)).toBe(true);
    for (const index of [0, 1]) { const value = nationalResponseFixture(index); expect(isNationalKnowledgeResponse(value, value.request, catalog)).toBe(true); }
  });
  it.each([
    (value: NationalKnowledgeCatalog) => Object.assign(value, { extra: 1 }),
    (value: NationalKnowledgeCatalog) => { value.cities.push(value.cities[0]); },
    (value: NationalKnowledgeCatalog) => { value.venues[0].city_id = "999999"; },
    (value: NationalKnowledgeCatalog) => { value.sources[0].venue_id = "unknown"; },
    (value: NationalKnowledgeCatalog) => { value.sources[0].url = "https://evil.invalid:444/"; },
    (value: NationalKnowledgeCatalog) => { value.sources[0].url = "https://user:secret@evil.invalid/"; },
    (value: NationalKnowledgeCatalog) => { value.sources[0].url = "https://official.invalid/reference#script"; },
  ])("rejects unknown fields, broken directory refs and unsafe source URLs (%#)", mutate => {
    const value = nationalCatalogFixture(); mutate(value); expect(isNationalKnowledgeCatalog(value)).toBe(false);
  });
  it("requires an exact chosen venue/city and bounded question, limit and date", () => {
    const request = nationalResponseFixture().request, catalog = nationalCatalogFixture();
    expect(isNationalKnowledgeRequest(request, catalog)).toBe(true);
    for (const patch of [{ city_id: "110000" }, { venue_id: "上博" }, { question: "a" }, { question: "x".repeat(161) }, { question: "预约\n说明" }, { limit: 6 }, { visit_date: "2026-02-30" }, { url: "https://outside.invalid/" }]) expect(isNationalKnowledgeRequest({ ...request, ...patch }, catalog)).toBe(false);
    expect(isNationalKnowledgeDate("2028-02-29")).toBe(true);
    expect(isNationalKnowledgeDate("2026-02-29")).toBe(false);
  });
  it.each([
    (value: NationalKnowledgeResponse) => Object.assign(value, { extra: 1 }),
    (value: NationalKnowledgeResponse) => { value.corpus_hash = "f".repeat(64); },
    (value: NationalKnowledgeResponse) => { value.request.visit_date = "2026-11-01"; },
    (value: NationalKnowledgeResponse) => { value.venue.city_id = "110000"; },
    (value: NationalKnowledgeResponse) => { value.matches[0].source.url = "https://valid-but-not-reviewed.invalid/"; },
    (value: NationalKnowledgeResponse) => { value.matches[0].source.venue_id = "controlled-park"; },
    (value: NationalKnowledgeResponse) => { value.matches[0].source.reviewed_text_sha256 = "e".repeat(64); },
    (value: NationalKnowledgeResponse) => { value.matches[0].chunk_id = "another/reservation"; },
    (value: NationalKnowledgeResponse) => { value.matches[0].source.fetched_at = "2026-02-30T00:00:00Z"; },
    (value: NationalKnowledgeResponse) => { value.matches[0].source.updated_at = "2027-01-01"; },
    (value: NationalKnowledgeResponse) => { value.matches[0].section.topics = ["executable"]; },
    (value: NationalKnowledgeResponse) => { value.matches[0].section.summary = "x".repeat(701); },
    (value: NationalKnowledgeResponse) => { value.matches[0].score = Infinity; },
    (value: NationalKnowledgeResponse) => { value.matches.push(value.matches[0]); },
    (value: NationalKnowledgeResponse) => { value.status = "no_match"; },
    (value: NationalKnowledgeResponse) => { value.matches[0].applicability_check.status = "current_applicability_unknown"; },
  ])("rejects schema/version/reference/date/order or temporal contradictions (%#)", mutate => {
    const value = nationalResponseFixture(); mutate(value);
    expect(isNationalKnowledgeResponse(value, nationalResponseFixture().request, nationalCatalogFixture())).toBe(false);
  });
  it("preserves historical status even without a visit date; date bounds do not certify admission", () => {
    const catalog = nationalCatalogFixture(), value = nationalResponseFixture(), match = value.matches[0];
    match.source.kind = "historical"; match.applicability_check.status = "historical_reference";
    expect(isNationalKnowledgeResponse(value, value.request, catalog)).toBe(true);
    match.source.kind = "temporary"; match.source.effective_from = "2026-11-01"; match.source.effective_until = "2026-11-03";
    value.request.visit_date = "2026-11-02"; match.applicability_check.status = "within_declared_range_unverified";
    expect(isNationalKnowledgeResponse(value, value.request, catalog)).toBe(true);
    value.request.visit_date = "2026-11-04"; match.applicability_check.status = "outside_declared_range";
    expect(isNationalKnowledgeResponse(value, value.request, catalog)).toBe(true);
    match.source.effective_until = null; match.applicability_check.status = "current_applicability_unknown";
    expect(isNationalKnowledgeResponse(value, value.request, catalog)).toBe(true);
  });
  it("accepts a truthful empty result without inventing a source or answer", () => {
    const value = nationalResponseFixture(); value.matches = []; value.status = "no_match";
    expect(isNationalKnowledgeResponse(value, value.request, nationalCatalogFixture())).toBe(true);
  });
});

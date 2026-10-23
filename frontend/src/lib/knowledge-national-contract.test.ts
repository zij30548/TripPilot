import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { isNationalKnowledgeCatalog, isNationalKnowledgeResponse, type NationalKnowledgeCatalog, type NationalKnowledgeResponse } from "./knowledge-national";

const backend = resolve(process.cwd(), "../backend");
// Actual Python models and current checked-in local corpus; no HTTP or model.
const fixture: { catalog: NationalKnowledgeCatalog; responses: NationalKnowledgeResponse[] } = JSON.parse(execFileSync(resolve(backend, ".venv/bin/python"), ["-B", "-c", `import json
from datetime import date
from tooling.travel.knowledge_national import build_national_index
from core.schemas.knowledge_national import NationalKnowledgeRequest
index = build_national_index()
catalog = index.catalog()
responses = []
for venue in catalog.venues:
    for question, visit_date in [("预约开放门票儿童服务", None), ("没有收录的火星轨道交通", date(2026, 11, 1))]:
        responses.append(index.search(NationalKnowledgeRequest(city_id=venue.city_id, venue_id=venue.id, question=question, visit_date=visit_date)).model_dump(mode="json"))
print(json.dumps({"catalog":catalog.model_dump(mode="json"),"responses":responses}, ensure_ascii=False))`], { cwd: backend, encoding: "utf8", timeout: 10_000 }));

describe("actual Python → TypeScript national reference v2 contract", () => {
  it("validates every reviewed city and venue without broadening legacy v1", () => {
    expect(isNationalKnowledgeCatalog(fixture.catalog)).toBe(true);
    expect(new Set(fixture.catalog.venues.map(venue => venue.city_id)).size).toBe(fixture.catalog.cities.length);
    expect(fixture.catalog.venues.some(venue => venue.kind === "amusement_park")).toBe(true);
    for (const response of fixture.responses) expect(isNationalKnowledgeResponse(response, response.request, fixture.catalog), `${response.venue.id}/${response.request.question}`).toBe(true);
  });
  it("preserves source whitelist binding, missing dates, coverage warnings and explicit no-match", () => {
    expect(fixture.responses.some(response => response.status === "no_match")).toBe(true);
    const matches = fixture.responses.flatMap(response => response.matches);
    expect(matches.length).toBeGreaterThan(0);
    expect(matches.some(match => match.source.published_at === null)).toBe(true);
    expect(matches.every(match => fixture.catalog.sources.some(source => source.id === match.source.id && source.url === match.source.url))).toBe(true);
    for (const response of fixture.responses) {
      if (!response.matches.length) continue;
      const changed = structuredClone(response); changed.matches[0].source.url += "?unreviewed=1";
      expect(isNationalKnowledgeResponse(changed, response.request, fixture.catalog)).toBe(false);
    }
  });
});

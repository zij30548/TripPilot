import { describe, expect, it } from "vitest";
import { assistantPlanningKey, prepareAssistantPlanning } from "./assistant-planning";
import { assistantPlanningFixture } from "./assistant-planning.test-fixtures";
import { DESTINATION_IDS } from "./destinations";
import { conversationFixture, OTHER_CHAT_ID } from "./chat-test-fixtures";
import { placesConversation, placesFixture, placeFixture } from "./chat-places-test-fixtures";

describe("confirmed chat input to existing planner (pure controlled fixtures)", () => {
  it.each(DESTINATION_IDS)("maps %s slug/adcode to unchanged strict TripRequest and default settings", city => {
    const { conversation, places } = assistantPlanningFixture(city);
    for (const id of [city, conversation.requirements.destination_id!]) {
      const current = { ...conversation, requirements: { ...conversation.requirements, destination_id: id } };
      const inputs = prepareAssistantPlanning(current, places);
      expect(inputs.request.destination_id).toBe(city);
      expect(inputs.request.accommodation_place).toEqual(places.accommodation);
      expect(inputs.request.must_visit_places).toEqual(places.required_places);
      expect(inputs.request.must_visit).toEqual(places.required_places.map(p => p.name));
      expect(inputs.request.daily_start_time).toBe("09:00"); expect(inputs.request.daily_end_time).toBe("18:00");
      expect(inputs.settings.stays[places.required_places[0].id]).toEqual({ value: "60", source: "default" });
      expect(inputs.settings.lunch).toEqual({ enabled: true, start: "12:00", end: "13:00" });
      expect(inputs.costs).toEqual({ tickets: {}, meals: {}, accommodation: "", other: "" });
    }
  });
  it("preserves transit, no-required input, user window and POI identity without sharing mutable objects", () => {
    const { conversation, places } = assistantPlanningFixture();
    conversation.requirements.transport_mode = "transit"; places.required_places = [];
    const before = structuredClone({ conversation, places });
    const inputs = prepareAssistantPlanning(conversation, places, { start: "08:30", end: "20:00" });
    expect(inputs.settings.transportMode).toBe("transit"); expect(inputs.request.must_visit_places).toEqual([]);
    expect(inputs.request.daily_start_time).toBe("08:30");
    inputs.request.accommodation_place!.name = "local edit";
    expect({ conversation, places }).toEqual(before);
  });
  it("refuses other catalog cities, stale/version/ownership mismatches, absent or wrong-city confirmations", () => {
    expect(() => prepareAssistantPlanning(placesConversation(), placesFixture({ accommodation: placeFixture() }))).toThrow("仅支持地点准备");
    const { conversation, places } = assistantPlanningFixture();
    for (const invalid of [null, { ...places, stale: true }, { ...places, intent_revision: 1 }, { ...places, conversation_id: OTHER_CHAT_ID },
      { ...places, accommodation: null }, { ...places, accommodation: placeFixture() }, { ...places, city_id: "320500" }]) {
      expect(() => prepareAssistantPlanning(conversation, invalid)).toThrow();
    }
    for (const field of ["start_date", "end_date", "travelers", "budget_cny", "transport_mode"] as const) {
      expect(() => prepareAssistantPlanning({ ...conversation, requirements: { ...conversation.requirements, [field]: null } }, places)).toThrow();
    }
  });
  it("safe cent conversion rejects subcent/overflow and binary-number round-trip loss, never truncates", () => {
    const { conversation, places } = assistantPlanningFixture();
    for (const value of ["0", "1.001", "NaN", "90071992547409.91", "90071992547409.92"]) {
      expect(() => prepareAssistantPlanning({ ...conversation, requirements: { ...conversation.requirements, budget_cny: value } }, places)).toThrow();
    }
    expect(prepareAssistantPlanning({ ...conversation, requirements: { ...conversation.requirements, budget_cny: "3000.01" } }, places).request.budget).toBe(3000.01);
  });
  it("strict window/date limits, confirmed-place list and city rules remain unchanged", () => {
    const { conversation, places } = assistantPlanningFixture();
    for (const window of [{ start: "18:00", end: "09:00" }, { start: "09:00:03", end: "18:00" }, { start: "invalid", end: "18:00" }]) {
      expect(() => prepareAssistantPlanning(conversation, places, window)).toThrow();
    }
    const week = { ...conversation, requirements: { ...conversation.requirements, end_date: "2026-10-16" } };
    expect(prepareAssistantPlanning(week, places).request.end_date).toBe("2026-10-16");
    expect(() => prepareAssistantPlanning({ ...week, requirements: { ...week.requirements, end_date: "2026-10-17" } }, places)).toThrow();
    expect(() => prepareAssistantPlanning(conversation, { ...places, required_places: [places.required_places[0], places.required_places[0]] })).toThrow();
    for (const issue of ["ambiguous", "unsupported_scope", "unsupported_action"] as const) {
      expect(() => prepareAssistantPlanning({ ...conversation, issues: [issue] }, places)).toThrow("不会静默");
    }
    expect(prepareAssistantPlanning({ ...conversation, issues: ["unimplemented_preference"] }, places).request.destination_id).toBe("hangzhou");
  });
  it("source identity excludes ordinary chat revisions and search batches but includes actual requirement/confirmed POI changes", () => {
    const { conversation, places } = assistantPlanningFixture(), window = { start: "09:00", end: "18:00" };
    const before = assistantPlanningKey(conversation, places, window);
    expect(assistantPlanningKey({ ...conversation, revision: 3, messages: conversationFixture().messages }, { ...places, intent_revision: 3, revision: 5 }, window)).toBe(before);
    expect(assistantPlanningKey({ ...conversation, requirements: { ...conversation.requirements, travelers: 3 } }, places, window)).not.toBe(before);
    expect(assistantPlanningKey(conversation, { ...places, required_places: [] }, window)).not.toBe(before);
    expect(assistantPlanningKey(conversation, places, { ...window, end: "19:00" })).not.toBe(before);
  });
});

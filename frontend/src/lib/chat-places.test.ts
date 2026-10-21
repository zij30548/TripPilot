import { describe, expect, it, vi } from "vitest";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { CHAT_CITIES, chatCity, chatRegionBelongsTo, isChatCityId, resolveChatCity } from "./chat-cities";
import { isPlacesWorkspace, requestPlaces, type PlacesRequest } from "./chat-places";
import { CHAT_ID, OP_ID } from "./chat-test-fixtures";
import { batchFixture, placeFixture, placesFixture, succeeded, SUZHOU } from "./chat-places-test-fixtures";

const reply = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
describe("chat-only city and POI contracts", () => {
  it("uses explicit directory membership, preserves old slugs, and never guesses city from a substring", () => {
    expect(CHAT_CITIES.length).toBe(369);
    for (const city of CHAT_CITIES) expect(isChatCityId(city.id)).toBe(true);
    expect(chatCity("shanghai")?.id).toBe("310000");
    expect(resolveChatCity(" 苏州市 ")?.id).toBe(SUZHOU);
    expect(resolveChatCity("苏州和上海")).toBeNull();
    expect(resolveChatCity("苏州附近")).toBeNull();
    expect(isChatCityId("999999")).toBe(false);
    expect(chatCity("澳门")).toBeNull();
  });
  it("does not turn model city aliases into a POI ownership certificate", () => {
    const region = placeFixture().region;
    expect(chatRegionBelongsTo(region, SUZHOU)).toBe(true);
    for (const changed of [{ ...region, cityname: "上海市" }, { ...region, citycode: "021" }, { ...region, adcode: "320599" }, { adcode: "320508" }, { ...region, extra: true }]) {
      expect(chatRegionBelongsTo(changed, SUZHOU)).toBe(false);
    }
  });
  it("checks the actual Python-generated workspace and city JSON across the directory", () => {
    const backend = resolve(process.cwd(), "../backend");
    const values = JSON.parse(execFileSync(resolve(backend, ".venv/bin/python"), ["-B", "-c", `
import json
from core.chat_cities import CITIES
from core.schemas.chat_places import ChatPlacesWorkspace
items = []
for city_id in CITIES:
    items.append(ChatPlacesWorkspace(conversation_id="${CHAT_ID}",intent_revision=2,revision=0,city_id=city_id).model_dump(mode="json"))
print(json.dumps(items))
`], { cwd: backend, encoding: "utf8", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", QWEN_API_KEY: "" } }));
    expect(values).toHaveLength(CHAT_CITIES.length);
    for (const value of values) expect(isPlacesWorkspace(value)).toBe(true);
    const projections = JSON.parse(execFileSync(resolve(backend, ".venv/bin/python"), ["-B", "-m", "tests.export_chat_places_contract"],
      { cwd: backend, encoding: "utf8", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", QWEN_API_KEY: "" } }));
    expect(projections).toHaveLength(16);
    for (const value of projections) expect(isPlacesWorkspace(value)).toBe(true);
    expect(isPlacesWorkspace(placesFixture({ batch: batchFixture(), required_places: [placeFixture()] }))).toBe(true);
  });
  it("rejects unknown fields, stale batches, malformed identities and cross-city snapshots", () => {
    const base = placesFixture({ batch: batchFixture() });
    for (const change of [{ owner_id: "forged" }, { revision: -1 }, { city_id: "999999" }, { stale: true }, { required_places: [placeFixture(), placeFixture()] },
      { batch: { ...batchFixture(), city_id: "310000" } }, { accommodation: { ...placeFixture(), region: { ...placeFixture().region, cityname: "上海市" } } },
      { batch: { ...batchFixture(), places: [{ ...placeFixture(), raw_model_text: "x" }] } }, { schema_version: "next" }, { clarification: "model_will_book" }]) {
      expect(isPlacesWorkspace({ ...base, ...change })).toBe(false);
    }
  });
});
describe("single same-origin operation transport", () => {
  it("binds the exact response identity and never submits client Place contents", async () => {
    const command: PlacesRequest = { action: "confirm", operation_id: OP_ID, expected_revision: 2, expected_intent_revision: 2,
      batch_id: batchFixture().id, place_id: placeFixture().id, role: "required" };
    const fetcher = vi.fn().mockResolvedValue(reply(placesFixture({ revision: 3, required_places: [placeFixture()], last_operation: succeeded() })));
    vi.stubGlobal("fetch", fetcher);
    await expect(requestPlaces(CHAT_ID, new AbortController().signal, command)).resolves.toBeTruthy();
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe(`/api/chat/conversations/${CHAT_ID}/places`);
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual(command);
    expect(fetcher.mock.calls[0][1].credentials).toBe("same-origin");
    fetcher.mockResolvedValueOnce(reply(placesFixture({ last_operation: succeeded(CHAT_ID) })));
    await expect(requestPlaces(CHAT_ID, new AbortController().signal, command)).rejects.toThrow("不属于本次");
    expect(fetcher).toHaveBeenCalledTimes(2);
    fetcher.mockResolvedValueOnce(reply(placesFixture({ revision: 3, intent_revision: 1, last_operation: succeeded() })));
    await expect(requestPlaces(CHAT_ID, new AbortController().signal, command)).rejects.toThrow("需求版本");
    expect(fetcher).toHaveBeenCalledTimes(3);
  });
  it("rejects a cancellation before dispatch, raw error text and an oversized response without retry", async () => {
    const controller = new AbortController(); controller.abort();
    const fetcher = vi.fn().mockResolvedValue(reply({ detail: { code: "chat_places_revision_conflict", message: "PRIVATE_RAW_BODY" } }, 409));
    vi.stubGlobal("fetch", fetcher);
    await expect(requestPlaces(CHAT_ID, controller.signal)).rejects.toThrow(); expect(fetcher).not.toHaveBeenCalled();
    await expect(requestPlaces(CHAT_ID, new AbortController().signal)).rejects.toThrow("版本");
    fetcher.mockResolvedValueOnce(new Response("{}", { headers: { "content-length": String(384 * 1024 + 1) } }));
    await expect(requestPlaces(CHAT_ID, new AbortController().signal)).rejects.toThrow("超限");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});

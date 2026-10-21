import { conversationFixture, CHAT_ID, OP_ID } from "./chat-test-fixtures";
import type { ChatPlace, PlacesWorkspace } from "./chat-places";

// Controlled fixtures, never claimed to be AMap search results.
export const SUZHOU = "320500";
export const BATCH_ID = "40000000-0000-4000-8000-000000000001";
export const placeFixture = (id = "controlled-a", name = "受控景点"): ChatPlace => ({ id, name, address: "受控测试地址", latitude: 31.32,
  longitude: 120.62, category: "受控类型", source: "amap", region: { cityname: "苏州市", citycode: "0512", adcode: "320508" } });
export const placesFixture = (patch: Partial<PlacesWorkspace> = {}): PlacesWorkspace => ({ schema_version: "chat-places-v1", conversation_id: CHAT_ID,
  intent_revision: 2, revision: 0, city_id: SUZHOU, accommodation: null, required_places: [], batch: null, last_operation: null,
  clarification: null, proposed_city_id: null, request_text: null, stale: false, ...patch });
export const batchFixture = (places = [placeFixture()]) => ({ id: BATCH_ID, city_id: SUZHOU, keyword: "受控景点", role: "required" as const,
  queried_at: "2026-10-21T00:00:00Z", places });
export const succeeded = (operation_id = OP_ID) => ({ operation_id, state: "succeeded" as const, error_code: null });
export const placesConversation = () => conversationFixture({ requirements: { ...conversationFixture().requirements, destination_id: SUZHOU } });

import { conversationFixture } from "./chat-test-fixtures";
import { placesFixture } from "./chat-places-test-fixtures";
import { cityRequest } from "./destination.test-fixtures";
import { tripRequest } from "./weather-schedule-check.test-fixtures";
import { destination, type DestinationId } from "./destinations";
import type { ChatPlace } from "./chat-places";

/** Synthetic controlled input, not live upstream evidence. */
export function assistantPlanningFixture(city: DestinationId = "hangzhou") {
  const request = cityRequest(tripRequest(), city), definition = destination(city);
  const conversation = conversationFixture({ requirements: {
    ...conversationFixture().requirements, destination_id: definition.adcode,
    start_date: request.start_date, end_date: request.end_date, budget_cny: String(request.budget), travelers: request.travelers,
    accommodation_hint: request.accommodation_place!.name, required_place_hints: request.must_visit,
    transport_mode: "walking", preferences: [],
  }, confirmed_revision: 2, issues: [] });
  const places = placesFixture({ city_id: definition.adcode, accommodation: structuredClone(request.accommodation_place) as ChatPlace,
    required_places: structuredClone(request.must_visit_places) as ChatPlace[] });
  return { conversation, places };
}

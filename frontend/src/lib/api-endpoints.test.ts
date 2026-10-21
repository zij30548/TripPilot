import { afterEach, describe, expect, it, vi } from "vitest";
import { AMAP_SERVICE_PATH, API_ENDPOINTS } from "./api-endpoints";

afterEach(() => vi.unstubAllEnvs());

describe("fixed same-origin browser endpoints", () => {
  it("covers every business client, including demo and disabled knowledge answers", () => {
    expect(API_ENDPOINTS).toEqual({
      authSession: "/api/auth/session", authLogin: "/api/auth/login", authRegister: "/api/auth/register", authLogout: "/api/auth/logout",
      chatConversations: "/api/chat/conversations",
      chatCities: "/api/chat/cities",
      usage: "/api/usage",
      places: "/api/places/search", candidates: "/api/places/candidates", schedule: "/api/trips/schedule-preview",
      weather: "/api/weather/forecast", explanation: "/api/trips/schedule-explanation",
      adjustment: "/api/trips/adjustment-intent", itineraryQuery: "/api/trips/itinerary-query",
      photo: "/api/media/landmark", video: "/api/media/video-frames", savedTrips: "/api/saved-trips",
      knowledgeSearch: "/api/knowledge/search", knowledgeAnswer: "/api/knowledge/answer",
      walking: "/api/routes/walking", transit: "/api/routes/transit", plan: "/api/trips/plan",
    });
    expect(AMAP_SERVICE_PATH).toBe("/_AMapService");
    expect(Object.isFrozen(API_ENDPOINTS)).toBe(true);
  });

  it.each(["https://travel.example", "http://127.0.0.1:3417", "https://travel.example:8443"])("resolves only to the visitor's page origin %s", origin => {
    for (const path of [...Object.values(API_ENDPOINTS), AMAP_SERVICE_PATH]) {
      expect(path.startsWith("/") && !path.startsWith("//")).toBe(true);
      expect(new URL(path, origin).origin).toBe(origin);
      expect(path).not.toMatch(/localhost|127\.0\.0\.1|https?:/);
    }
  });

  it("cannot be retargeted by public or server environment values in a browser module", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "https://untrusted.example");
    vi.stubEnv("TRIPPILOT_BACKEND_ORIGIN", "https://private.example");
    vi.resetModules();
    const endpoints = await import("./api-endpoints");
    expect(endpoints.API_ENDPOINTS).toEqual(API_ENDPOINTS);
    expect(endpoints.AMAP_SERVICE_PATH).toBe(AMAP_SERVICE_PATH);
  });
});

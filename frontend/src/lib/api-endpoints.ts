// Browser requests always stay on the page's origin. The Next.js server alone
// chooses the trusted backend target; public environment values cannot override it.
export const API_ENDPOINTS = Object.freeze({
  workspaceSession: "/api/workspace/session",
  chatConversations: "/api/chat/conversations",
  chatCities: "/api/chat/cities",
  usage: "/api/usage",
  places: "/api/places/search",
  candidates: "/api/places/candidates",
  schedule: "/api/trips/schedule-preview",
  weather: "/api/weather/forecast",
  explanation: "/api/trips/schedule-explanation",
  adjustment: "/api/trips/adjustment-intent",
  itineraryQuery: "/api/trips/itinerary-query",
  photo: "/api/media/landmark",
  video: "/api/media/video-frames",
  savedTrips: "/api/saved-trips",
  knowledgeSearch: "/api/knowledge/search",
  knowledgeAnswer: "/api/knowledge/answer",
  walking: "/api/routes/walking",
  transit: "/api/routes/transit",
  plan: "/api/trips/plan",
} as const);

export const AMAP_SERVICE_PATH = "/_AMapService";

// Controlled administrative metadata, never a real AMap verification.
import { destination, type DestinationId } from "./destinations";
import type { Place } from "@/types/place";
import type { TripRequest } from "@/types/trip";
import type { CandidateResponse } from "@/types/candidates";
import type { WeatherForecastResponse } from "@/types/weather";
export function located(p: Place, id: DestinationId = "shanghai"): Place {
  const d = destination(id);
  return { ...structuredClone(p), region: { citycode: d.citycode, cityname: d.official_name, adcode: d.district_adcodes[1] } };
}
export function cityRequest(r: TripRequest, id: DestinationId = "shanghai"): TripRequest {
  return { ...structuredClone(r), destination_id: id, accommodation_place: r.accommodation_place ? located(r.accommodation_place, id) : null, must_visit_places: (r.must_visit_places ?? []).map(p => located(p, id)) };
}
export function cityCandidates(r: CandidateResponse, id: DestinationId = "shanghai"): CandidateResponse {
  return { ...structuredClone(r), destination_id: id, candidates: r.candidates.map(c => ({ ...c, place: located(c.place, id) })) };
}
export function cityWeather(r: WeatherForecastResponse, id: DestinationId = "shanghai"): WeatherForecastResponse {
  return { ...structuredClone(r), request: { ...r.request, destination_id: id }, city: destination(id).official_name, adcode: destination(id).adcode };
}

import definitions from "../../../shared/destinations.json";

export const DESTINATION_IDS = ["shanghai", "hangzhou", "beijing", "guangzhou", "chengdu", "xian"] as const;
export type DestinationId = typeof DESTINATION_IDS[number];
export type Destination = { id: DestinationId; name: string; official_name: string; adcode: string; citycode: string; district_level: "province" | "city"; district_adcodes: string[] };
export const DESTINATIONS = definitions as Destination[];
export function isDestinationId(value: unknown): value is DestinationId { return DESTINATION_IDS.some(id => id === value); }
export function destination(value: DestinationId): Destination {
  const result = DESTINATIONS.find(d => d.id === value);
  if (!result) throw new Error("请选择支持的目的地。");
  return result;
}
export type PlaceRegion = { citycode: string; cityname: string; adcode: string };
export function isRegion(value: unknown): value is PlaceRegion {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const r = value as Record<string, unknown>;
  return Object.keys(r).length === 3 && ["citycode", "cityname", "adcode"].every(k => typeof r[k] === "string") &&
    DESTINATIONS.some(d => regionBelongsTo(r as PlaceRegion, d.id));
}
export function regionBelongsTo(region: PlaceRegion | undefined, id: DestinationId): boolean {
  const d = destination(id);
  return !!region && region.citycode === d.citycode && [d.name, d.official_name].includes(region.cityname) && d.district_adcodes.includes(region.adcode);
}

// Current confirmed inputs, not the source archive's version, determine the next action.
// This uses the existing region contract; it does not independently verify a POI.
export function needsDestinationPlaceConfirmation(request: {
  destination_id?: DestinationId;
  accommodation_place?: { region?: PlaceRegion } | null;
  must_visit_places?: { region?: PlaceRegion }[];
}): boolean {
  const city = request.destination_id;
  return !city || !request.accommodation_place ||
    [request.accommodation_place, ...request.must_visit_places ?? []].some(p => !regionBelongsTo(p.region, city));
}

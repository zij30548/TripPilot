import catalog from "../../../shared/chat-cities.json";
import legacy from "../../../shared/destinations.json";

// Separate from the six-city scheduling contract. Membership is a catalog lookup,
// never an arbitrary string, name substring or guessed administrative prefix.
export type ChatCityId = string;
export type ChatCity = (typeof catalog.cities)[number];
export const CHAT_CITIES: readonly ChatCity[] = catalog.cities;
const cities = new Map(CHAT_CITIES.map(city => [city.id, city]));
const oldIds = new Map(legacy.map(city => [city.id, city.adcode]));
export function chatCity(id: string | null): ChatCity | null {
  return id ? cities.get(oldIds.get(id) ?? id) ?? null : null;
}
export function isChatCityId(value: unknown): value is ChatCityId {
  return typeof value === "string" && chatCity(value) !== null;
}
export function resolveChatCity(text: string): ChatCity | null {
  const query = text.trim().toLowerCase();
  const matches = CHAT_CITIES.filter(city => city.id === query || city.aliases.some(alias => alias.toLowerCase() === query));
  return matches.length === 1 ? matches[0] : null;
}
export function chatCityOptions(text: string): readonly ChatCity[] {
  const query = text.trim().toLowerCase();
  return CHAT_CITIES.filter(city => !query || city.aliases.some(alias => alias.toLowerCase().includes(query)) || city.province_name.includes(query)).slice(0, 20);
}
export function chatRegionBelongsTo(region: unknown, cityId: string): boolean {
  const city = cities.get(cityId);
  if (!city || !region || typeof region !== "object" || Array.isArray(region)) return false;
  const r = region as Record<string, unknown>;
  return Object.keys(r).length === 3 && ["citycode", "cityname", "adcode"].every(key => Object.hasOwn(r, key)) &&
    r.citycode === city.citycode && [city.name, city.official_name].includes(r.cityname as string) &&
    typeof r.adcode === "string" && city.district_adcodes.includes(r.adcode);
}

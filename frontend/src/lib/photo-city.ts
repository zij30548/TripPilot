import { DESTINATIONS, type DestinationId } from "./destinations";

// Photo clues are unverified model text. These aliases only normalize language;
// never use them for AMap administrative ownership or confirmed Place validation.
const englishAliases: Record<DestinationId, readonly string[]> = {
  shanghai: ["Shanghai"],
  hangzhou: ["Hangzhou"],
  beijing: ["Beijing"],
  guangzhou: ["Guangzhou"],
  chengdu: ["Chengdu"],
  xian: ["Xian", "Xi'an", "Xi’an"],
};

export const PHOTO_CITY_ALIASES = DESTINATIONS.map(city => ({
  id: city.id,
  aliases: [city.name, city.official_name, ...englishAliases[city.id]],
}));

const cityByAlias = new Map<string, DestinationId>();
for (const { id, aliases } of PHOTO_CITY_ALIASES) {
  for (const alias of aliases) {
    const key = alias.trim().toLowerCase();
    if (!key || cityByAlias.has(key)) throw new Error("照片城市别名必须唯一且非空。");
    cityByAlias.set(key, id);
  }
}

export function resolvePhotoCity(value: string | null): DestinationId | null {
  return value === null ? null : cityByAlias.get(value.trim().toLowerCase()) ?? null;
}

import { describe, expect, it } from "vitest";
import { DESTINATIONS, DESTINATION_IDS, regionBelongsTo } from "./destinations";
import { PHOTO_CITY_ALIASES, resolvePhotoCity } from "./photo-city";

describe("photo-only exact city aliases", () => {
  it("covers exactly the existing six cities with no duplicate alias ownership", () => {
    expect(PHOTO_CITY_ALIASES.map(row => row.id)).toEqual([...DESTINATION_IDS]);
    const aliases = PHOTO_CITY_ALIASES.flatMap(row => row.aliases.map(a => a.trim().toLowerCase()));
    expect(new Set(aliases).size).toBe(aliases.length);
  });
  it.each(PHOTO_CITY_ALIASES)("resolves every explicit Chinese/English alias of $id", ({id, aliases}) => {
    for (const text of aliases) {
      expect(resolvePhotoCity(text)).toBe(id);
      expect(resolvePhotoCity(` \t${text.toUpperCase()}\n `)).toBe(id);
      expect(resolvePhotoCity(text.toLowerCase())).toBe(id);
    }
  });
  it.each(["Xian", "Xi'an", "Xi’an"])("supports the explicit Xi'an spelling %s", text => {
    expect(resolvePhotoCity(text)).toBe("xian");
  });
  it.each([null, "", " \t ", "unknown", "深圳", "Shenzhen", "New York", "Shanghai / Beijing", "上海杭州", "上海 Shanghai", "Shanghai City", "Peking", "Xi an", "Ｘｉａｎ", "Shang hai", "我在上海", "__proto__"])("rejects unknown, mixed or non-listed city %s", text => {
    expect(resolvePhotoCity(text)).toBeNull();
  });
  it("does not relax real POI administrative ownership", () => {
    for (const city of DESTINATIONS) {
      const region = {citycode:city.citycode,cityname:city.official_name,adcode:city.district_adcodes[0]};
      expect(regionBelongsTo(region, city.id)).toBe(true);
      expect(regionBelongsTo({...region,cityname:PHOTO_CITY_ALIASES.find(row=>row.id===city.id)!.aliases[2]},city.id)).toBe(false);
    }
  });
});

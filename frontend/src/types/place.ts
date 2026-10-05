export type Place = {
  id: string;
  name: string;
  address: string | null;
  latitude: number;
  longitude: number;
  category: string | null;
  source: "amap";
};

export function hasValidCoordinates(place: Pick<Place, "latitude" | "longitude">): boolean {
  return Number.isFinite(place.latitude) && place.latitude >= -90 && place.latitude <= 90 &&
    Number.isFinite(place.longitude) && place.longitude >= -180 && place.longitude <= 180;
}

export function isPlace(value: unknown): value is Place {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const place = value as Record<string, unknown>;
  return typeof place.id === "string" && place.id.trim().length > 0 &&
    typeof place.name === "string" && place.name.trim().length > 0 &&
    (place.address === null || typeof place.address === "string") &&
    (place.category === null || typeof place.category === "string") &&
    typeof place.latitude === "number" && typeof place.longitude === "number" &&
    hasValidCoordinates({ latitude: place.latitude, longitude: place.longitude }) &&
    place.source === "amap";
}

// User-confirmed input carries only the Place contract, not invented travel data.
export function isConfirmedPlace(value: unknown): value is Place {
  return isPlace(value) && Object.keys(value).every((key) =>
    ["id", "name", "address", "latitude", "longitude", "category", "source"].includes(key));
}

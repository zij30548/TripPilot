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

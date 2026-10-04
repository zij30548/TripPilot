import { describe, expect, it } from "vitest";

import { hasValidCoordinates } from "@/types/place";

describe("hasValidCoordinates", () => {
  it.each([
    { longitude: 120, latitude: 30 },
    { longitude: 0, latitude: 0 },
    { longitude: -180, latitude: -90 },
    { longitude: 180, latitude: 90 },
  ])("accepts finite coordinates within geographic bounds", (coordinates) => {
    expect(hasValidCoordinates(coordinates)).toBe(true);
  });

  it.each([
    { longitude: 181, latitude: 30 },
    { longitude: -181, latitude: 30 },
    { longitude: 120, latitude: 91 },
    { longitude: 120, latitude: -91 },
    { longitude: Number.NaN, latitude: 30 },
    { longitude: 120, latitude: Number.NaN },
    { longitude: Infinity, latitude: 30 },
    { longitude: 120, latitude: -Infinity },
    { longitude: "120", latitude: 30 },
    { longitude: 120, latitude: null },
    { longitude: 120 },
    { latitude: 30 },
  ])("rejects unusable coordinates without coercion or a fallback", (coordinates) => {
    expect(hasValidCoordinates(coordinates as unknown as Parameters<typeof hasValidCoordinates>[0])).toBe(false);
  });
});

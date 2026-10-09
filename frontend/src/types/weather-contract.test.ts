import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { isWeatherForecastResponse, type WeatherForecastResponse } from "@/types/weather";

describe("Python weather output → actual TypeScript runtime guard", () => {
  it("accepts controlled model outputs and rejects semantic corruption", () => {
    const backend = resolve(process.cwd(), "../backend");
    const localPython = `${backend}/.venv/bin/python`;
    const output = execFileSync(existsSync(localPython) ? localPython : "python3", ["-B", "tests/export_weather_contract.py"], {
      cwd: backend,
      env: { ...process.env, PYTHONPATH: backend, PYTHONDONTWRITEBYTECODE: "1", AMAP_WEB_KEY: "" },
      encoding: "utf8",
      maxBuffer: 2 * 1024 * 1024,
    });
    const cases: WeatherForecastResponse[] = JSON.parse(output);
    expect(cases.length).toBeGreaterThan(300);
    for (const value of cases) {
      expect(isWeatherForecastResponse(value, value.request)).toBe(true);
      expect(isWeatherForecastResponse({ ...value, adcode: "110000" })).toBe(false);
      expect(isWeatherForecastResponse({ ...value, coverage: "complete" })).toBe(false);
      expect(isWeatherForecastResponse({ ...value, days: value.days.slice(1) })).toBe(false);
      expect(isWeatherForecastResponse({ ...value, rain_risk: "low" })).toBe(false);
      if (value.days[0].day) {
        const copy = structuredClone(value);
        copy.days[0].day!.temperature_celsius = Number.NaN;
        expect(isWeatherForecastResponse(copy)).toBe(false);
        copy.days[0].day!.temperature_celsius = 0;
        copy.days[0].day!.precipitation_basis = "fabricated";
        expect(isWeatherForecastResponse(copy)).toBe(false);
      }
    }
  });
});

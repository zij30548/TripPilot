export type WeatherForecastRequest = { start_date: string; end_date: string };
export type WeatherFreshness = "fresh" | "stale" | "unknown";
export type WeatherPrecipitation = "rain" | "snow" | "rain_snow" | "none" | "unknown";
export type WeatherPeriod = {
  weather: string | null;
  temperature_celsius: number | null;
  wind_direction: string | null;
  wind_power: string | null;
  precipitation: WeatherPrecipitation;
  precipitation_basis: string | null;
};
export type WeatherForecastDay = {
  date: string;
  status: "available" | "unavailable";
  day: WeatherPeriod | null;
  night: WeatherPeriod | null;
};
export type WeatherForecastResponse = {
  request: WeatherForecastRequest;
  source: "amap";
  city: "上海市";
  adcode: "310000";
  timezone: "Asia/Shanghai";
  queried_at: string;
  reported_at: string | null;
  report_time_status: "valid" | "missing" | "invalid";
  freshness_at_query: WeatherFreshness;
  coverage: "complete" | "partial" | "none";
  guidance_rule: "amap_text_precipitation_v1";
  days: WeatherForecastDay[];
};

const DAY_MS = 86_400_000;
const FUTURE_TOLERANCE_MS = 5 * 60_000;
const rain = new Set(["阵雨", "雷阵雨", "雷阵雨并伴有冰雹", "小雨", "中雨", "大雨", "暴雨", "大暴雨", "特大暴雨", "强阵雨", "强雷阵雨", "极端降雨", "毛毛雨/细雨", "雨", "小雨-中雨", "中雨-大雨", "大雨-暴雨", "暴雨-大暴雨", "大暴雨-特大暴雨", "冻雨"]);
const snow = new Set(["雪", "阵雪", "小雪", "中雪", "大雪", "暴雪", "小雪-中雪", "中雪-大雪", "大雪-暴雪"]);
const rainSnow = new Set(["雨雪天气", "雨夹雪", "阵雨夹雪"]);
const knownWithoutRainSnow = new Set(["晴", "少云", "晴间多云", "多云", "阴", "有风", "平静", "微风", "和风", "清风", "强风/劲风", "疾风", "大风", "烈风", "风暴", "狂爆风", "飓风", "热带风暴", "霾", "中度霾", "重度霾", "严重霾", "浮尘", "扬沙", "沙尘暴", "强沙尘暴", "龙卷风", "雾", "浓雾", "强浓雾", "轻雾", "大雾", "特强浓雾", "热", "冷"]);

// Exact descriptions from AMap's weather-code table. No substring inference:
// an unfamiliar description stays visible but is explicitly not evaluated.
export function classifyWeatherPrecipitation(weather: string | null): WeatherPrecipitation {
  if (weather !== null && rain.has(weather)) return "rain";
  if (weather !== null && snow.has(weather)) return "snow";
  if (weather !== null && rainSnow.has(weather)) return "rain_snow";
  if (weather !== null && knownWithoutRainSnow.has(weather)) return "none";
  return "unknown";
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function exact(value: Record<string, unknown>, keys: string[]): boolean {
  return Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
}
function dateMillis(value: unknown): number | null {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value) || value.slice(0, 4) === "0000") return null;
  const parsed = Date.parse(`${value}T00:00:00Z`);
  return Number.isFinite(parsed) && new Date(parsed).toISOString().slice(0, 10) === value ? parsed : null;
}
export function isWeatherForecastRequest(value: unknown): value is WeatherForecastRequest {
  if (!record(value) || !exact(value, ["start_date", "end_date"])) return false;
  const start = dateMillis(value.start_date);
  const end = dateMillis(value.end_date);
  return start !== null && end !== null && end >= start && end - start <= 2 * DAY_MS;
}
export function getWeatherDates(request: WeatherForecastRequest): string[] {
  if (!isWeatherForecastRequest(request)) return [];
  const start = dateMillis(request.start_date)!;
  const end = dateMillis(request.end_date)!;
  return Array.from({ length: (end - start) / DAY_MS + 1 }, (_, index) => new Date(start + index * DAY_MS).toISOString().slice(0, 10));
}
// Fixed UTC+8 has no daylight-saving transition; no host timezone is involved.
export function getShanghaiDate(nowMs: number = Date.now()): string {
  return Number.isFinite(nowMs) ? new Date(nowMs + 8 * 3_600_000).toISOString().slice(0, 10) : "";
}

function timestampMillis(value: unknown, zone: "Z" | "+08:00"): number | null {
  if (typeof value !== "string") return null;
  const format = zone === "Z"
    ? /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?Z$/
    : /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?\+08:00$/;
  const match = value.match(format);
  if (!match || dateMillis(match[1]) === null || Number(match[2]) > 23 || Number(match[3]) > 59 || Number(match[4]) > 59) return null;
  const parsed = Date.parse(value);
  // Python emits microseconds. Date.parse truncates them to milliseconds, which
  // otherwise launders an exact 24-hour + 1µs source age into "fresh".
  const fraction = value.match(/\.(\d{1,6})/)?.[1] ?? "";
  const subMillisecond = Number(fraction.padEnd(6, "0")) % 1000 / 1000;
  return Number.isFinite(parsed) ? parsed + subMillisecond : null;
}

export function getWeatherFreshness(response: Pick<WeatherForecastResponse, "reported_at" | "report_time_status">, nowMs: number = Date.now()): WeatherFreshness {
  if (response.report_time_status !== "valid" || !Number.isFinite(nowMs)) return "unknown";
  const reported = timestampMillis(response.reported_at, "+08:00");
  if (reported === null || Number(response.reported_at!.slice(0, 4)) < 2000 || reported > nowMs + FUTURE_TOLERANCE_MS) return "unknown";
  return nowMs - reported > DAY_MS ? "stale" : "fresh";
}

function nullableText(value: unknown): value is string | null {
  return value === null || (typeof value === "string" && value.length > 0 && [...value].length <= 100 && value.trim() === value && !/[\u0000-\u001f\u007f-\u009f\ufeff]/.test(value));
}
function isPeriod(value: unknown): value is WeatherPeriod {
  if (!record(value) || !exact(value, ["weather", "temperature_celsius", "wind_direction", "wind_power", "precipitation", "precipitation_basis"])) return false;
  if (!nullableText(value.weather) || !nullableText(value.wind_direction) || !nullableText(value.wind_power)) return false;
  if (value.temperature_celsius !== null && !(typeof value.temperature_celsius === "number" && Number.isFinite(value.temperature_celsius) && value.temperature_celsius >= -100 && value.temperature_celsius <= 100)) return false;
  return value.precipitation === classifyWeatherPrecipitation(value.weather) && value.precipitation_basis === value.weather;
}
export function isWeatherForecastResponse(value: unknown, request?: WeatherForecastRequest): value is WeatherForecastResponse {
  if (!record(value) || !exact(value, ["request", "source", "city", "adcode", "timezone", "queried_at", "reported_at", "report_time_status", "freshness_at_query", "coverage", "guidance_rule", "days"])) return false;
  if (!isWeatherForecastRequest(value.request) || (request !== undefined && (!isWeatherForecastRequest(request) || value.request.start_date !== request.start_date || value.request.end_date !== request.end_date))) return false;
  if (value.source !== "amap" || value.city !== "上海市" || value.adcode !== "310000" || value.timezone !== "Asia/Shanghai" || value.guidance_rule !== "amap_text_precipitation_v1") return false;
  const queried = timestampMillis(value.queried_at, "Z");
  if (queried === null) return false;
  if (value.report_time_status === "valid") {
    const reported = timestampMillis(value.reported_at, "+08:00");
    if (reported === null || typeof value.reported_at !== "string" || Number(value.reported_at.slice(0, 4)) < 2000 || reported > queried + FUTURE_TOLERANCE_MS) return false;
    if (value.freshness_at_query !== (queried - reported > DAY_MS ? "stale" : "fresh")) return false;
  } else if ((value.report_time_status !== "missing" && value.report_time_status !== "invalid") || value.reported_at !== null || value.freshness_at_query !== "unknown") return false;
  const dates = getWeatherDates(value.request);
  if (!Array.isArray(value.days) || value.days.length !== dates.length) return false;
  let available = 0;
  for (let index = 0; index < dates.length; index++) {
    const day: unknown = value.days[index];
    if (!record(day) || !exact(day, ["date", "status", "day", "night"]) || day.date !== dates[index]) return false;
    if (day.status === "available") {
      if (!isPeriod(day.day) || !isPeriod(day.night)) return false;
      available++;
    } else if (day.status !== "unavailable" || day.day !== null || day.night !== null) return false;
  }
  return value.coverage === (available === 0 ? "none" : available === dates.length ? "complete" : "partial");
}

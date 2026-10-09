"use client";

import { useEffect, useState } from "react";
import type { useWeatherForecast } from "@/lib/use-weather-forecast";
import { getShanghaiDate, getWeatherDates, getWeatherFreshness, type WeatherForecastRequest, type WeatherForecastResponse, type WeatherPeriod } from "@/types/weather";

type WeatherController = ReturnType<typeof useWeatherForecast>;
const DAY_MS = 86_400_000;

// This is a local display clock, not a weather polling loop. Timers and visibility
// events never call the request hook. Midnight and the 24-hour boundary are exact.
function useDisplayClock(response: WeatherForecastResponse | null) {
  const [clock, setClock] = useState(() => ({ response, now: Date.now() }));
  // A newly received snapshot must not inherit the preceding timer's older time.
  // React reapplies this same-component state adjustment before committing UI.
  if (clock.response !== response) setClock(() => ({ response, now: Date.now() }));
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    function schedule() {
      const current = Date.now();
      const midnight = Date.parse(`${getShanghaiDate(current)}T00:00:00+08:00`) + DAY_MS;
      const staleBoundary = response?.reported_at ? Date.parse(response.reported_at) + DAY_MS + 1 : Infinity;
      const nextBoundary = Math.min(midnight, staleBoundary > current ? staleBoundary : Infinity);
      timer = setTimeout(update, Math.max(1, Math.min(60_000, nextBoundary - current)));
    }
    function update() {
      clearTimeout(timer);
      setClock({ response, now: Date.now() });
      schedule();
    }
    function onVisibility() {
      if (document.visibilityState === "visible") update();
    }
    schedule();
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("focus", update);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("focus", update);
    };
  }, [response]);
  return clock.now;
}

function shanghaiTime(value: string) {
  return new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
}

function PeriodForecast({ label, period, allowGuidance }: { label: "白天" | "夜间"; period: WeatherPeriod | null; allowGuidance: boolean }) {
  const precipitation = period?.precipitation ?? "unknown";
  let guidance: string;
  if (!allowGuidance) guidance = "仅展示取得的预报值，暂不提供当前有效的出行判断。";
  else if (precipitation === "rain") guidance = `${label}预报有雨：准备防雨用品，可考虑室内备选。`;
  else if (precipitation === "snow") guidance = `${label}预报有雪：注意保暖、防滑，可考虑室内备选。`;
  else if (precipitation === "rain_snow") guidance = `${label}预报有雨雪：准备防雨用品，注意保暖、防滑，可考虑室内备选。`;
  else if (precipitation === "none") guidance = `${label}未触发雨雪提示规则，不代表保证适合出行。`;
  else guidance = `${label}天气描述未知或未纳入规则，未评估出行影响。`;

  return <section aria-label={label} className="min-w-0 rounded-xl bg-white p-3">
    <h4 className="text-sm font-semibold text-[#18392f]">{label}</h4>
    <dl className="mt-2 space-y-1 text-sm leading-6 text-[#56605c]">
      <div className="break-words"><dt className="inline">天气：</dt><dd className="inline">{period?.weather ?? "未知"}</dd></div>
      <div><dt className="inline">{label}温度：</dt><dd className="inline">{period?.temperature_celsius == null ? "未知" : `${period.temperature_celsius}°C`}</dd></div>
      <div className="break-words"><dt className="inline">风向：</dt><dd className="inline">{period?.wind_direction ?? "未知"}</dd></div>
      <div className="break-words"><dt className="inline">风力：</dt><dd className="inline">{period?.wind_power ?? "未知"}</dd></div>
    </dl>
    <p className="mt-3 text-xs leading-5 text-[#315f51]">{guidance}</p>
    {period?.precipitation_basis && <p className="mt-1 break-words text-xs leading-5 text-[#68726c]">雨雪判定依据：{period.precipitation_basis}</p>}
  </section>;
}

export default function WeatherForecast({ request, weather }: { request: WeatherForecastRequest; weather: WeatherController }) {
  const { state, canQuery } = weather;
  const response = state.response;
  const now = useDisplayClock(response);
  const freshness = response ? getWeatherFreshness(response, now) : null;
  const today = getShanghaiDate(now);
  const dates = getWeatherDates(request);
  const loading = state.status === "loading";
  const attempted = state.status !== "idle" || response !== null;

  return <section aria-label="上海逐日天气预报" className="mt-6 min-w-0 rounded-3xl border border-[#34657b]/20 bg-[#f1f7fa] p-5 sm:p-8">
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0 flex-1 basis-64">
        <h2 className="text-xl font-semibold text-[#18392f]">上海逐日天气预报</h2>
        <p className="mt-2 text-sm leading-6 text-[#56605c]">按本次确认的旅行日期查看上海市预报。仅在你点击时查询，不会随日期切换、地图或草案操作自动刷新。</p>
      </div>
      <button type="button" disabled={loading || !canQuery} onClick={() => { void weather.query(); }}
        className="min-h-11 max-w-full rounded-xl bg-[#18392f] px-5 py-3 text-sm font-semibold text-white focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-50">
        {loading ? "正在查询天气…" : attempted ? "刷新天气" : "查询天气"}
      </button>
    </div>
    <p className="mt-3 text-xs leading-5 text-[#68726c]">仅为市级昼夜预报，不是地点级或逐小时天气；昼夜温度不是最高／最低温。本轮提示不参与候选筛选、排程、预算或地图路线，旧 Mock 天气仍单独保留。</p>
    <p className="mt-1 text-xs leading-5 text-[#68726c]">时间均按 Asia/Shanghai（上海时间）显示。来源发布时间超过 24 小时标记陈旧，这是本产品提示规则，不是高德承诺的有效期。</p>
    {!canQuery && <p role="alert" className="mt-4 text-sm text-[#a63d2d]">请返回修改需求，确认有效的 1～3 天旅行日期。</p>}
    <div aria-live="polite" className="mt-4 text-sm leading-6 text-[#315f51]">
      {state.status === "idle" && <p>尚未查询天气，请点击“查询天气”。</p>}
      {loading && <p role="status">正在查询上海天气，请稍候。</p>}
      {state.status === "failed" && <p role="alert">{state.message ?? "本次天气查询失败，请主动重试。"}</p>}
      {state.showingPrevious && response && <p>当前展示上次成功查询的整批结果；{loading ? "正在刷新。" : "本次刷新失败，查询时间与发布时间仍为原值。"}</p>}
      {response && <p>{response.coverage === "complete" ? "本批预报包含全部旅行日期。" : response.coverage === "partial" ? "本批预报仅包含部分旅行日期，缺失日期未补值。" : "本批未取得旅行日期的预报；这不等同于查询失败。"}</p>}
    </div>
    {response && <div className="mt-3 rounded-xl border border-[#34657b]/15 bg-white/70 p-3 text-xs leading-6 text-[#56605c]">
      <p>来源：高德天气 · 上海市（310000）</p>
      <p>成功查询时间：{shanghaiTime(response.queried_at)}</p>
      <p>来源发布时间：{response.reported_at ? shanghaiTime(response.reported_at) : response.report_time_status === "missing" ? "未知（来源未提供）" : "未知（来源时间无法确认）"}</p>
      <p>{freshness === "stale" ? "预报已陈旧：来源发布时间已超过 24 小时，暂不提供当前有效的出行判断。" : freshness === "unknown" ? "预报新鲜度未知：无法确认来源发布时间，暂不提供当前有效的出行判断。" : "来源发布时间未超过 24 小时；这不保证预报准确或适合出行。"}</p>
    </div>}
    <div className="mt-5 grid gap-4 lg:grid-cols-3">
      {dates.map((date) => {
        const day = response?.days.find((item) => item.date === date);
        const past = date < today;
        return <article key={date} aria-label={`${date} 天气预报`} className="min-w-0 rounded-2xl border border-[#34657b]/20 p-4">
          <h3 className="font-semibold text-[#18392f]">{date}</h3>
          {past && <p className="mt-2 text-xs leading-5 text-[#8a5b19]">该日期已过去。取得的旧预报不是历史实际天气，不提供当前有效的出行判断。</p>}
          {day?.status === "available" ? <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-1">
            <PeriodForecast label="白天" period={day.day} allowGuidance={!past && freshness === "fresh"} />
            <PeriodForecast label="夜间" period={day.night} allowGuidance={!past && freshness === "fresh"} />
          </div> : <p className="mt-3 text-sm leading-6 text-[#68726c]">{response ? "暂未取得该日期预报。不会用邻日、历史值或 Mock 天气补充。" : loading ? "正在查询该日期预报…" : state.status === "failed" ? "本次查询失败，尚未取得该日期预报。" : "尚未查询该日期预报。"}</p>}
        </article>;
      })}
    </div>
  </section>;
}

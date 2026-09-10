import type { Weather } from "@/types/trip";

export default function WeatherSummary({ weather }: { weather: Weather }) {
  return (
    <section aria-labelledby="weather-title" className="rounded-2xl border border-[#18201d]/10 bg-[#eaf0eb] p-5 sm:p-6">
      <div className="flex items-center justify-between gap-3"><h2 id="weather-title" className="text-lg font-semibold">天气概览</h2><span className="text-xs font-semibold text-[#315f51]">MOCK</span></div>
      <p className="mt-1 text-xs text-[#56605c]"><time dateTime={weather.date}>{weather.date}</time></p>
      <div className="mt-5 flex flex-wrap items-end justify-between gap-3"><p className="text-2xl font-semibold text-[#18392f]">{weather.condition}</p><p className="text-lg font-medium">{weather.min_temperature}° / {weather.max_temperature}°C</p></div>
      <p className="mt-3 text-sm text-[#315f51]">降雨风险 {weather.rain_risk}% · 最低 / 最高温</p>
      <p className="mt-3 text-xs leading-5 text-[#68726c]">仅为天气卡片演示，不是该日期的真实预报。</p>
    </section>
  );
}

import TripPlanner from "@/components/trip/trip-planner";
import SavedHistory from "@/components/trip/saved-history";

const planningSteps = [
  {
    number: "01",
    title: "告诉我们你的边界",
    description: "确认日期、住宿参考点与必去地点。",
  },
  {
    number: "02",
    title: "主动生成真实草案",
    description: "真实交通估时与规则排程；可按兴趣准备候选。",
  },
  {
    number: "03",
    title: "核对、调整与保存",
    description: "费用、天气和 AI 按需核对，历史可创建独立副本。",
  },
];

export default function Home() {
  return (
    <main className="min-h-screen overflow-hidden bg-[#f4f2ec] text-[#18201d]">
      <div className="mx-auto max-w-[1440px] px-5 py-6 sm:px-8 lg:px-12 lg:py-8">
        <header className="flex items-center justify-between border-b border-[#18201d]/10 pb-5">
          <a
            className="flex items-center gap-3 rounded-lg focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[#df5b3f]"
            href="#top"
            aria-label="TripPilot 首页"
          >
            <span className="grid size-9 place-items-center rounded-full bg-[#18392f] text-sm font-bold text-white">
              T
            </span>
            <span className="text-xl font-semibold tracking-[-0.04em]">
              TripPilot
            </span>
          </a>
          <SavedHistory />
          <span className="hidden rounded-full border border-[#18392f]/15 bg-white/70 px-3 py-1.5 text-xs font-semibold text-[#31564b] sm:inline sm:px-4 sm:text-sm">
            上海 · 杭州 · 北京
          </span>
        </header>

        <TripPlanner>
          <section className="lg:sticky lg:top-12 lg:self-start">
            <p className="mb-6 flex items-center gap-3 text-xs font-bold tracking-[0.2em] text-[#bd4c35] uppercase">
              <span className="h-px w-8 bg-[#bd4c35]" />
              Plan less. Experience more.
            </p>
            <h1 className="max-w-2xl text-[2.7rem] leading-[1.08] font-semibold tracking-[-0.055em] text-balance sm:text-6xl lg:text-[4rem]">
              确认想去的城市，规划属于你的行程。
            </h1>
            <p className="mt-6 max-w-xl text-base leading-7 text-[#56605c] sm:text-lg sm:leading-8">
              从上海、杭州、北京选择一座城市，确认地点，主动查询真实交通，再按时间窗口生成 1～3 日草案。
              必去按输入顺序安排，可选每天最多补充一个。预算用于核对，旅行节奏暂不参与排程；AI 只按需解读或理解受限调整，不负责生成整份行程。
            </p>

            <div className="mt-9 grid max-w-xl grid-cols-3 overflow-hidden rounded-2xl border border-[#18201d]/10 bg-white/55">
              {[
                ["1～3 天", "行程长度"],
                ["三城可选", "单城规划"],
                ["规则排程", "规划方式"],
              ].map(([value, label], index) => (
                <div
                  key={label}
                  className={`px-3 py-4 text-center sm:px-5 ${
                    index > 0 ? "border-l border-[#18201d]/10" : ""
                  }`}
                >
                  <p className="text-sm font-semibold text-[#18392f] sm:text-base">
                    {value}
                  </p>
                  <p className="mt-1 text-[11px] text-[#747b77] sm:text-xs">
                    {label}
                  </p>
                </div>
              ))}
            </div>

            <ol className="mt-10 hidden max-w-xl space-y-5 lg:block">
              {planningSteps.map((step) => (
                <li key={step.number} className="flex gap-4">
                  <span className="pt-0.5 font-mono text-xs font-semibold text-[#bd4c35]">
                    {step.number}
                  </span>
                  <div>
                    <h2 className="text-sm font-semibold">{step.title}</h2>
                    <p className="mt-1 text-sm leading-6 text-[#737a76]">
                      {step.description}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
          </section>

        </TripPlanner>
        <footer className="border-t border-[#18201d]/10 py-5 text-sm text-[#56605c]"><a href="/demo" target="_blank" rel="noopener noreferrer" className="inline-block min-h-11 underline">旧版 Mock／地图联调示例（独立页面）</a></footer>
      </div>
    </main>
  );
}

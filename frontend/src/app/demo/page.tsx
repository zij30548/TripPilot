import DemoPlanner from "@/components/trip/demo-planner";

export default function DemoPage() {
  return <main className="mx-auto min-h-screen max-w-7xl bg-[#f4f2ec] px-5 py-8 text-[#18201d]">
    <a href="/" target="_blank" rel="noopener noreferrer" className="inline-block min-h-11 font-semibold underline">正式规划首页（新标签页）</a>
    <h1 className="text-3xl font-semibold">旧版组合示例 · Mock 行程与地图联调</h1>
    <p className="mt-4 text-sm leading-7">此页保留旧 Mock 接口与活动绑定地图，不是正式草案地图。示例时间、费用和天气是 Mock；明确搜索与路线查询才获取真实数据。打开此页不会自动请求业务数据。</p>
    <DemoPlanner><p className="leading-7">主动提交需求后加载固定两日 Mock 示例。独立的真实草案工具也保留在这里用于兼容回归；日常规划请使用首页，无需先加载示例。</p></DemoPlanner>
  </main>;
}

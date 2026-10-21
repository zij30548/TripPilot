"use client";

import { useEffect, useState } from "react";
import type { ChatConversation } from "@/lib/chat";
import { chatCity, chatCityOptions } from "@/lib/chat-cities";
import { useChatPlaces, type PlacesPlanningAvailability } from "@/lib/chat-places-controller";
import { CLARIFICATIONS, type PlaceRole, type PlacesWorkspace } from "@/lib/chat-places";

const focus = "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#376d5b]";
const button = `min-h-11 rounded-xl border border-[#cbd6c6] px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-45 ${focus}`;

/** Compact chat result card, not the old planning controllers or a new scheduler. */
export default function ChatPlaces({ conversation, open, blocked, onWorkspace, onAvailability }: { conversation: ChatConversation; open: boolean; blocked: boolean; onWorkspace?: (workspace: PlacesWorkspace) => void; onAvailability?: (value: PlacesPlanningAvailability) => void }) {
  const tools = useChatPlaces(conversation, open, blocked, onAvailability), { state } = tools;
  const [cityText, setCityText] = useState("");
  const [proposedCity, setProposedCity] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<"search" | "ask">("search");
  const [role, setRole] = useState<PlaceRole>("required");
  const workspace = state.workspace, city = chatCity(workspace?.city_id ?? null);
  const batch = workspace?.batch;
  const preview = batch?.places.find(place => place.id === state.previewId);
  const busy = blocked || state.pending;
  const running = workspace?.last_operation?.state === "running";
  const canWrite = tools.usable && !busy && !state.needsRead && !running;
  const cityChoices = chatCityOptions(cityText);
  const proposed = chatCity(proposedCity ?? workspace?.proposed_city_id ?? null);
  useEffect(() => { if (workspace) onWorkspace?.(workspace); }, [workspace, onWorkspace]);

  return <section hidden={!open} aria-label="真实地点准备" className="mt-6 rounded-[22px] border border-[#dce4dc] bg-[#fffefa] p-5 sm:p-6">
    <h2 className="text-lg font-semibold">把想去的名字，核对成真实地点</h2>
    <p className="mt-2 text-xs leading-6 text-[#73816a]">这里准备住宿和必去地点，不生成、采用或保存行程。目录包含大陆城市及部分特殊行政单元，不保证各地交通和天气服务可用。</p>
    {state.pending && <p role="status" className="mt-3 text-sm">正在处理本次地点准备…</p>}
    {state.error && <p role="alert" className="mt-3 rounded-xl bg-[#fff4df] p-3 text-sm leading-6 text-[#8b6339]">{state.error}</p>}
    {state.notice && <p role="status" className="mt-3 text-xs leading-6 text-[#64775a]">{state.notice}</p>}
    <div className="mt-3 flex flex-wrap gap-2">
      <button type="button" className={button} disabled={state.pending || blocked} onClick={() => void tools.reload()}>重新读取地点准备</button>
      {(state.pending || running) && <button type="button" className={button} onClick={() => void tools.cancel()}>停止地点查询</button>}
      {workspace?.stale && <button type="button" className={button} disabled={busy || state.needsRead} onClick={() => void tools.act({ action: "sync" })}>核对后同步最新需求</button>}
    </div>
    {workspace && <>
      {workspace.stale && <p role="status" className="mt-3 text-sm text-[#8b6339]">需求版本已经变化，旧候选不能确认。同城同步可保留仍相关的已确认地点；城市不一致或未确定时会清除旧地点。</p>}
      <p className="mt-5 text-sm font-medium">地点准备城市：{city ? `${city.official_name} · ${city.province_name}` : "尚未确认"}</p>
      {!!workspace.city_id && chatCity(conversation.requirements.destination_id)?.id !== workspace.city_id && <p className="mt-2 text-xs leading-6 text-[#8b6339]">地点准备城市与需求意向尚未一致。当前只独立准备地点，后续生成前必须重新核对，不能混用两个城市。</p>}
      <details className="mt-3">
        <summary className={`min-h-10 cursor-pointer py-2 text-sm ${focus}`}>{city ? "重新选择城市" : "选择城市"}</summary>
        <label className="mt-2 block text-xs text-[#73816a]" htmlFor={`place-city-${conversation.id}`}>城市名称（例如苏州、成都市）</label>
        <input id={`place-city-${conversation.id}`} value={cityText} maxLength={80} className={`mt-2 min-h-11 w-full rounded-xl border border-[#cbd6c6] px-3 text-sm ${focus}`}
          onChange={event => { tools.edit(); setCityText(event.target.value); setProposedCity(null); }} />
        <ul className="mt-2 max-h-44 overflow-y-auto rounded-xl border border-[#e1e6db]">
          {cityChoices.map(option => <li key={option.id}><button type="button" className={`min-h-11 w-full px-3 py-2 text-left text-sm hover:bg-[#f0f3ea] ${focus}`}
            disabled={!canWrite} onClick={() => { tools.edit(); setProposedCity(option.id); }}>{option.official_name} · {option.province_name}</button></li>)}
        </ul>
        {!cityChoices.length && <p className="mt-2 text-xs">目录中没有匹配城市，请补充准确名称；不会回退到上海。</p>}
        <p className="mt-2 text-xs leading-6 text-[#73816a]">显示最多20项；选择只是预览，下面明确确认才改变地点准备。切换会清除旧城市的候选、住宿和必去，不会改写历史存档。</p>
      </details>
      {proposed && proposed.id !== workspace.city_id && <div className="mt-3 rounded-xl bg-[#f0f4e9] p-3">
        <p className="text-sm">准备使用：{proposed.official_name} · {proposed.province_name}</p>
        <button type="button" className={`${button} mt-3`} disabled={!canWrite} onClick={() => { void tools.act({ action: "set_city", city_id: proposed.id }); setProposedCity(null); }}>
          {workspace.city_id ? "确认切换城市并清除旧地点" : "确认使用这座城市"}
        </button>
      </div>}
      <form aria-label="主动查找真实地点" className="mt-5 border-t border-[#e1e6db] pt-4" onSubmit={event => {
        event.preventDefault();
        if (!canWrite || !query.trim() || mode === "search" && !workspace.city_id) return;
        void tools.act(mode === "search" ? { action: "search", keyword: query.trim(), role } : { action: "ask", text: query.trim() });
      }}>
        <div className="flex flex-wrap gap-3">
          <label className="text-xs">查找方式<select aria-label="地点查找方式" value={mode} disabled={busy} className={`ml-2 min-h-11 rounded-lg border border-[#cbd6c6] p-2 ${focus}`}
            onChange={event => { tools.edit(); setMode(event.target.value as "search" | "ask"); }}><option value="search">按名称搜索（不调用模型）</option><option value="ask">用一句话找（调用模型一次）</option></select></label>
          {mode === "search" && <label className="text-xs">用途<select aria-label="地点用途" value={role} disabled={busy} className={`ml-2 min-h-11 rounded-lg border border-[#cbd6c6] p-2 ${focus}`}
            onChange={event => { tools.edit(); setRole(event.target.value as PlaceRole); }}><option value="required">必去地点</option><option value="accommodation">住宿参考点</option></select></label>}
        </div>
        <label htmlFor={`place-query-${conversation.id}`} className="mt-3 block text-xs">{mode === "search" ? "地点关键词" : "你想找什么地点？"}</label>
        <textarea id={`place-query-${conversation.id}`} rows={2} value={query} maxLength={mode === "search" ? 80 : 2000}
          placeholder={mode === "search" ? "例如：拙政园" : "例如：在苏州找拙政园，作为必去地点"}
          onChange={event => { tools.edit(); setQuery(event.target.value); }} className={`mt-2 w-full rounded-xl border border-[#cbd6c6] p-3 text-sm ${focus}`} />
        <p className="mt-2 text-xs leading-6 text-[#73816a]">{mode === "search" ? "点击后只搜索已确认城市，不调用模型。" : "点击后最多一次模型选择、一次地点搜索，不会自动确认或追加总结；城市改变要先由你确认。"} 选城市、展开或预览不会查询高德。</p>
        <button type="submit" className={`${button} mt-3 bg-[#325b46] text-white`} disabled={!canWrite || !query.trim() || mode === "search" && !workspace.city_id}>
          {mode === "search" ? "主动搜索高德地点" : "理解要求并查找地点"}
        </button>
      </form>
      {workspace.request_text && <p className="mt-4 whitespace-pre-wrap break-words text-xs leading-6 text-[#73816a]">本次地点要求：{workspace.request_text}</p>}
      {workspace.clarification && <p role="status" className="mt-3 rounded-xl bg-[#f4f3e8] p-3 text-sm leading-6">{CLARIFICATIONS[workspace.clarification]}</p>}
      {batch && !state.dirty && !workspace.stale && <div className="mt-5">
        <h3 className="text-sm font-semibold">高德候选 · 仅查看，不代表已确认</h3>
        <p className="mt-1 break-words text-xs leading-6 text-[#73816a]">{chatCity(batch.city_id)?.official_name} · 关键词：{batch.keyword} · 用途：{batch.role === "required" ? "必去地点" : "住宿参考点"} · 查询时间：{batch.queried_at}</p>
        {!batch.places.length && <p className="mt-3 text-sm">没有找到匹配地点。请检查城市或换关键词，不会编造候选。</p>}
        <ul className="mt-3 space-y-2">{batch.places.map(place => <li key={place.id}>
          <button type="button" className={`${button} w-full text-left ${state.previewId === place.id ? "bg-[#edf2e6]" : ""}`} disabled={!canWrite}
            aria-pressed={state.previewId === place.id} onClick={() => tools.preview(place.id)}><span className="block font-medium">预览：{place.name}</span><span className="mt-1 block break-words text-xs text-[#73816a]">{place.address || "未提供详细地址"}</span></button>
        </li>)}</ul>
        {preview && <section aria-label="真实地点预览" className="mt-4 rounded-xl border border-[#c4d2bc] p-4">
          <h4 className="font-semibold">{preview.name}</h4><p className="mt-2 break-words text-sm">{preview.address || "上游未提供地址，请额外核对"}</p>
          <p className="mt-2 text-xs leading-6 text-[#73816a]">高德返回城市：{preview.region.cityname}；行政码：{preview.region.adcode}。坐标：{preview.longitude}, {preview.latitude}。地点有真实搜索来源，不代表营业、预约或照片身份已经核实。</p>
          <button type="button" className={`${button} mt-3 bg-[#325b46] text-white`} disabled={!canWrite || batch.role === "required" && (workspace.required_places.length >= 6 || workspace.required_places.some(place => place.id === preview.id))}
            onClick={() => void tools.act({ action: "confirm", batch_id: batch.id, place_id: preview.id, role: batch.role })}>
            {batch.role === "required" ? "明确确认，加入必去" : "明确确认，设为住宿参考点"}
          </button>
          {batch.role === "required" && workspace.required_places.some(place => place.id === preview.id) && <p className="mt-2 text-xs">这个POI已经加入必去，不会重复添加。</p>}
        </section>}
      </div>}
      <div className="mt-5 border-t border-[#e1e6db] pt-4" aria-label="已经确认的真实地点">
        <h3 className="text-sm font-semibold">已确认地点 · 服务端工作副本 v{workspace.revision}</h3>
        <p className="mt-2 break-words text-sm">住宿参考点：{workspace.accommodation?.name ?? "尚未确认"}</p>
        {workspace.accommodation && <button type="button" className={`${button} mt-2 text-xs`} disabled={!canWrite} onClick={() => void tools.act({ action: "remove", place_id: workspace.accommodation!.id, role: "accommodation" })}>移除住宿参考点</button>}
        <ol className="mt-3 space-y-2">{workspace.required_places.map((place, index) => <li key={place.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-[#f3f5ed] px-3 py-2 text-sm">
          <span className="min-w-0 break-words">{index + 1}. {place.name}</span><button type="button" className={`${button} text-xs`} disabled={!canWrite} onClick={() => void tools.act({ action: "remove", place_id: place.id, role: "required" })}>移除{place.name}</button>
        </li>)}</ol>
        <p className="mt-3 text-xs leading-6 text-[#73816a]">必去{workspace.required_places.length}/6。这里准备真实地点；确认不会自动生成行程。请到行程面板核对当前输入并主动操作。工作副本与需求城市不一致时不能据此生成。</p>
      </div>
    </>}
  </section>;
}

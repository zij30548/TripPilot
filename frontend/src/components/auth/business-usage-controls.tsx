"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { authSnapshot } from "@/lib/auth-session";
import { queryBusinessUsage, type BusinessUsage } from "@/lib/business-usage";
import {
  BUSINESS_SERVICES, BUSINESS_SERVICE_LABELS, businessUsageNoticeSnapshot, businessUsageNoticeServerSnapshot,
  businessUsageNoticeText, clearBusinessUsageNotice, subscribeBusinessUsageNotice,
} from "@/lib/business-usage-notices";

function shanghaiTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value));
}

export function BusinessUsageControls({ epoch, disabled = false }: { epoch: number; disabled?: boolean }) {
  const notice = useSyncExternalStore(subscribeBusinessUsageNotice, businessUsageNoticeSnapshot, businessUsageNoticeServerSnapshot);
  const [open, setOpen] = useState(false);
  const [usage, setUsage] = useState<BusinessUsage | null>(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const current = useRef({ active: true, version: 0, controller: null as AbortController | null, timer: null as ReturnType<typeof setTimeout> | null });
  function invalidate() {
    const value = current.current;
    value.version += 1;
    value.controller?.abort();
    value.controller = null;
    if (value.timer) clearTimeout(value.timer);
    value.timer = null;
  }
  useEffect(() => {
    const value = current.current;
    value.active = true;
    return () => {
      value.active = false;
      value.version += 1;
      value.controller?.abort();
      value.controller = null;
      if (value.timer) clearTimeout(value.timer);
      value.timer = null;
    };
  }, []);
  function valid(version: number) {
    return current.current.active && version === current.current.version && authSnapshot().epoch === epoch && authSnapshot().phase === "authenticated";
  }
  async function refresh() {
    if (disabled || current.current.controller || authSnapshot().epoch !== epoch || authSnapshot().phase !== "authenticated") return;
    invalidate();
    const version = current.current.version;
    const controller = new AbortController();
    current.current.controller = controller;
    current.current.timer = setTimeout(() => controller.abort(), 10_000);
    setOpen(true); setLoading(true); setMessage(null); setUsage(null);
    try {
      const result = await queryBusinessUsage(controller.signal);
      if (!valid(version) || controller.signal.aborted) return;
      setUsage(result);
    } catch (error) {
      if (!valid(version)) return;
      setMessage(controller.signal.aborted ? "额度查询已超时，请手动重试。" : error instanceof Error ? error.message : "无法读取调用额度，请手动重试。");
    } finally {
      if (valid(version)) {
        current.current.controller = null;
        if (current.current.timer) clearTimeout(current.current.timer);
        current.current.timer = null;
        setLoading(false);
      }
    }
  }
  function close() { invalidate(); setOpen(false); setLoading(false); setMessage(null); }
  const currentNotice = notice?.epoch === epoch ? notice : null;
  return <section aria-label="调用额度与限制" className="mx-auto max-w-6xl px-4 pb-3 text-sm">
    <div className="flex justify-end">
      <button type="button" className="rounded-lg border px-3 py-2" disabled={disabled} aria-expanded={open} aria-controls="business-usage-panel" onClick={() => open ? close() : void refresh()}>我的调用额度</button>
    </div>
    {currentNotice && <div role="alert" className="mt-3 rounded-xl bg-amber-50 p-3 text-amber-950">
      <p className="font-medium">上次调用被限制</p>
      <p className="mt-1 break-words">{businessUsageNoticeText(currentNotice)}</p>
      {currentNotice.resetAt && <p className="mt-1 text-xs">本次限制提示的重置时间：{shanghaiTime(currentNotice.resetAt)}（上海时间）。</p>}
      <button type="button" className="mt-2 underline" onClick={() => { if (current.current.active && authSnapshot().epoch === epoch) clearBusinessUsageNotice(); }}>关闭提示</button>
    </div>}
    {open && <div id="business-usage-panel" className="mt-3 rounded-xl border bg-white p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-semibold">当前浏览器工作区调用额度</h2>
        <div className="flex gap-3"><button type="button" className="underline disabled:opacity-50" disabled={disabled || loading} onClick={() => void refresh()}>刷新额度</button><button type="button" className="underline" onClick={close}>{loading ? "取消查询并关闭" : "关闭额度"}</button></div>
      </div>
      <p className="mt-2 text-xs text-slate-600">这里只读取自己的计数，不发起高德、模型或媒体处理。额度按调用尝试计，不是精确人民币费用上限；已发送失败或取消仍可能计数，不会自动重试业务。</p>
      {loading && <p role="status" className="mt-3">正在读取调用额度…</p>}
      {message && <p role="alert" className="mt-3 text-amber-900">{message}</p>}
      {usage && <>
        <p className="mt-3">工作区短窗口：{usage.rate.used} / {usage.rate.limit} 次，窗口 {usage.rate.window_seconds} 秒。</p>
        <p className="mt-1 text-xs text-slate-600">数据时间 {shanghaiTime(usage.now)}；当日额度于 {shanghaiTime(usage.reset_at)} 重置（上海时间）。服务整体也有限额；工作区有剩余不保证本次可调用。</p>
        <ul className="mt-3 grid gap-2 sm:grid-cols-2">{BUSINESS_SERVICES.map(service => <li key={service} className="rounded-lg bg-slate-50 p-3">
          <span className="font-medium">{BUSINESS_SERVICE_LABELS[service]}</span>
          <p>所示日期已计 {usage.services[service].used} / {usage.services[service].limit}，剩余 {usage.services[service].remaining}{usage.services[service].limit === 0 ? "（未开放）" : ""}</p>
        </li>)}</ul>
      </>}
    </div>}
  </section>;
}

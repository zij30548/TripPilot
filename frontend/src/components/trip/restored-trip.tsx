"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useRestoredTrip } from "@/lib/use-restored-trip";
import { applyEditedRequirements, type RestoredTrip } from "@/lib/restore-trip";
import type { TripRequest } from "@/types/trip";
import PlanningWorkspace, { type WorkspaceEdit } from "./planning-workspace";
import TripRequestForm from "../trip-request-form";
import SavedHistory, { SavedTripDetails } from "./saved-history";

type EditSession = { id: number; workspace: WorkspaceEdit; dirtyBefore: boolean };
export function RestoredWorkspace({ copy }: { copy: RestoredTrip }) {
  const [inputs, setInputs] = useState(() => structuredClone(copy.inputs));
  const [revision, setRevision] = useState(0), [dirty, setDirty] = useState(false);
  const [editing, setEditing] = useState<EditSession | null>(null), editRef = useRef<EditSession | null>(null);
  const [notices, setNotices] = useState<string[]>([]);
  const serial = useRef(0), alive = useRef(true);
  const markDirty = useCallback(() => { if (alive.current) setDirty(true); }, []);
  useEffect(() => { alive.current = true; return () => { alive.current = false; editRef.current = null; }; }, []);
  useEffect(() => {
    if (!dirty) return;
    const leave = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", leave);
    return () => window.removeEventListener("beforeunload", leave);
  }, [dirty]);
  const edit = (workspace: WorkspaceEdit) => {
    if (!alive.current || editRef.current || !workspace.isCurrent()) return;
    const next = { id: ++serial.current, workspace, dirtyBefore: dirty };
    editRef.current = next; setEditing(next);
  };
  const confirm = (session: EditSession, request: TripRequest) => {
    if (!alive.current || editRef.current !== session || !session.workspace.isCurrent()) return;
    try {
      const next = applyEditedRequirements(session.workspace.read(), request);
      // All conversion succeeds before synchronously invalidating the old workspace.
      session.workspace.invalidate(); editRef.current = null; ++serial.current;
      setInputs(next.inputs); setNotices(next.notices); setRevision(v => v + 1); setEditing(null); markDirty();
    } catch (error) { setNotices([error instanceof Error ? error.message : "需求无效，未应用修改。"]); }
  };
  return <>
    <section aria-label="历史工作副本来源" className="mb-6 rounded-2xl border border-amber-200 bg-amber-50 p-5 text-sm leading-7">
      <h1 className="text-2xl font-semibold">继续规划 · 独立工作副本</h1>
      <p className="mt-3">基于“{copy.source.name}”创建。新打开时尚未重新生成，原存档及原标签页不受影响。</p>
      <p>原保存时间：{new Date(copy.source.saved_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}；原生成时间：{new Date(copy.source.snapshot.schedule.generated_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}。</p>
      <p>未保存的副本编辑是临时状态，刷新会丢失并重新从原存档开始。没有自动保存；另存也不会覆盖原记录。离开提示保护本页工作状态，已保存快照不受影响。</p>
      <p>只恢复原需求、排程输入和费用原文，没有更早的候选池、排除记录、天气、AI 解读、标注、调整预览或地图绑定。主动生成会重新查询所选交通，但不会重新核验历史 POI 名称、地址或坐标。</p>
    </section>
    {notices.length > 0 && <div role="status" className="mb-5 rounded-xl bg-white p-4 text-sm leading-6">{notices.map(n => <p key={n}>{n}</p>)}</div>}
    {editing && <section aria-label="编辑副本需求" className="mb-6" onChangeCapture={markDirty}>
      <p className="mb-4 text-sm">取消保留当前副本。确认后清除旧正式草案；日期或人数变化将清空全部费用估算，住宿、必去或兴趣变化将清空可选清单。</p>
      <TripRequestForm key={editing.id} initialRequest={inputs.request} onConfirm={request => confirm(editing, request)} onCancel={() => {
        if (!alive.current || editRef.current !== editing) return;
        editRef.current = null; ++serial.current; setEditing(null); setDirty(editing.dirtyBefore);
      }} />
    </section>}
    <div hidden={!!editing}>
      <PlanningWorkspace key={`${copy.sessionId}:${revision}`} request={inputs.request} initial={inputs} onEdit={edit} onDirty={markDirty} />
    </div>
    <details className="mt-8 rounded-2xl border border-[#315f51]/30 bg-white p-5">
      <summary className="cursor-pointer font-semibold">只读历史参考 · 不是新正式草案</summary>
      <div className="mt-5"><SavedTripDetails detail={copy.source} allowRestore={false} /></div>
    </details>
  </>;
}

export default function RestoredTripPage({ recordId }: { recordId: string }) {
  const source = useRestoredTrip(recordId);
  return <main className="mx-auto min-h-screen max-w-7xl bg-[#f4f2ec] px-4 py-8 text-[#18201d] sm:px-8">
    <header className="mb-7 flex flex-wrap items-center justify-between gap-4"><button type="button" onClick={() => window.open("/", "_blank", "noopener,noreferrer")} className="min-h-11 font-semibold">TripPilot · 首页（新标签页）</button><SavedHistory /></header>
    {source.error ? <section role="alert" className="rounded-xl bg-white p-5"><p>{source.error}</p><button onClick={source.retry} className="mt-4 min-h-11 rounded-xl border px-4 py-2">重试读取存档</button></section>
      : source.copy ? <RestoredWorkspace key={source.copy.sessionId} copy={source.copy} /> : <p role="status">正在读取并校验历史快照，尚未创建副本…</p>}
  </main>;
}

"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { applyEditedRequirements, type WorkspaceInputs } from "@/lib/restore-trip";
import type { SavedDetail } from "@/lib/saved-trips";
import type { TripRequest } from "@/types/trip";
import PlanningWorkspace, { type WorkspaceEdit } from "./planning-workspace";
import TripRequestForm from "../trip-request-form";
import { SavedTripDetails } from "./saved-history";

type EditSession = { id: number; workspace: WorkspaceEdit; dirtyBefore: boolean };
export type PlanningOrigin = { kind: "new" } | { kind: "history"; source: SavedDetail };
export default function EditablePlanningSession({ initial, sessionId, origin }: { initial: WorkspaceInputs; sessionId: string; origin: PlanningOrigin }) {
  const [inputs, setInputs] = useState(() => structuredClone(initial));
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
    {origin.kind === "history" ? <section aria-label="历史工作副本来源" className="mb-6 rounded-2xl border border-amber-200 bg-amber-50 p-5 text-sm leading-7">
      <h1 className="text-2xl font-semibold">继续规划 · 独立工作副本</h1>
      <p className="mt-3">基于“{origin.source.name}”创建。新打开时尚未重新生成，原存档及原标签页不受影响。</p>
      <p>原保存时间：{new Date(origin.source.saved_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}；原生成时间：{new Date(origin.source.snapshot.schedule.generated_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}。</p>
      <p>未保存的副本编辑是临时状态，刷新会丢失并重新从原存档开始。没有自动保存；另存也不会覆盖原记录。离开提示保护本页工作状态，已保存快照不受影响。</p>
      <p>只恢复原需求、排程输入和费用原文，没有更早的候选池、排除记录、天气、AI 解读、标注、调整预览或地图绑定。主动生成会重新查询所选交通，但不会重新核验历史 POI 名称、地址或坐标。</p>
    </section> : <section className="mb-6 rounded-2xl bg-white p-5 text-sm leading-7">
      <h1 className="text-2xl font-semibold">上海旅行 · 正式规划工作区</h1>
      <p>从本次确认的需求开始规划。请主动生成草案，再核对与保存；刷新会丢失未保存的工作，没有自动保存。</p>
    </section>}
    {notices.length > 0 && <div role="status" className="mb-5 rounded-xl bg-white p-4 text-sm leading-6">{notices.map(n => <p key={n}>{n}</p>)}</div>}
    {editing && <section aria-label="编辑规划需求" className="mb-6" onChangeCapture={markDirty}>
      <p className="mb-4 text-sm">取消保留当前工作。确认后清除旧正式草案；日期或人数变化将清空全部费用估算，住宿、必去或兴趣变化将清空可选清单。</p>
      <TripRequestForm key={editing.id} initialRequest={inputs.request} onConfirm={request => confirm(editing, request)} onCancel={() => {
        if (!alive.current || editRef.current !== editing) return;
        editRef.current = null; ++serial.current; setEditing(null); setDirty(editing.dirtyBefore);
      }} />
    </section>}
    <div hidden={!!editing}>
      <PlanningWorkspace key={`${sessionId}:${revision}`} request={inputs.request} initial={inputs} origin={origin.kind} onEdit={edit} onDirty={markDirty} />
    </div>
    {origin.kind === "history" && <details className="mt-8 rounded-2xl border border-[#315f51]/30 bg-white p-5">
      <summary className="cursor-pointer font-semibold">只读历史参考 · 不是新正式草案</summary>
      <div className="mt-5"><SavedTripDetails detail={origin.source} allowRestore={false} /></div>
    </details>}
  </>;
}

"use client";
import { useRestoredTrip } from "@/lib/use-restored-trip";
import type { RestoredTrip } from "@/lib/restore-trip";
import EditablePlanningSession from "./editable-planning-session";
import SavedHistory from "./saved-history";

export function RestoredWorkspace({ copy }: { copy: RestoredTrip }) {
  return <EditablePlanningSession initial={copy.inputs} sessionId={copy.sessionId} origin={{ kind: "history", source: copy.source }} />;
}
export default function RestoredTripPage({ recordId }: { recordId: string }) {
  const source = useRestoredTrip(recordId);
  return <main className="mx-auto min-h-screen max-w-7xl bg-[#f4f2ec] px-4 py-8 text-[#18201d] sm:px-8">
    <header className="mb-7 flex flex-wrap items-center justify-between gap-4"><button type="button" onClick={() => window.open("/", "_blank", "noopener,noreferrer")} className="min-h-11 font-semibold">TripPilot · 首页（新标签页）</button><SavedHistory /></header>
    {source.error ? <section role="alert" className="rounded-xl bg-white p-5"><p>{source.error}</p><button onClick={source.retry} className="mt-4 min-h-11 rounded-xl border px-4 py-2">重试读取存档</button></section>
      : source.copy ? <RestoredWorkspace key={source.copy.sessionId} copy={source.copy} /> : <p role="status">正在读取并校验历史快照，尚未创建副本…</p>}
  </main>;
}

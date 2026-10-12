"use client";
import { useRef, useState, type ReactNode } from "react";
import TripRequestForm from "@/components/trip-request-form";
import { newWorkspaceInputs, type WorkspaceInputs } from "@/lib/restore-trip";
import type { TripRequest } from "@/types/trip";
import EditablePlanningSession from "./editable-planning-session";

export default function TripPlanner({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<{ id: string; inputs: WorkspaceInputs } | null>(null);
  const confirmed = useRef(false);
  const confirm = (request: TripRequest) => {
    if (confirmed.current) return;
    const inputs = newWorkspaceInputs(request);
    confirmed.current = true;
    setSession({ id: crypto.randomUUID(), inputs });
  };
  return <div id="top" className="pb-12 pt-8 sm:pt-12 lg:pb-20">
    {session ? <EditablePlanningSession key={session.id} sessionId={session.id} initial={session.inputs} origin={{ kind: "new" }} />
      : <div className="grid gap-10 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] lg:gap-12">{children}<TripRequestForm onConfirm={confirm} /></div>}
  </div>;
}

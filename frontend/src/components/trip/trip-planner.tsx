"use client";

import { useRef, useState, type ReactNode } from "react";
import TripRequestForm from "@/components/trip-request-form";
import TripPlanResult from "@/components/trip-plan-result";
import type { TripPlan } from "@/types/trip";

export default function TripPlanner({ children }: { children: ReactNode }) {
  const [plan, setPlan] = useState<TripPlan | null>(null);
  const formRegion = useRef<HTMLDivElement>(null);

  function editRequest() {
    setPlan(null);
    requestAnimationFrame(() => {
      formRegion.current?.querySelector<HTMLInputElement>("input")?.focus();
    });
  }

  return (
    <div id="top" className="pb-12 pt-8 sm:pt-12 lg:pb-20">
      {/* Keep the form mounted so returning from results preserves every input. */}
      <div hidden={plan !== null} ref={formRegion}>
        <div className="grid gap-10 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] lg:gap-12">
          {children}
          <TripRequestForm onSuccess={setPlan} />
        </div>
      </div>
      {plan && <TripPlanResult plan={plan} onEdit={editRequest} />}
    </div>
  );
}

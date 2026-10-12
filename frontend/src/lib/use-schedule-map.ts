"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { scheduleMapData } from "./schedule-map";
import type { ScheduleResponse } from "@/types/schedule";

export type FormalMapContext = { isCurrent: () => boolean };
type Selection = { placeId: string; target: "home" | "visit"; itemIndex?: number };

// Mounted with the official draft version, not generated_at (which can repeat).
export function useScheduleMap(response: ScheduleResponse, context?: FormalMapContext) {
  const [{ date, generation }, setDate] = useState({ date: response.days[0]?.date ?? "", generation: 0 });
  const [opened, setOpened] = useState(false), [initialized, setInitialized] = useState(false);
  const [selection, setSelection] = useState<Selection | null>(null), [selectionVersion, setSelectionVersion] = useState(0);
  const epoch = useRef(0), alive = useRef(true);
  const mapRegion = useRef<HTMLElement>(null), home = useRef<HTMLDivElement>(null);
  const cards = useRef(new Map<number, HTMLLIElement>());
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const data = useMemo(() => scheduleMapData(response, date), [response, date]);
  const isCurrent = () => alive.current && epoch.current === generation && (!context || context.isCurrent());
  const reveal = () => { setInitialized(true); setOpened(true); };
  const locate = (target: Selection, fromMarker: boolean) => {
    if (!context || !isCurrent()) return;
    if (target.target === "visit" && !data.visits.some(v => v.itemIndex === target.itemIndex && v.placeId === target.placeId)) return;
    if (target.target === "home" && target.placeId !== data.home.id) return;
    reveal(); setSelection(target); setSelectionVersion(v => v + 1);
    const element = fromMarker ? target.target === "home" ? home.current : cards.current.get(target.itemIndex!) : mapRegion.current;
    element?.focus({ preventScroll: true }); element?.scrollIntoView({ block: "center", behavior: "smooth" });
  };
  return { date, data, opened, initialized, selection, selectionVersion, mapRegion, home, cards, isCurrent,
    selectDate: (next: string) => {
      if (!isCurrent() || next === date || !response.days.some(d => d.date === next)) return;
      epoch.current++; setDate({ date: next, generation: epoch.current }); setSelection(null);
    },
    toggle: () => { if (!isCurrent()) return; if (opened) setOpened(false); else reveal(); },
    locateVisit: (itemIndex: number) => { const v = data.visits.find(v => v.itemIndex === itemIndex); if (v) locate({ placeId: v.placeId, target: "visit", itemIndex }, false); },
    locateHome: () => locate({ placeId: data.home.id, target: "home" }, false),
    selectMarker: (placeId: string) => {
      // A shared accommodation/visit marker always lands on its dual-role description.
      if (placeId === data.home.id) locate({ placeId, target: "home" }, true);
      else { const v = data.visits.find(v => v.placeId === placeId); if (v) locate({ placeId, target: "visit", itemIndex: v.itemIndex }, true); }
    },
  };
}

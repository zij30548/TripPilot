"use client";
import { useEffect, useRef, useState } from "react";
import { readTrip } from "./saved-trips";
import { restoreSavedTrip, type RestoredTrip } from "./restore-trip";

type State = { id: string; attempt: number; copy: RestoredTrip | null; error: string | null };
export function useRestoredTrip(id: string) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<State>({ id: "", attempt: -1, copy: null, error: null });
  const [observedId, setObservedId] = useState(id);
  if (observedId !== id) {
    setObservedId(id);
    setState({ id: "", attempt: -1, copy: null, error: null });
  }
  const version = useRef(0);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    const active = new AbortController(), generation = ++version.current;
    controller.current = active;
    void readTrip(id, active.signal).then(detail => {
      if (active.signal.aborted || version.current !== generation) return;
      const copy = restoreSavedTrip(detail, crypto.randomUUID());
      setState({ id, attempt, copy, error: null });
    }).catch(error => {
      if (active.signal.aborted || version.current !== generation) return;
      setState({ id, attempt, copy: null, error: error instanceof Error ? error.message : "读取历史失败，未创建副本。" });
    });
    return () => { active.abort(); };
  }, [id, attempt]);
  // Never render the previous source during the render-before-effect interval.
  const current = observedId === id && state.id === id && state.attempt === attempt ? state : null;
  const renderedVersion = version.current;
  const retry = () => { if (controller.current?.signal.aborted || renderedVersion !== version.current || state.id !== id || state.attempt !== attempt || !state.error) return; ++version.current; setAttempt(v => v + 1); };
  return { copy: current?.copy ?? null, error: current?.error ?? null, retry };
}

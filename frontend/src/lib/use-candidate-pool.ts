"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CandidateError, queryCandidates } from "@/lib/candidates-api";
import { isCandidateRequest, type Candidate, type CandidateResponse } from "@/types/candidates";
import type { Place } from "@/types/place";
import type { TripRequest } from "@/types/trip";

export type CandidatePoolState = {
  status: "idle" | "loading" | "success" | "partial" | "failed";
  response: CandidateResponse | null;
  lastAttempt: CandidateResponse | null;
  message: string | null;
  showingPrevious: boolean;
};
export type CandidatePoolSnapshot = {
  response: CandidateResponse | null;
  lastAttempt: CandidateResponse | null;
  excludedIds: ReadonlySet<string>;
  loading: boolean;
  showingPrevious: boolean;
  message: string | null;
};
const emptyState = (): CandidatePoolState => ({ status: "idle", response: null, lastAttempt: null, message: null, showingPrevious: false });

// Owned by the whole result, not the keyed daily explorer. Editing invalidates synchronously;
// result unmount also aborts. No global store, browser storage or automatic request.
export function useCandidatePool(request: TripRequest, onInputsChange?: (snapshot: CandidatePoolSnapshot) => void) {
  const [state, setState] = useState<CandidatePoolState>(emptyState);
  const [excludedIds, setExcludedIds] = useState<ReadonlySet<string>>(() => new Set());
  const version = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const pending = useRef(false);
  const lastValid = useRef<CandidateResponse | null>(null);
  const snapshot = useRef<CandidatePoolSnapshot>({ response: null, lastAttempt: null, excludedIds: new Set(), loading: false, showingPrevious: false, message: null });
  const publish = useCallback((patch: Partial<CandidatePoolSnapshot>) => {
    snapshot.current = { ...snapshot.current, ...patch, excludedIds: new Set(patch.excludedIds ?? snapshot.current.excludedIds) };
    // This runs inside the originating action/response handler, never a later effect.
    // Downstream previews abort before the pool changes or a new search starts.
    onInputsChange?.(snapshot.current);
  }, [onInputsChange]);

  const cancelPending = useCallback(() => {
    version.current += 1;
    controller.current?.abort();
    controller.current = null;
    pending.current = false;
  }, []);
  const invalidate = useCallback(() => {
    cancelPending();
    lastValid.current = null;
    publish({ response: null, lastAttempt: null, excludedIds: new Set(), loading: false, showingPrevious: false, message: null });
    setState(emptyState());
    setExcludedIds(new Set());
  }, [cancelPending, publish]);
  useEffect(() => cancelPending, [cancelPending]);

  const candidateRequest: unknown = {
    accommodation_place: request.accommodation_place,
    must_visit_places: request.must_visit_places ?? [],
    interests: request.interests,
  };
  const canQuery = isCandidateRequest(candidateRequest);
  const query = async (): Promise<boolean> => {
    if (pending.current) return false;
    if (!isCandidateRequest(candidateRequest)) {
      setState((previous) => ({ ...previous, status: "failed", message: "请先返回修改需求，确认住宿参考点及支持的兴趣。", showingPrevious: previous.response !== null }));
      return false;
    }
    cancelPending();
    const requestVersion = version.current;
    const requestController = new AbortController();
    controller.current = requestController;
    pending.current = true;
    publish({ loading: true, showingPrevious: lastValid.current !== null, message: null });
    setState((previous) => ({ ...previous, status: "loading", message: null, showingPrevious: previous.response !== null }));
    try {
      const response = await queryCandidates(candidateRequest, requestController.signal);
      if (requestVersion !== version.current || requestController.signal.aborted) return false;
      if (response.status === "failed") {
        publish({ response: lastValid.current, lastAttempt: response, loading: false, showingPrevious: lastValid.current !== null,
          message: lastValid.current ? "更新失败，当前展示上次获取的候选及排除选择。" : "未能获取可选地点，必去要求仍保留，请主动重试。" });
        setState({ status: "failed", response: lastValid.current, lastAttempt: response,
          message: lastValid.current ? "更新失败，当前展示上次获取的候选及排除选择。" : "未能获取可选地点，必去要求仍保留，请主动重试。",
          showingPrevious: lastValid.current !== null });
        return false;
      }
      lastValid.current = response;
      publish({ response, lastAttempt: response, loading: false, showingPrevious: false,
        message: response.status === "partial" ? "部分搜索未完成，当前仅展示本次成功获取的候选，可主动重新获取。" : null });
      setState({ status: response.status, response, lastAttempt: response, showingPrevious: false,
        message: response.status === "partial" ? "部分搜索未完成，当前仅展示本次成功获取的候选，可主动重新获取。" : null });
      return true;
    } catch (error) {
      if (requestVersion !== version.current || requestController.signal.aborted) return false;
      publish({ response: lastValid.current, lastAttempt: null, loading: false, showingPrevious: lastValid.current !== null,
        message: `${error instanceof CandidateError ? error.message : "获取候选地点失败，请主动重试。"}${lastValid.current ? "当前展示上次获取的候选及排除选择。" : "必去要求仍保留。"}` });
      setState({ status: "failed", response: lastValid.current, lastAttempt: null, showingPrevious: lastValid.current !== null,
        message: `${error instanceof CandidateError ? error.message : "获取候选地点失败，请主动重试。"}${lastValid.current ? "当前展示上次获取的候选及排除选择。" : "必去要求仍保留。"}` });
      return false;
    } finally {
      if (requestVersion === version.current) { controller.current = null; pending.current = false; }
    }
  };
  const candidates: Candidate[] = state.response?.candidates ?? (request.must_visit_places ?? []).map((place) => ({
    place, role: "must_visit", retrieval_sources: [],
  }));
  const exclude = (id: string) => {
    if (!candidates.some((candidate) => candidate.role === "optional" && candidate.place.id === id)) return;
    const next = new Set([...snapshot.current.excludedIds, id]);
    publish({ excludedIds: next });
    setExcludedIds(next);
  };
  const restore = (id: string) => {
    if (!snapshot.current.excludedIds.has(id)) return;
    const next = new Set(snapshot.current.excludedIds);
    next.delete(id);
    publish({ excludedIds: next });
    setExcludedIds(next);
  };
  const activePlaces: Place[] = candidates.filter((candidate) => candidate.role === "must_visit" || !excludedIds.has(candidate.place.id)).map((candidate) => candidate.place);
  const getActivePlaces = () => activePlaces;
  return { state, candidates, excludedIds, activePlaces, getActivePlaces, query, invalidate, exclude, restore, canQuery };
}

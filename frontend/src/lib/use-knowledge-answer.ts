"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { answerKnowledge } from "./knowledge-answer-api";
import type { KnowledgeResponse } from "./knowledge";
import type { AnswerResponse } from "./knowledge-answer";

type State = { status: "idle" | "loading" | "success" | "failed"; response: AnswerResponse | null; message: string | null };
const empty = (): State => ({ status: "idle", response: null, message: null });
export function useKnowledgeAnswer() {
  const source = useRef<KnowledgeResponse | null>(null), version = useRef(0), alive = useRef(true), pending = useRef<AbortController | null>(null);
  const [state, setState] = useState<State>(empty);
  const clear = useCallback(() => { version.current++; pending.current?.abort(); pending.current = null; source.current = null; }, []);
  const invalidate = useCallback(() => { clear(); setState(empty()); }, [clear]);
  const bind = useCallback((value: KnowledgeResponse) => { invalidate(); source.current = value; }, [invalidate]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; clear(); }; }, [clear]);
  const displayed = version.current;
  const generate = async () => {
    if (!alive.current || displayed !== version.current || pending.current || !source.current) return;
    const snapshot = source.current, id = ++version.current, controller = new AbortController(); pending.current = controller;
    setState({ ...empty(), status: "loading" });
    try {
      const response = await answerKnowledge(snapshot, crypto.randomUUID(), controller.signal);
      if (alive.current && version.current === id && !controller.signal.aborted) setState({ status: "success", response, message: null });
    } catch (error) {
      if (alive.current && version.current === id && !controller.signal.aborted) setState({ ...empty(), status: "failed", message: error instanceof Error ? error.message : "回答未完成，请主动重试。" });
    } finally { if (alive.current && version.current === id) pending.current = null; }
  };
  const cancel = () => {
    if (!alive.current || displayed !== version.current) return;
    version.current++; pending.current?.abort(); pending.current = null;
    setState({ ...empty(), message: "已取消回答，检索资料保留。上游若已发出仍可能计费。" });
  };
  return { state, invalidate, bind, generate, cancel };
}

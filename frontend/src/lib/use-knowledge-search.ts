"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { searchKnowledge } from "./knowledge-api";
import { isKnowledgeRequest, type KnowledgeResponse } from "./knowledge";
import { useKnowledgeAnswer } from "./use-knowledge-answer";

type Input = { venue_id: string; question: string };
type State = { status: "idle" | "loading" | "success" | "failed"; response: KnowledgeResponse | null; message: string | null };
const empty = (): State => ({ status: "idle", response: null, message: null });
export function useKnowledgeSearch() {
  const answer = useKnowledgeAnswer();
  const [input, setInput] = useState<Input>({ venue_id: "", question: "" });
  const ref = useRef(input), version = useRef(0), alive = useRef(true), pending = useRef<AbortController | null>(null);
  const [state, setState] = useState<State>(empty);
  const invalidateAnswer = answer.invalidate;
  const invalidate = useCallback(() => { version.current++; pending.current?.abort(); pending.current = null; invalidateAnswer(); }, [invalidateAnswer]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; invalidate(); }; }, [invalidate]);
  const update = (value: Partial<Input>) => {
    if (!alive.current) return;
    invalidate(); ref.current = { ...ref.current, ...value }; setInput(ref.current); setState(empty());
  };
  const query = async () => {
    if (!alive.current || pending.current) return;
    const request = { ...ref.current, question: ref.current.question.trim(), limit: 5 };
    if (!isKnowledgeRequest(request)) { setState({ ...empty(), status: "failed", message: "请选择馆区并输入2至160字的问题。" }); return; }
    invalidate(); const id = version.current, controller = new AbortController(); pending.current = controller;
    setState({ ...empty(), status: "loading" });
    try {
      const response = await searchKnowledge(request, controller.signal);
      if (alive.current && version.current === id && !controller.signal.aborted) { answer.bind(response); setState({ status: "success", response, message: null }); }
    } catch {
      if (alive.current && version.current === id && !controller.signal.aborted) setState({ ...empty(), status: "failed", message: "资料查询未完成，请检查服务后主动重试。" });
    } finally { if (version.current === id) pending.current = null; }
  };
  const displayedVersion = version.current;
  const cancel = () => {
    if (!alive.current || displayedVersion !== version.current) return;
    invalidate(); setState({ ...empty(), message: "已取消本次查询。" });
  };
  return { input, state, update, query, cancel, answer };
}

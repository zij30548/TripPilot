"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { authSnapshot, subscribeAuth } from "./auth-session";
import { readNationalKnowledgeCatalog, searchNationalKnowledge } from "./knowledge-national-api";
import { isNationalKnowledgeRequest, type KnowledgeVenueKind, type NationalKnowledgeCatalog, type NationalKnowledgeResponse } from "./knowledge-national";

export type NationalKnowledgeInput = { city_id: string; kind: KnowledgeVenueKind | ""; venue_id: string; question: string; visit_date: string };
type ResultState = { status: "idle" | "loading" | "success" | "failed"; response: NationalKnowledgeResponse | null; message: string | null };
type CatalogState = { status: "idle" | "loading" | "success" | "failed"; catalog: NationalKnowledgeCatalog | null; message: string | null };
const emptyInput = (): NationalKnowledgeInput => ({ city_id: "", kind: "", venue_id: "", question: "", visit_date: "" });
const emptyResult = (): ResultState => ({ status: "idle", response: null, message: null });
const emptyCatalog = (): CatalogState => ({ status: "idle", catalog: null, message: null });

/** Local directory reads are separate from explicit searches. No answer workflow. */
export function useNationalKnowledge(active = true) {
  const [input, setInput] = useState(emptyInput), inputRef = useRef(input);
  const [state, setState] = useState<ResultState>(emptyResult);
  const [directory, setDirectory] = useState<CatalogState>(emptyCatalog), catalogRef = useRef<NationalKnowledgeCatalog | null>(null);
  const alive = useRef(true), version = useRef(0), catalogVersion = useRef(0), owner = useRef(authSnapshot().epoch);
  const pending = useRef<AbortController | null>(null), catalogPending = useRef<AbortController | null>(null);
  const invalidate = useCallback(() => { ++version.current; const request = pending.current; pending.current = null; request?.abort(); }, []);
  const invalidateCatalog = useCallback(() => { ++catalogVersion.current; const request = catalogPending.current; catalogPending.current = null; request?.abort(); }, []);
  useEffect(() => {
    alive.current = true;
    const unsubscribe = subscribeAuth(() => {
      const next = authSnapshot().epoch;
      if (owner.current === next || !alive.current) return;
      owner.current = next;
      invalidate(); invalidateCatalog();
      inputRef.current = emptyInput(); catalogRef.current = null;
      setInput(inputRef.current); setState(emptyResult()); setDirectory(emptyCatalog());
    });
    return () => { alive.current = false; unsubscribe(); invalidate(); invalidateCatalog(); };
  }, [invalidate, invalidateCatalog]);
  const loadCatalog = useCallback(async () => {
    if (!alive.current || catalogPending.current) return;
    invalidate(); invalidateCatalog();
    const id = catalogVersion.current, epoch = authSnapshot().epoch, controller = new AbortController();
    catalogPending.current = controller;
    catalogRef.current = null;
    setState(emptyResult()); setDirectory({ ...emptyCatalog(), status: "loading" });
    const current = () => alive.current && catalogPending.current === controller && catalogVersion.current === id && authSnapshot().epoch === epoch && !controller.signal.aborted;
    try {
      const catalog = await readNationalKnowledgeCatalog(controller.signal);
      if (!current()) return;
      catalogRef.current = catalog;
      const value = inputRef.current;
      if (value.city_id && !catalog.cities.some(row => row.id === value.city_id)) {
        inputRef.current = { ...value, city_id: "", venue_id: "" }; setInput(inputRef.current);
      } else if (value.venue_id && !catalog.venues.some(row => row.id === value.venue_id && row.city_id === value.city_id && (!value.kind || row.kind === value.kind))) {
        inputRef.current = { ...value, venue_id: "" }; setInput(inputRef.current);
      }
      setDirectory({ status: "success", catalog, message: null });
    } catch (error) {
      if (current()) setDirectory({ ...emptyCatalog(), status: "failed", message: error instanceof Error ? error.message : "资料目录无法加载，请主动重试。" });
    } finally { if (catalogPending.current === controller && catalogVersion.current === id) catalogPending.current = null; }
  }, [invalidate, invalidateCatalog]);
  // Loading the local, reviewed catalogue is not a keyword search or an external call.
  // Remain mounted between product pages; navigation never discards results.
  useEffect(() => {
    if (active && directory.status === "idle") void loadCatalog();
  }, [active, directory.status, loadCatalog]);
  const update = (patch: Partial<NationalKnowledgeInput>) => {
    if (!alive.current) return;
    invalidate();
    const before = inputRef.current;
    const next = { ...before, ...patch };
    if (next.city_id !== before.city_id || next.kind !== before.kind) next.venue_id = "";
    inputRef.current = next; setInput(next); setState(emptyResult());
  };
  const query = async () => {
    if (!alive.current || pending.current) return;
    const value = inputRef.current, catalog = catalogRef.current;
    const request = { city_id: value.city_id, venue_id: value.venue_id, question: value.question.trim(), visit_date: value.visit_date || null, limit: 5 };
    const venue = catalog?.venues.find(row => row.id === value.venue_id);
    if (!catalog || !isNationalKnowledgeRequest(request, catalog) || !venue || value.kind && value.kind !== venue.kind) {
      setState({ ...emptyResult(), status: "failed", message: "请选择已收录城市和场馆，并输入2至160字的问题；参观日期可留空。" }); return;
    }
    invalidate();
    const id = version.current, epoch = authSnapshot().epoch, controller = new AbortController();
    pending.current = controller; setState({ ...emptyResult(), status: "loading" });
    const current = () => alive.current && pending.current === controller && version.current === id && authSnapshot().epoch === epoch && catalogRef.current === catalog && !controller.signal.aborted;
    try {
      const response = await searchNationalKnowledge(request, catalog, controller.signal);
      if (current()) setState({ status: "success", response, message: null });
    } catch (error) {
      if (current()) setState({ ...emptyResult(), status: "failed", message: error instanceof Error ? error.message : "资料查询未完成，请主动重试。" });
    } finally { if (pending.current === controller && version.current === id) pending.current = null; }
  };
  const displayedVersion = version.current;
  const cancel = () => {
    if (!alive.current || displayedVersion !== version.current) return;
    invalidate(); setState({ ...emptyResult(), message: "已取消本次资料查询。" });
  };
  return { input, state, directory, update, query, cancel, loadCatalog };
}

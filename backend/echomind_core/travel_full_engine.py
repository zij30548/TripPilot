"""Full travel composition of the preserved EchoMind algorithms.

Explicit opt-in Python composition, not the legacy HTTP/default runtime. A
single gate, metered provider and finite budget cover fusion -> routing ->
specialists -> original retrieval/composition. No template preparation, service
startup, environment discovery or paid call occurs at construction time.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import replace
from datetime import date
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from echomind_core.travel_memory import TravelMemoryPort
    from echomind_core.travel_observation import TravelObservationState
    from echomind_core.travel_vector_recall import ReviewedVectorRecall

from core.config import Settings
from core.llm_utils import bounded_json
from core.schemas.dialogue import DialogueSelectionContext, group_schema
from core.usage_runtime import MeteredAsyncClient
from echomind_core.execution import (
    CandidateScope, ExecutionError, ExecutionGate, GuardedToolRegistry, SafeToolManager,
    ToolDeclaration, _task_client, execution_gate,
)
from echomind_core.provider import MAX_TOOLS, MODEL
from echomind_core.travel_bridge import TravelToolBridge
from echomind_core.travel_budget import TravelTaskBudget
from echomind_core.travel_capabilities import ReviewedKnowledgePort, make_role_tools
from echomind_core.travel_domain import DOMAIN_VERSION, TravelIntent, TravelIntentRecognizer, TravelRole
from echomind_core.travel_engine import (
    TravelOrchestrator, TravelTurn, _TravelAgent, _TravelRunner, _freeze_selection_context,
)
from echomind_core.travel_package import FrozenTravelDomainPackage, TravelDomainPackage
from echomind_core.travel_routing import ROLE_FOR_SLOT, TravelRoutingPolicy, is_independent_read, routing_projection
from echomind_core.travel_templates import PreparedTravelTemplates
from echomind_core.upstream.agents.agent_orchestrator import (
    AgentCallResult, AgentOrchestrator, AgentType, OrchestratorResult, Request, ResponseComposer, RoutingDecision,
)
from echomind_core.upstream.agents.tools import AgentToolSpec
from echomind_core.upstream.tooling.tool_manager import Tool

VERSION = "echomind-travel-full-v2"


class _DomainAgent(_TravelAgent):
    def _build_system_prompt(self, request: Request) -> str:
        return (self._package.prompt_for(ROLE_FOR_SLOT[self.agent_type], request.message)
                + "\n角色tool_scope仅为权限上限，未注册名称不可调用；本次实际工具：" + "、".join(self.get_tools()))

    async def _call_llm(self, request: Request) -> AgentCallResult:
        result = await super()._call_llm(request)
        projection = await self._travel_bridge.project()
        names = set(result.tools_used)
        messages = [row["data"]["message"] for row in projection.tool_results if row["name"] in names]
        # Composer receives program facts/pending states, not the model's free
        # factual draft or a meaningless terminal marker. Its output still has
        # no authority to overwrite the final program projection.
        return replace(result, content="\n\n".join(messages))


class ReviewedRetrieval:
    """Original rewrite/parallel recall/dedup/rerank, bounded to one venue.

    Trusted construction selects venue/date. Rewrites cannot redirect it. Full
    validated units are projected, never model-written knowledge answers. The
    original rerank's short candidate previews remain a known quality limit.
    """
    def __init__(self, bridge: TravelToolBridge, port: ReviewedKnowledgePort, city_id: str,
                 venue_id: str, *, package: FrozenTravelDomainPackage, visit_date: date | None = None,
                 vector: ReviewedVectorRecall | None = None) -> None:
        from echomind_core.travel_stages import StageClient
        if type(bridge) is not TravelToolBridge or type(port) is not ReviewedKnowledgePort:
            raise ExecutionError("configuration")
        self.bridge, self.port = bridge, port
        callback = port.bound_recall(city_id, venue_id, visit_date)
        self.recall_method = "reviewed-local-bm25"
        if vector is not None:
            from echomind_core.travel_vector_recall import ReviewedVectorRecall
            if (type(vector) is not ReviewedVectorRecall or vector.reviewed is not port
                    or vector.resources.scope != bridge.snapshot.binding.scope
                    or (vector.city_id, vector.venue_id, vector.visit_date) != (city_id, venue_id, visit_date)):
                raise ExecutionError("travel_retrieval_binding")
            callback = vector.bound_recall()
            self.recall_method = "original-knowledgebase-chroma"
        async def recall(arguments, context):
            try:
                return await callback(arguments, context)
            except BaseException as error:
                # Original per-query fallback must not hide a broken scope,
                # corrupted source or cancellation behind other recall hits.
                execution_gate().fail("cancelled" if isinstance(error, asyncio.CancelledError)
                    else getattr(error, "code", "travel_retrieval_failed"))
                raise
        self.manager = SafeToolManager()
        self.manager.register_read_only(Tool(name="reviewed_recall", description="当前审核馆区完整资料",
            schema={"type": "object", "properties": {
                "query": {"type": "string", "minLength": 2, "maxLength": 160},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 5}},
                "required": ["query", "top_k"], "additionalProperties": False},
            handler=recall, timeout_s=20))
        # The original manager uses a single client for both methods. A tiny
        # scoped facade chooses the stage at each call; no algorithm is copied.
        self._rewrite_client = StageClient(package, "rewrite", self.manager._client)
        self._rerank_client = StageClient(package, "rerank", self.manager._client)
        original_rewrite, original_rank = self.manager.rewrite_query, self.manager._rerank_with_status

        async def rewrite(query, n=3):
            previous = self.manager._client
            self.manager._client = self._rewrite_client
            try:
                return await original_rewrite(query, n)
            finally:
                self.manager._client = previous

        async def rank(query, items, top_k):
            previous = self.manager._client
            self.manager._client = self._rerank_client
            try:
                return await original_rank(query, items, top_k)
            finally:
                self.manager._client = previous

        self.manager.rewrite_query, self.manager._rerank_with_status = rewrite, rank
        self._running = False

    def spec(self) -> AgentToolSpec:
        async def search(request: Request, arguments: dict[str, Any]):
            if self._running:
                raise ExecutionError("travel_retrieval_busy")
            self._running = True
            try:
                await self.bridge._check(request)
                scope = execution_gate().scope
                context = {"owner_id": scope.owner_id, "conversation_id": scope.conversation_id,
                    "session_id": scope.session_id, "input_version": scope.input_version}
                found = await self.manager.search_with_rewrite("reviewed_recall", arguments["query"],
                                                               arguments["top_k"], context=context)
                await self.bridge._check(request)
                if not found.success or type(found.data) is not list:
                    raise ExecutionError("travel_retrieval_failed")
                lines = ["资料检索；相关文字不等于问题已回答或今天适用。知识库AI问答未开放。"]
                for item in found.data:
                    source, section = item["source"], item["section"]
                    lines.extend([source["title"] + " · " + section["heading"],
                        "整理摘要：" + section["summary"], "原文短摘录：" + section["excerpt"],
                        "官方来源：" + source["url"], "适用性：" + source["applicability"],
                        "来源发布日期：" + str(source["published_at"] or "未知"),
                        "来源更新日期：" + str(source["updated_at"] or "未知"),
                        "实际获取时间：" + source["fetched_at"], item["applicability_check"]["notice"]])
                return await self.bridge._record("search_knowledge_base", {
                    "kind": "read", "executed": True, "source": "local_reviewed_materials",
                    "message": "\n".join(lines), "retrieval_only": True,
                    "items": found.data, "reranked": found.reranked,
                    "corpus_hash": self.port.corpus_hash, "recall_method": self.recall_method,
                    "binding": self.bridge.snapshot.binding.as_dict()}, request)
            except BaseException as error:
                execution_gate().fail("cancelled" if isinstance(error, asyncio.CancelledError)
                    else getattr(error, "code", "travel_retrieval_failed"))
                raise
            finally:
                self._running = False
        return AgentToolSpec("search_knowledge_base", "当前绑定馆区的原改写、多路召回、去重与重排；只展示资料。",
            {"type": "object", "properties": {
                "query": {"type": "string", "minLength": 2, "maxLength": 160},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 5}},
                "required": ["query", "top_k"], "additionalProperties": False}, search)


class FullTravelOrchestrator(TravelRoutingPolicy, TravelOrchestrator):
    """Original fusion, execution/fallback and parallel Composer on one budget.

    Four original enum slots are compatibility internals; public profiles are
    travel roles. Browser-dependent reads remain pending proposals, not fake
    backend time/price facts. Only the independent schedule+cost pair may run
    in parallel, with strictly read-only specialist tools for that dispatch.
    """
    def __init__(self, bridge: TravelToolBridge, recognizer: TravelIntentRecognizer, *,
                 package: FrozenTravelDomainPackage | None = None,
                 national: ReviewedKnowledgePort | None = None, retrieval: ReviewedRetrieval | None = None,
                 observation: TravelObservationState | None = None,
                 selection_context: DialogueSelectionContext | None = None,
                 memory: TravelMemoryPort | None = None, prepared: PreparedTravelTemplates | None = None) -> None:
        from echomind_core.travel_stages import StageClient
        if (type(bridge) is not TravelToolBridge or type(recognizer) is not TravelIntentRecognizer
                or not bridge.parallel_reads or retrieval is not None and (
                    type(retrieval) is not ReviewedRetrieval or retrieval.bridge is not bridge)
                or prepared is not None and type(prepared) is not PreparedTravelTemplates):
            raise ExecutionError("configuration")
        self.bridge, self._intent_recognizer = bridge, recognizer
        self.package = package or TravelDomainPackage().capture()
        if type(self.package) is not FrozenTravelDomainPackage:
            raise ExecutionError("configuration")
        self._recent_tool_traces, self._pool = deque(maxlen=200), {}
        self._shared_tools, self.classification, self.last_route = {}, None, None
        self.retrieval, self.observation, self.memory = retrieval, observation, memory
        self.prepared = prepared
        self._intent_recognizer.client = StageClient(self.package, "classifier", recognizer.client)
        client = _task_client()
        self._composer = ResponseComposer(StageClient(self.package, "composer", client), MODEL)
        self._selection_json = _freeze_selection_context(selection_context)
        original_recognize = self._intent_recognizer.recognize
        async def classified(message, history=None):
            result = await original_recognize(message, history)
            self.classification = {"intent": result.intent.value, "confidence": result.confidence,
                "intent_group": result.intent_group, "source": "original_fusion",
                "source_scores": dict(result.source_scores)}
            return result
        self._intent_recognizer.recognize = classified
        for slot, role in ROLE_FOR_SLOT.items():
            tools = make_role_tools(bridge, role, national=national)
            if role in {TravelRole.COORDINATOR, TravelRole.PLACE_REFERENCE} and retrieval is not None:
                # Explicit alternative presentation: no duplicate old/local
                # material tool competing with the trusted full-RAG binding.
                for name in ("search_materials", "search_reviewed_materials", "travel_materials"):
                    tools.pop(name, None)
                tools["search_knowledge_base"] = retrieval.spec()
            elif len(tools) > MAX_TOOLS:
                # National direct lookup replaces—not supplements—the old
                # three-venue direct tool in the coordinator's finite schema.
                tools.pop("search_materials", None)
            if len(tools) > MAX_TOOLS:
                raise ExecutionError("travel_tool_limit")
            profile = replace(self.package.profile(role), tool_scope=tuple(tools))
            agent = _DomainAgent(client, MODEL, profile=profile)
            agent.agent_type, agent._travel_bridge = slot, bridge
            agent._selection_json, agent._package = self._selection_json, self.package
            agent.set_shared_tools(tools)
            agent._tool_registry = GuardedToolRegistry(tuple(ToolDeclaration(v, True) for v in tools.values()))
            self._pool[slot] = [agent]
            self._shared_tools.update(tools)
        if observation is not None:
            observation.attach(self, retrieval.manager if retrieval else None)

    def _route_decision(self, request: Request) -> RoutingDecision:
        decision = super()._route_decision(request)
        self.last_route = routing_projection(decision)
        return decision

    async def run(self, request: Request) -> OrchestratorResult:
        # Only the public one-shot runner can admit input; no caller-supplied
        # classification bypasses the preserved fusion chain.
        if request.intent is not None:
            raise ExecutionError("travel_classification_required")
        await self.bridge._check(request)
        # Validate every possible role's domain context before a classifier or
        # memory query can spend an attempt. Align with the runner's 4000-char
        # message bound, not a later hidden 2000-char Skill-only failure.
        for role in TravelRole:
            self.package.prompt_for(role, request.message)
        if self.prepared is not None:
            self.prepared.load_into(self._intent_recognizer._embedding, self._intent_recognizer.templates)
        embedding = self._intent_recognizer._embedding
        if embedding.template_fingerprint(self._intent_recognizer.templates) not in embedding.remote_templates:
            raise ExecutionError("embedding_preparation_required")
        if self.memory is not None:
            context = await self.memory.context_for(request)
            request.context = bounded_json(context, 16_000)
        try:
            result = await AgentOrchestrator.run(self, request)
            return result
        finally:
            if self.observation is not None:
                await self.observation.collect_once(self.retrieval.manager if self.retrieval else None)

    async def run_parallel(self, request: Request, decision: RoutingDecision) -> OrchestratorResult:
        if not is_independent_read(request) or set(decision.agent_types) != {AgentType.TECHNICAL, AgentType.BILLING}:
            raise ExecutionError("travel_parallel_gated")
        # Both specialists receive exactly one read choice. No mutation can
        # sneak through because a classifier selected a read-like intent.
        saved = []
        coordinator = self._pool[AgentType.GENERAL][0]
        saved.append((coordinator, coordinator._shared_tools, coordinator._tool_registry))
        fallback_tools = {name: replace(coordinator._shared_tools[name], input_schema=group_schema((action,)))
            for name, action in (("travel_schedule", "read_schedule"), ("travel_budget", "read_budget"))}
        coordinator.set_shared_tools(fallback_tools)
        coordinator._tool_registry = GuardedToolRegistry(tuple(ToolDeclaration(v, True) for v in fallback_tools.values()))
        for slot, name, action in ((AgentType.TECHNICAL, "travel_schedule", "read_schedule"),
                                    (AgentType.BILLING, "travel_budget", "read_budget")):
            agent = self._pool[slot][0]
            saved.append((agent, agent._shared_tools, agent._tool_registry))
            spec = agent._shared_tools[name]
            checked = replace(spec, input_schema=group_schema((action,)))
            agent.set_shared_tools({name: checked})
            agent._tool_registry = GuardedToolRegistry((ToolDeclaration(checked, True),))
        try:
            return await AgentOrchestrator.run_parallel(self, request, decision)
        finally:
            for agent, tools, registry in saved:
                agent.set_shared_tools(tools)
                agent._tool_registry = registry


class FullTravelCandidate:
    """Explicit offline/full-runtime entry; never changes the default website.

    A prepared owner-bound intent index is required before a single send. The
    final business projection comes from actual guarded tools, not Composer's
    free prose. Composer executes for collaboration but is not fact authority.
    """
    def __init__(self, bridge: TravelToolBridge, recognizer: TravelIntentRecognizer, **options: Any) -> None:
        self.orchestrator = FullTravelOrchestrator(bridge, recognizer, **options)
        self.bridge, self._runner, self._used = bridge, _TravelRunner(self.orchestrator), False

    async def run(self, request: Request, gate: ExecutionGate, settings: Settings,
                  client: MeteredAsyncClient) -> TravelTurn:
        if (self._used or type(gate.budget) is not TravelTaskBudget
                or gate.scope != self.bridge.snapshot.binding.scope
                or request.request_id != self.bridge.snapshot.binding.operation_id):
            raise ExecutionError("travel_context_changed")
        self._used = True
        await self.bridge._check()
        memory = self.orchestrator.memory
        if memory is not None:
            memory.begin(gate)
        try:
            run = await self._runner.run(request, gate, settings, client)
        finally:
            if memory is not None:
                memory.end()
        versions = {"candidate": VERSION, "domain": DOMAIN_VERSION,
            "skills": self.orchestrator.package.summary(), "budget": gate.budget.summary(),
            "classification": self.orchestrator.classification, "routing": self.orchestrator.last_route,
            "composer_fact_authority": False,
            "prepared_templates": self.orchestrator.prepared.summary() if self.orchestrator.prepared else None,
            "memory": "explicit_scoped_port" if memory is not None else "not_attached"}
        try:
            await self.bridge._check()
            latest = gate.current()
            if type(latest) is not CandidateScope or latest != gate.scope:
                raise ExecutionError("travel_context_changed")
            if run.state != "complete" or run.result is None:
                raise ExecutionError(run.code or "travel_execution_failed")
            if any(row.get("success") is not True or row.get("result_success") is False
                   for row in run.result.tool_traces if "tool_name" in row):
                raise ExecutionError("travel_tool_failed")
            projection = await self.bridge.project()
            latest = gate.current()
            if type(latest) is not CandidateScope or latest != gate.scope:
                raise ExecutionError("travel_context_changed")
            if projection.state == "no_tool_result":
                raise ExecutionError("travel_tool_required")
            return TravelTurn(projection.state, None, projection.message, projection.as_dict(),
                ROLE_FOR_SLOT[run.result.primary_agent or run.result.agent_type].value,
                run.counts, run.elapsed_ms, versions)
        except ExecutionError as error:
            return TravelTurn("failed", error.code, "本次完整链路未完成，没有执行旅行变更。",
                None, None, run.counts, run.elapsed_ms, versions)

"""Opt-in safety profile around preserved EchoMind algorithms, not a new engine.

No live route imports this module. Trusted composition supplies all clients,
storage, tool functions and current-context checks. The source-preserved core
continues to own fusion, routing, parallel collaboration, retrieval/cache/
circuit-breakers and monitor feedback. This layer gates execution, does not
certify generated facts, and never adopts or saves a travel plan.
"""
from __future__ import annotations

import asyncio
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
import inspect
import math
import re
import time
from typing import Any, Callable, Iterator, Mapping
from uuid import UUID

from core.config import Settings
from core.llm_utils import RuntimeFailure, bounded_json, strict_json
from core.usage_runtime import MeteredAsyncClient, _context as usage_context
from echomind_core.diagnostics import SafeEvent, emit, quarantine_upstream_logging
from echomind_core.provider import (
    MODEL, ProviderError, QwenMessagesClient, TaskBudget, _arguments, _schema, bind_provider,
)
from echomind_core.upstream.agents.agent_orchestrator import (
    AgentOrchestrator, BillingAgent, EscalationAgent, GeneralAgent,
    OrchestratorResult, Request, ResponseComposer, TechnicalAgent,
)
from echomind_core.upstream.agents.tools import AgentToolSpec, build_shared_rag_tools
from echomind_core.upstream.core.intent_recognizer import IntentRecognizer
from echomind_core.upstream.core.llm_utils import extract_text_content
from echomind_core.upstream.evaluation.evaluator import LLMJudge, QualityScores
from echomind_core.upstream.tooling.tool_manager import MCPToolManager, Tool, ToolResult

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}\Z")
_ACTIVE: ContextVar[ExecutionGate | None] = ContextVar("echomind_execution_gate", default=None)


class ExecutionError(RuntimeFailure):
    """A fixed code only; no model fields or underlying exceptions."""


@dataclass(frozen=True)
class CandidateScope:
    """Input version MUST monotonically advance, including A→B→A changes.

    session_id is new for each independently initialized workspace; it is never
    derived only from owner/conversation. The trusted application owns versions.
    """
    owner_id: str
    conversation_id: str
    session_id: str
    input_version: int

    def __post_init__(self) -> None:
        if not isinstance(self.owner_id, str) or re.fullmatch(r"[a-f0-9]{32}", self.owner_id) is None:
            raise ExecutionError("configuration")
        for value in (self.conversation_id, self.session_id):
            try:
                if type(value) is not str or str(UUID(value)) != value:
                    raise ValueError
            except (ValueError, TypeError, AttributeError):
                raise ExecutionError("configuration") from None
        if type(self.input_version) is not int or not 0 <= self.input_version <= 2**53 - 1:
            raise ExecutionError("configuration")


@dataclass
class ExecutionGate:
    scope: CandidateScope
    current: Callable[[], CandidateScope] = field(repr=False)
    budget: TaskBudget = field(repr=False)
    _closed: bool = field(default=False, init=False, repr=False)
    _failure: str | None = field(default=None, init=False, repr=False)
    _pending: set[asyncio.Task[Any]] = field(default_factory=set, init=False, repr=False)

    def check(self, request: Request | None = None) -> None:
        try:
            current = self.current()
            matches = type(current) is CandidateScope and current == self.scope
        except Exception:
            matches = False
        if self._closed or not matches or (request is not None and (
            request.user_id != self.scope.owner_id or request.conv_id != self.scope.conversation_id
        )):
            self.fail("context_changed")
            raise ExecutionError("context_changed")
        if self._failure is not None:
            raise ExecutionError(self._failure)
        if self.budget.stopped:
            raise ExecutionError("task_stopped")
        if not self.budget.remaining_seconds:
            self.fail("task_deadline")
            raise ExecutionError("task_deadline")

    def fail(self, code: str) -> None:
        if self._failure is None:
            self._failure = code
        self.budget.stop()

    def invalidate(self) -> None:
        self._closed = True
        self.fail("context_changed")
        current = asyncio.current_task()
        for task in tuple(self._pending):
            if task is not current and not task.done():
                task.cancel()


@contextmanager
def bind_execution(gate: ExecutionGate) -> Iterator[None]:
    gate.check()
    token = _ACTIVE.set(gate)
    try:
        yield
    finally:
        gate.invalidate()
        _ACTIVE.reset(token)


def execution_gate() -> ExecutionGate:
    gate = _ACTIVE.get()
    if gate is None:
        raise ExecutionError("provider_unbound")
    gate.check()
    return gate


class _GuardedMessages:
    def __init__(self, messages: Any):
        self.messages = messages

    async def create(self, **arguments: Any) -> Any:
        gate = execution_gate()
        try:
            usage = usage_context()
            expected_path = f"/chat/conversations/{gate.scope.conversation_id}/assistant-turns"
            if usage.owner_id != gate.scope.owner_id or usage.request_path != expected_path:
                raise ValueError
        except Exception:
            gate.fail("context_changed")
            raise ExecutionError("context_changed") from None
        task = asyncio.current_task()
        if task is not None:
            gate._pending.add(task)
        try:
            result = await self.messages.create(**arguments)
            gate.check()
            return result
        except RuntimeFailure as error:
            gate.fail(error.code)
            raise
        except asyncio.CancelledError:
            gate.fail("cancelled")
            raise
        except Exception:
            gate.fail("provider_failed")
            raise ExecutionError("provider_failed") from None
        finally:
            if task is not None:
                gate._pending.discard(task)


class _TaskClient:
    """Preserved fallbacks cannot hide a denied/failed model attempt."""
    def __init__(self, client: QwenMessagesClient | None):
        original = client or QwenMessagesClient()
        if not isinstance(original, QwenMessagesClient):
            raise ExecutionError("configuration")
        self.messages = _GuardedMessages(original.messages)


def _task_client(client: Any = None) -> _TaskClient:
    return client if isinstance(client, _TaskClient) else _TaskClient(client)


def _snapshot(value: Any, limit: int, code: str) -> Any:
    try:
        return strict_json(bounded_json(value, limit))
    except (RuntimeFailure, ValueError, TypeError):
        raise ExecutionError(code) from None


async def _call_handler(handler: Callable[..., Any], gate: ExecutionGate, *arguments: Any) -> Any:
    """Drain an already-running sync read before releasing the execution scope."""
    if inspect.iscoroutinefunction(handler):
        return await handler(*arguments)
    def invoke() -> Any:
        gate.check()
        return handler(*arguments)
    worker = asyncio.create_task(asyncio.to_thread(invoke))
    try:
        result = await asyncio.shield(worker)
    except asyncio.CancelledError:
        gate.fail("cancelled")
        # Threads cannot be killed. Hold the scope until the known worker exits;
        # repeated cancellation is not permission to orphan work or refund it.
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled():
            worker.exception()  # observe any private exception, never log it
        raise
    if inspect.isawaitable(result):
        return await result
    return result


def _freeze_request(request: Request, scope: CandidateScope) -> Request:
    """Whitelist-copy before any await; mutable caller state is never executed."""
    from echomind_core.upstream.core.intent_recognizer import IntentCategory, UrgencyLevel
    if (type(request) is not Request or request.user_id != scope.owner_id
            or request.conv_id != scope.conversation_id or request.before_unsafe_tool is not None
            or request.tool_attempts or request.unsafe_tool_calls
            or not isinstance(request.message, str) or not 1 <= len(request.message) <= 4000
            or not isinstance(request.context, str) or len(request.context) > 16_000
            or request.intent is not None and type(request.intent) is not IntentCategory
            or request.urgency is not None and type(request.urgency) is not UrgencyLevel
            or type(request.intent_confidence) not in (int, float)
            or not math.isfinite(request.intent_confidence) or not 0 <= request.intent_confidence <= 1
            or request.intent_group is not None and request.intent_group not in {item.value for item in IntentCategory}):
        raise ExecutionError("configuration")
    try:
        if str(UUID(request.request_id)) != request.request_id:
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        raise ExecutionError("configuration") from None
    history = _snapshot(request.history or [], 24_000, "configuration")
    if type(history) is not list or len(history) > 12 or any(
        type(item) is not dict or set(item) != {"role", "content"}
        or item["role"] not in {"user", "assistant"} or type(item["content"]) is not str
        or len(item["content"]) > 4000 for item in history
    ):
        raise ExecutionError("configuration")
    entities = _snapshot(request.entities, 8192, "configuration")
    if type(entities) is not dict or len(entities) > 32 or any(
        len(key) > 64 or type(values) is not list or len(values) > 16
        or any(type(value) is not str or len(value) > 512 for value in values)
        for key, values in entities.items()
    ):
        raise ExecutionError("configuration")
    state = _snapshot(request.conversation_state, 16_000, "configuration")
    if state is not None and type(state) is not dict:
        raise ExecutionError("configuration")
    frozen = replace(request, history=history, entities=entities, conversation_state=state,
                     tool_attempts=[], unsafe_tool_calls=set())
    _snapshot({"message": frozen.message, "context": frozen.context, "history": history,
               "entities": entities, "state": state}, 48_000, "configuration")
    return frozen


@dataclass(frozen=True)
class ToolDeclaration:
    spec: AgentToolSpec
    read_only: bool
    version: str = "1"


class GuardedToolRegistry:
    """Frozen trusted declarations; the model can only choose registered names."""

    def __init__(self, declarations: tuple[ToolDeclaration, ...]):
        if type(declarations) is not tuple or len(declarations) > 32:
            raise ExecutionError("tool_registry")
        self._declared: dict[str, tuple[AgentToolSpec, Any]] = {}
        for entry in declarations:
            if type(entry) is not ToolDeclaration:
                raise ExecutionError("tool_registry")
            spec = entry.spec
            if (type(entry) is not ToolDeclaration or entry.read_only is not True
                    or not isinstance(spec, AgentToolSpec) or not callable(spec.handler)
                    or not isinstance(spec.name, str) or _NAME.fullmatch(spec.name) is None
                    or spec.name in self._declared or spec.retry_safe is not True
                    or not isinstance(entry.version, str) or re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", entry.version) is None):
                raise ExecutionError("tool_registry")
            schema = _snapshot(spec.input_schema, 8192, "tool_registry")
            try:
                validator = _schema(schema)
            except ProviderError:
                raise ExecutionError("tool_registry") from None
            self._declared[spec.name] = (replace(spec, input_schema=schema), validator)

    def wrap(self, candidate: AgentToolSpec) -> AgentToolSpec:
        registered = self._declared.get(candidate.name)
        if (registered is None or candidate.handler is not registered[0].handler
                or candidate.input_schema != registered[0].input_schema
                or candidate.retry_safe is not True):
            raise ExecutionError("tool_registry")
        spec, validator = registered

        async def guarded(request: Request, arguments: dict[str, Any]) -> Any:
            gate = execution_gate()
            gate.check(request)
            values = _snapshot(arguments, 8192, "tool_arguments")
            try:
                _arguments(values, validator)
            except ProviderError:
                raise ExecutionError("tool_arguments") from None
            try:
                await gate.budget.reserve_tool()
            except ProviderError as error:
                gate.fail(error.code)
                raise ExecutionError(error.code) from None
            # Recheck after any reservation await, before executing trusted code.
            gate.check(request)
            task = asyncio.current_task()
            if task is not None:
                gate._pending.add(task)
            started = time.monotonic()
            try:
                async with asyncio.timeout(gate.budget.remaining_seconds):
                    result = await _call_handler(spec.handler, gate, request, values)
                gate.check(request)
                result = _snapshot(result, 32_000, "tool_result")
                emit(SafeEvent("tool", "complete", count=gate.budget.counts[1],
                               elapsed_ms=(time.monotonic() - started) * 1000))
                return result
            except asyncio.CancelledError:
                gate.fail("cancelled")
                raise
            except TimeoutError:
                gate.fail("task_deadline")
                raise ExecutionError("task_deadline") from None
            except ExecutionError:
                raise
            except Exception:
                # Original tool/Agent code may stringify exceptions. Do not let
                # sensitive handler exceptions enter that preserved trace.
                raise ExecutionError("tool_failed") from None
            finally:
                if task is not None:
                    gate._pending.discard(task)

        return replace(spec, input_schema=_snapshot(spec.input_schema, 8192, "tool_registry"), handler=guarded)


class SafeIntentRecognizer(IntentRecognizer):
    """Original fusion/learning with injected embedding; no provider discovery."""

    def __init__(self, embedding: Any, client: QwenMessagesClient | None = None,
                 confidence_threshold: float = 0.5):
        quarantine_upstream_logging()
        if not 0 <= confidence_threshold <= 1 or not callable(getattr(embedding, "vectors", None)):
            raise ExecutionError("configuration")
        self.client, self.model, self.threshold = _task_client(client), MODEL, confidence_threshold
        self._embedding, self._embedding_enabled = embedding, True
        self._cache, self._cache_expiries = {}, {}
        self.cache_hits = self.cache_misses = 0
        self._identity: tuple[str, str, str] | None = None
        self._latest_version = -1

    async def recognize(self, message: str, history: list[dict[str, str]] | None = None):
        gate = execution_gate()
        identity = (gate.scope.owner_id, gate.scope.conversation_id, gate.scope.session_id)
        if (self._identity is not None and identity != self._identity
                or gate.scope.input_version < self._latest_version):
            raise ExecutionError("context_changed")
        if gate.scope.input_version != self._latest_version:
            self._cache.clear()
            self._cache_expiries.clear()
        self._identity, self._latest_version = identity, gate.scope.input_version
        if not isinstance(message, str) or not 1 <= len(message) <= 4000:
            raise ExecutionError("configuration")
        _snapshot(history or [], 24_000, "configuration")
        result = await super().recognize(message, history=history)
        # Upstream may produce a lexical fallback after a failed model. Do not
        # present this as a successful candidate task after a provider stop.
        gate.check()
        return result

    def learn(self, message: str, correct: Any) -> None:
        # Upstream feedback appends to a module-global template dictionary.
        # Preserve that algorithm in upstream, but do not share one workspace's
        # private correction with others. Explicit owner-reviewed template
        # publication/versioning is a later adapter, never an automatic write.
        raise ExecutionError("configuration")


class SafeToolManager(MCPToolManager):
    """Original retrieval chain, finite local read-only handlers only in stage2."""

    def __init__(self, client: QwenMessagesClient | None = None):
        quarantine_upstream_logging()
        self._client, self._model = _task_client(client), MODEL
        self._tools, self._cache_versions, self._cache = {}, {}, {}

    def register(self, tool: Tool) -> None:
        raise ExecutionError("tool_registry")

    def unregister(self, name: str) -> None:
        if _ACTIVE.get() is not None:
            raise ExecutionError("tool_registry")
        super().unregister(name)

    def register_read_only(self, tool: Tool) -> None:
        if (_ACTIVE.get() is not None or not isinstance(tool, Tool)
                or tool.name in self._tools or tool.fallback is not None or not callable(tool.handler)
                or not isinstance(tool.name, str) or _NAME.fullmatch(tool.name) is None
                or type(tool.timeout_s) not in (int, float) or not math.isfinite(tool.timeout_s) or tool.timeout_s <= 0
                or type(tool.cache_ttl) not in (int, float) or not math.isfinite(tool.cache_ttl)
                or not 0 <= tool.cache_ttl <= 3600 or len(self._tools) >= 32):
            raise ExecutionError("tool_registry")
        handler = tool.handler
        validator = _schema(_snapshot(tool.schema, 8192, "tool_registry"))

        async def guarded(params: dict[str, Any], context: dict[str, Any] | None) -> Any:
            gate = execution_gate()
            values = _snapshot(params, 8192, "tool_arguments")
            try:
                _arguments(values, validator)
                await gate.budget.reserve_tool()
            except ProviderError as error:
                gate.fail(error.code)
                raise ExecutionError(error.code) from None
            gate.check()
            trusted = {"owner_id": gate.scope.owner_id, "conversation_id": gate.scope.conversation_id,
                       "session_id": gate.scope.session_id, "input_version": gate.scope.input_version}
            if context != trusted:
                gate.fail("context_changed")
                raise ExecutionError("context_changed")
            try:
                result = await _call_handler(handler, gate, values, trusted)
                gate.check()
                return _snapshot(result, 32_000, "tool_result")
            except asyncio.CancelledError:
                gate.fail("cancelled")
                raise
            except ExecutionError:
                raise
            except Exception:
                raise ExecutionError("tool_failed") from None

        # Explicit local callback; remote stdio/http MCP registration is a later
        # trusted transport task, not enabled by accepting a URL from the model.
        wrapped = replace(tool, schema=_snapshot(tool.schema, 8192, "tool_registry"),
                          handler=guarded, timeout_s=min(tool.timeout_s, 20))
        wrapped.stats, wrapped.breaker = tool.stats, tool.breaker
        super().register(wrapped)

    async def call(self, name: str, params: dict[str, Any], context: dict[str, Any] | None = None,
                   *, use_cache: bool = True, rerank_top_k: int = 0) -> ToolResult:
        gate = execution_gate()
        trusted = {"owner_id": gate.scope.owner_id, "conversation_id": gate.scope.conversation_id,
                   "session_id": gate.scope.session_id, "input_version": gate.scope.input_version}
        if context is not None and context != trusted:
            gate.fail("context_changed")
            raise ExecutionError("context_changed")
        result = await super().call(name, params, trusted, use_cache=use_cache, rerank_top_k=rerank_top_k)
        gate.check()
        return result

    async def rewrite_query(self, query: str, n: int = 3) -> list[str]:
        gate = execution_gate()
        result = await super().rewrite_query(query, n)
        gate.check()
        return result

    async def _rerank_with_status(self, query: str, items: list[Any], top_k: int) -> tuple[list[Any], bool]:
        gate = execution_gate()
        result = await super()._rerank_with_status(query, items, top_k)
        gate.check()
        return result

    async def search_with_rewrite(self, tool_name: str, query: str, top_k: int = 5,
                                  context: dict[str, Any] | None = None) -> ToolResult:
        gate = execution_gate()
        result = await super().search_with_rewrite(tool_name, query, top_k, context)
        gate.check()
        return result


class _GuardedAgentTools:
    _tool_registry: GuardedToolRegistry

    def get_tools(self) -> dict[str, AgentToolSpec]:
        return {name: self._tool_registry.wrap(spec) for name, spec in super().get_tools().items()}


class _General(_GuardedAgentTools, GeneralAgent):
    pass


class _Technical(_GuardedAgentTools, TechnicalAgent):
    pass


class _Billing(_GuardedAgentTools, BillingAgent):
    pass


class _Escalation(_GuardedAgentTools, EscalationAgent):
    pass


class SafeOrchestrator(AgentOrchestrator):
    """Build original pool without raw env/model/embedding/client constructors."""

    def __init__(self, embedding: Any, tools: Mapping[str, AgentToolSpec] | None = None,
                 skill_manager: Any = None, rag_tool_manager: SafeToolManager | None = None,
                 client: QwenMessagesClient | None = None):
        quarantine_upstream_logging()
        client = _task_client(client)
        self._intent_recognizer = SafeIntentRecognizer(embedding, client)
        self._skill_manager = skill_manager
        self._composer = ResponseComposer(client, MODEL, skill_manager)
        self._shared_tools = dict(tools or {})
        if len(self._shared_tools) > 13 or any(
            type(key) is not str or not isinstance(spec, AgentToolSpec) or key != spec.name
            for key, spec in self._shared_tools.items()
        ):
            raise ExecutionError("tool_registry")
        if rag_tool_manager is not None:
            for name, spec in build_shared_rag_tools(rag_tool_manager).items():
                if name in self._shared_tools:
                    raise ExecutionError("tool_registry")
                self._shared_tools[name] = spec
        self._recent_tool_traces = deque(maxlen=200)
        self._pool = {}
        for cls in (_General, _Technical, _Billing, _Escalation):
            # BaseAgent reads no env or network; skip _make_agent's environment
            # overrides so every original branch stays on the fixed snapshot.
            agent = cls(client, MODEL, skill_manager, profile=replace(cls.profile, model=MODEL))
            agent.set_shared_tools(self._shared_tools)
            raw = super(_GuardedAgentTools, agent).get_tools()
            agent._tool_registry = GuardedToolRegistry(tuple(ToolDeclaration(spec, True) for spec in raw.values()))
            self._pool[cls.agent_type] = [agent]

    def set_shared_tools(self, tools: Mapping[str, AgentToolSpec] | None) -> None:
        # Registration is an explicit immutable construction phase. Dynamic
        # Skill text can change; tool authority cannot mutate mid-task.
        raise ExecutionError("tool_registry")


@dataclass(frozen=True)
class CandidateRun:
    state: str
    code: str | None
    result: OrchestratorResult | None
    counts: tuple[int, int, int]
    elapsed_ms: float


class CandidateRunner:
    def __init__(self, orchestrator: SafeOrchestrator):
        if not isinstance(orchestrator, SafeOrchestrator):
            raise ExecutionError("configuration")
        self.orchestrator = orchestrator
        self._running = False
        self._identity: tuple[str, str, str] | None = None
        self._latest_version = -1

    async def run(self, request: Request, gate: ExecutionGate, settings: Settings,
                  client: MeteredAsyncClient) -> CandidateRun:
        if self._running:
            raise ExecutionError("configuration")
        request = _freeze_request(request, gate.scope)
        identity = (gate.scope.owner_id, gate.scope.conversation_id, gate.scope.session_id)
        if (self._identity is not None and identity != self._identity
                or gate.scope.input_version < self._latest_version):
            raise ExecutionError("context_changed")
        self._identity, self._latest_version = identity, gate.scope.input_version
        self._running = True
        started = time.monotonic()
        state, code, result = "failed", None, None
        try:
            gate.check(request)
            with bind_execution(gate), bind_provider(settings, client, gate.budget):
                async with asyncio.timeout(gate.budget.remaining_seconds):
                    result = await self.orchestrator.run(request)
                gate.check(request)
                state = "complete" if result.status == "success" else result.status
                code = result.error_code
        except asyncio.CancelledError:
            gate.fail("cancelled")
            raise
        except TimeoutError:
            gate.fail("task_deadline")
            state, code, result = "failed", "task_deadline", None
        except RuntimeFailure as error:
            state, code, result = "failed", error.code, None
        except Exception:
            state, code, result = "failed", "execution_failed", None
        finally:
            self._running = False
        elapsed = (time.monotonic() - started) * 1000
        emit(SafeEvent("task", code or state, count=gate.budget.counts[0], elapsed_ms=elapsed, model=MODEL))
        return CandidateRun(state, code, result, gate.budget.counts, elapsed)


class SafeJudge:
    """One explicit Judge attempt; original parser/calibration, never repair."""

    def __init__(self, client: QwenMessagesClient | None = None):
        quarantine_upstream_logging()
        self.client = _task_client(client)

    async def judge(self, question: str, response: str, context: str | None = None) -> QualityScores:
        gate = execution_gate()
        _snapshot([question, response, context], 16_000, "configuration")
        prompt = LLMJudge.JUDGE_PROMPT.format(
            question=question, response=response,
            context_section=f"背景信息: {context}" if context else "",
            few_shot_examples=LLMJudge.few_shot_prompt(),
        )
        stop = None
        try:
            answer = await self.client.messages.create(model=MODEL, max_tokens=1024, temperature=0.0,
                                                       messages=[{"role": "user", "content": prompt}])
            gate.check()
            stop = answer.stop_reason
            values = LLMJudge._parse_scores(extract_text_content(answer.content, separator=""))
            emit(SafeEvent("judge", "complete", count=1, model=MODEL))
            return QualityScores(**values, attempts=1, stop_reason=stop)
        except asyncio.CancelledError:
            gate.fail("cancelled")
            raise
        except Exception:
            emit(SafeEvent("judge", "unscored", count=1, model=MODEL))
            return QualityScores(None, None, None, None, judge_failed=True,
                                 error="judge_failed", attempts=1, stop_reason=stop,
                                 error_type="judge_output")

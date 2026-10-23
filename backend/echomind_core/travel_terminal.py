"""Request-local travel handoff around the source-preserved feedback loop.

This is a program stop, NOT a synthesized model reply or relaxed tool parser.
Read tools still use the original native feedback loop. A checked clarification
or proposal hands control to the user/browser without paying for a model to say
"stop". Upstream's broad Exception handlers must not swallow that handoff.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import time
from typing import Any

from echomind_core.execution import ExecutionError, execution_gate
from core.schemas.dialogue import GROUPS
from echomind_core.upstream.agents.agent_orchestrator import AgentCallResult, BaseAgent, Request
from echomind_core.upstream.agents.tools import AgentToolSpec


_TERMINAL = {"travel_clarify": "clarification", "travel_information": "guidance",
             "update_trip_requirements": "requirements_proposal",
             **{name: "proposal" for name in GROUPS}}


class _ProgramHandoff(BaseException):
    """Private control flow; caught only by its owning travel invocation.

    Deliberately not Exception: the preserved loop treats all Exceptions as tool
    errors. CancelledError/SystemExit/KeyboardInterrupt are never caught here.
    """
    def __init__(self, owner: TravelFeedbackBoundary):
        self.owner = owner


class TravelFeedbackBoundary:
    """One invocation, actual native IDs, and fully guarded completed results."""
    def __init__(self, request: Request, agent_type: str, client: Any):
        self.request, self.agent_type = request, agent_type
        self.gate = execution_gate()
        self._messages = client.messages
        self.messages = self  # Per-agent client facade; never mutate shared client.
        self.tools_used: list[str] = []
        self.traces: list[dict[str, Any]] = []
        self._pending: tuple[str, str, dict[str, Any]] | None = None
        self._terminal = False

    def _reject(self, code: str) -> None:
        self.gate.fail(code)
        raise ExecutionError(code)

    async def create(self, **arguments: Any) -> Any:
        self.gate.check(self.request)
        if self._terminal or self._pending is not None:
            self._reject("travel_tool_failed")
        # Existing guarded provider owns request/payload/usage/schema validation.
        response = await self._messages.create(**arguments)
        self.gate.check(self.request)
        calls = [block for block in response.content if BaseAgent._block_type(block) == "tool_use"]
        # Travel parallel execution is gated. Validate the whole batch BEFORE
        # dispatch: a valid first clarification cannot hide a second operation.
        if len(calls) > 1:
            self._reject("travel_multiple_tools")
        if calls:
            call = calls[0]
            self._pending = (BaseAgent._block_value(call, "id"),
                             BaseAgent._block_value(call, "name"),
                             deepcopy(BaseAgent._block_value(call, "input")))
        return response  # Never replace native stop_reason/content.

    def wrap(self, spec: AgentToolSpec) -> AgentToolSpec:
        async def completed(request: Request, arguments: dict[str, Any]) -> Any:
            self.gate.check(request)
            if (request is not self.request or self._pending is None
                    or self._pending[1:] != (spec.name, arguments)):
                self._reject("travel_context_changed")
            call_id = self._pending[0]
            started = time.monotonic()
            # spec is already wrapped by GuardedToolRegistry. Do not interrupt
            # its post-handler scope check, result snapshot or completion event.
            result = await spec.handler(request, arguments)
            self.gate.check(request)
            self.tools_used.append(spec.name)
            result_success = bool(result["success"]) if isinstance(result, dict) and "success" in result else None
            self.traces.append({"agent_type": self.agent_type, "tool_name": spec.name,
                "tool_use_id": call_id, "input": deepcopy(arguments), "success": True,
                "result_success": result_success, "latency_ms": round((time.monotonic() - started) * 1000, 1),
                "cached": bool(result.get("cached")) if isinstance(result, dict) else False,
                "reranked": bool(result.get("reranked")) if isinstance(result, dict) else False,
                "error": "", "source": result.get("source", "local") if isinstance(result, dict) else "local",
                "mcp_server": None, "mcp_tool": None, "retry_safe": spec.retry_safe})
            self._pending = None
            expected = _TERMINAL.get(spec.name)
            if expected is not None:
                if (type(result) is not dict or result.get("kind") != expected
                        or result.get("executed") is not False or result_success is False):
                    self._reject("travel_tool_failed")
                self._terminal = True
                raise _ProgramHandoff(self)
            return result

        return replace(spec, handler=completed)

    def finish(self, signal: _ProgramHandoff) -> AgentCallResult:
        self.gate.check(self.request)
        if signal.owner is not self or not self._terminal or self._pending is not None:
            self._reject("travel_context_changed")
        # The bridge's own authority-checked projection renders the actual
        # question/proposal. This internal marker is not a model final answer.
        return AgentCallResult(content="程序已取得旅行工具结果，本轮交还用户或浏览器。",
                               tools_used=list(self.tools_used), tool_traces=deepcopy(self.traces))

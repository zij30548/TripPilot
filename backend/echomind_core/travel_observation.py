"""Explicit process-local lifecycle for EchoMind's original monitor feedback.

Only four AgentStats objects and sanitized technical metadata outlive a turn.
No request, client, gate, tool handler, result, owner or prompt is retained.  The
original PerformanceMonitor performs threshold/penalty calculation and writes
it back to the same statistics attached to the next fresh travel orchestrator.
This is neither persistent recovery nor a semantic-quality scoring service.
"""
from __future__ import annotations

import asyncio
from collections import deque
from copy import deepcopy
import math
import os
import re
from types import SimpleNamespace
from typing import Any

from prometheus_client import CollectorRegistry

from echomind_core.diagnostics import quarantine_upstream_logging
from echomind_core.execution import ExecutionError
from echomind_core.provider import MODEL
from echomind_core.travel_routing import ROLE_FOR_SLOT
from echomind_core.upstream.agents.agent_orchestrator import (
    AgentOrchestrator, AgentStats, AgentType, BaseAgent,
)
from echomind_core.upstream.monitor.performance_monitor import PerformanceMonitor
from echomind_core.upstream.tooling.tool_manager import MCPToolManager

VERSION = "travel-original-monitor-feedback-v1"
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}\Z")
_TOOL_FIELDS = {"total", "success_rate", "avg_latency_ms", "consecutive_fails", "circuit_state"}


class _ObservedRole:
    """A stats-only view satisfying original get_stats, never an executable Agent."""
    def __init__(self, slot: AgentType, stats: AgentStats):
        self.stats = stats
        self.profile = SimpleNamespace(role=ROLE_FOR_SLOT[slot].value, workflow=())
        self._model = MODEL
        self.tool_names: tuple[str, ...] = ()

    def get_tools(self) -> dict[str, None]:
        return dict.fromkeys(self.tool_names)


class _ObservedPool:
    def __init__(self):
        self._pool = {slot: [_ObservedRole(slot, AgentStats())] for slot in AgentType}

    # Reuse the original summary and penalty update, not another monitor model.
    get_stats = AgentOrchestrator.get_stats
    update_routing_penalties = AgentOrchestrator.update_routing_penalties


class _ObservedTools:
    def __init__(self):
        self.rows: dict[str, dict[str, Any]] = {}

    def get_stats(self) -> dict[str, dict[str, Any]]:
        return deepcopy(self.rows)


def _tool_snapshot(manager: MCPToolManager | None) -> dict[str, dict[str, Any]] | None:
    if manager is None:
        return None
    if not isinstance(manager, MCPToolManager):
        raise ExecutionError("travel_observation_invalid")
    try:
        rows = manager.get_stats()
        if type(rows) is not dict or len(rows) > 32:
            raise ValueError
        for name, value in rows.items():
            if (type(name) is not str or _NAME.fullmatch(name) is None or type(value) is not dict
                    or set(value) != _TOOL_FIELDS
                    or type(value["total"]) is not int or not 0 <= value["total"] <= 10**12
                    or type(value["consecutive_fails"]) is not int
                    or not 0 <= value["consecutive_fails"] <= value["total"]
                    or type(value["success_rate"]) not in (int, float)
                    or not math.isfinite(value["success_rate"]) or not 0 <= value["success_rate"] <= 1
                    or type(value["avg_latency_ms"]) not in (int, float)
                    or not math.isfinite(value["avg_latency_ms"]) or not 0 <= value["avg_latency_ms"] <= 3_600_000
                    or value["circuit_state"] not in {"closed", "open", "half_open"}):
                raise ValueError
        return deepcopy(rows)
    except Exception:
        raise ExecutionError("travel_observation_invalid") from None


class TravelObservationState:
    """One application-process observer; created/closed by trusted lifecycle code.

    Fresh Agents share technical scores, not clients, conversation or authority.
    Tool metrics are the latest validated snapshot, not invented cumulative
    successes reconstructed from rounded rates.  No exporters/webhooks start.
    """
    def __init__(self, *, interval_seconds: float = 10.0):
        if (type(interval_seconds) not in (int, float) or not math.isfinite(interval_seconds)
                or not .05 <= interval_seconds <= 60):
            raise ExecutionError("travel_observation_invalid")
        quarantine_upstream_logging()
        self._pid = os.getpid()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False
        self._attached = 0
        self._pool, self._tools = _ObservedPool(), _ObservedTools()
        self._registry = CollectorRegistry()
        self._monitor = PerformanceMonitor(self._pool, self._tools, interval_s=interval_seconds,
            registry=self._registry, webhook_url=None, prometheus_port=None)
        # Keep the original append/filter logic, with finite resource storage.
        self._monitor._alerts = deque(maxlen=100)
        self._monitor._suggestions = deque(maxlen=40)

    def _check(self, *, asynchronous: bool = False) -> None:
        if self._closed or os.getpid() != self._pid:
            raise ExecutionError("travel_observation_closed")
        if asynchronous:
            current = asyncio.get_running_loop()
            if self._loop is not None and self._loop is not current:
                raise ExecutionError("travel_observation_invalid")
            self._loop = current

    def attach(self, orchestrator: AgentOrchestrator, tool_manager: MCPToolManager | None = None) -> None:
        """Prevalidate the whole pool, then attach; retain no task-owned object."""
        self._check()
        if not isinstance(orchestrator, AgentOrchestrator):
            raise ExecutionError("travel_observation_invalid")
        pending: list[tuple[BaseAgent, _ObservedRole, tuple[str, ...]]] = []
        try:
            pool = orchestrator._pool
            if type(pool) is not dict or set(pool) != set(AgentType):
                raise ValueError
            seen = set()
            for slot, agents in pool.items():
                if type(agents) is not list or len(agents) != 1:
                    raise ValueError
                agent, shared = agents[0], self._pool._pool[slot][0]
                if (not isinstance(agent, BaseAgent) or id(agent) in seen or agent.agent_type is not slot
                        or agent.profile.role != ROLE_FOR_SLOT[slot].value or agent._model != MODEL
                        or type(agent.stats) is not AgentStats):
                    raise ValueError
                seen.add(id(agent))
                # Refuse to discard work already recorded by an unattached
                # Agent. Attaching the same shared object again is harmless.
                stats = agent.stats
                if stats is not shared.stats and (stats.total or stats.success or stats.total_ms
                        or stats.samples or stats.monitor_penalty or stats.retry_at or stats.probe_running):
                    raise ValueError
                tools = agent.get_tools()
                if (type(tools) is not dict or len(tools) > 32
                        or any(type(name) is not str or _NAME.fullmatch(name) is None for name in tools)):
                    raise ValueError
                pending.append((agent, shared, tuple(sorted(tools))))
            snapshot = _tool_snapshot(tool_manager)
        except Exception:
            raise ExecutionError("travel_observation_invalid") from None
        for agent, shared, names in pending:
            agent.stats = shared.stats
            shared.tool_names = names
        if snapshot is not None:
            self._tools.rows = snapshot
        self._attached += 1

    async def collect_once(self, tool_manager: MCPToolManager | None = None) -> None:
        self._check(asynchronous=True)
        snapshot = _tool_snapshot(tool_manager)
        if snapshot is not None:
            self._tools.rows = snapshot
        await self._monitor._collect()

    async def start(self) -> None:
        self._check(asynchronous=True)
        await self._monitor.start()

    async def stop(self) -> None:
        self._check(asynchronous=True)
        await self._monitor.stop()

    def summary(self) -> dict[str, Any]:
        # Read-only operator technical view. No public route is added here;
        # global aggregates must not be labelled one workspace's private score.
        source = self._monitor.summary()
        agents = {ROLE_FOR_SLOT[slot].value: deepcopy(source["agent_stats"][f"{slot.value}_0"])
                  for slot in AgentType}
        alerts = []
        for alert in source["active_alerts"]:
            value = deepcopy(alert)
            value["severity"] = value["severity"].value
            alerts.append(value)
        return {"schema_version": VERSION, "scope": "process_local_technical_execution",
                "persistence": "none", "semantic_quality": "not_assessed",
                "closed": self._closed, "running": self._monitor._active,
                "attachment_count": self._attached, "agent_stats": agents,
                "tool_stats_scope": "latest_sanitized_snapshot", "tool_stats": deepcopy(source["tool_stats"]),
                "active_alerts": alerts, "suggestions": deepcopy(source["suggestions"])}

    async def close(self) -> None:
        if self._closed:
            return
        self._check(asynchronous=True)
        await self._monitor.stop()
        for agents in self._pool._pool.values():
            agents[0].stats.observer = None
        self._closed = True

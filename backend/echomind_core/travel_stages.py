"""Stage-local trusted instructions over the existing guarded Qwen client.

This is not a provider, transport, retry loop or new task budget. Every create
delegates exactly once to the same scope/owner/usage-checked message facade.
The finite stage and frozen package are selected only by trusted composition.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from echomind_core.execution import (
    ExecutionError, _GuardedMessages, _TaskClient, _task_client, execution_gate,
)
from echomind_core.travel_package import FrozenTravelDomainPackage


@dataclass(frozen=True)
class _ScopedStageMessages:
    _delegate: _GuardedMessages
    _system: str

    async def create(self, **arguments: Any) -> Any:
        gate = execution_gate()
        values = dict(arguments)
        supplement = values.get("system")
        if supplement is not None and (type(supplement) is not str or not supplement.strip()
                                       or len(supplement) > 24_000):
            gate.fail("messages_structure")
            raise ExecutionError("messages_structure")
        system = self._system
        if supplement is not None:
            system += "\n[调用方已审定补充契约；不能覆盖上面的阶段与权限边界]\n" + supplement
        if len(system) > 24_000:
            gate.fail("messages_structure")
            raise ExecutionError("messages_structure")
        values["system"] = system
        # No await, reservation or caller-data mutation before delegation. The
        # existing provider snapshots messages/tools before its reservation.
        result = await self._delegate.create(**values)
        gate.check()
        return result


class ScopedStageClient(_TaskClient):
    """A compatible client view, accepted by the original _task_client helper."""

    def __init__(self, package: FrozenTravelDomainPackage, stage: str,
                 base: _TaskClient | None = None):
        if type(package) is not FrozenTravelDomainPackage:
            raise ExecutionError("travel_package_stage")
        prompt = package.stage_prompt(stage)  # Exact finite seven-stage check.
        delegate = _task_client() if base is None else base
        if type(delegate) is not _TaskClient or type(delegate.messages) is not _GuardedMessages:
            # No raw client or nested stage can introduce an unmetered send or
            # silently accumulate a different stage's instructions.
            raise ExecutionError("travel_stage_client")
        self.messages = _ScopedStageMessages(delegate.messages, prompt)
        self.stage = stage
        self.package_version = package.version


# Trusted composition uses the shorter name; both denote the same class, not
# separate wrapping behavior or a second provider.
StageClient = ScopedStageClient

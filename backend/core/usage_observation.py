"""Request-local dispatch observations, not a replacement for durable quotas.

No URLs, credentials, bodies, owner IDs or model prose are retained. Crossing
the transport boundary is an attempted send, not proof of upstream receipt or
charging. A process crash still requires durable readback, never blind retry.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Awaitable, Callable
from typing import Any, Iterator


class DispatchObservation:
    def __init__(self, *, before_dispatch: Callable[[], Awaitable[Any]] | None = None) -> None:
        if before_dispatch is not None and not callable(before_dispatch):
            from core.business_usage import UsageError
            raise UsageError()
        self._states: list[str] = []
        self._used = False
        # Trusted request-local authority callback, never request JSON, a URL
        # or a serialized diagnostic. Other/legacy contexts have no new fence.
        self._before_dispatch = before_dispatch

    async def before_dispatch(self, service: str) -> None:
        if service == "qwen_text" and self._before_dispatch is not None:
            await self._before_dispatch()

    def start(self, service: str) -> int | None:
        if service != "qwen_text":
            return None
        if len(self._states) >= 32:
            # This is stricter than an unbounded observer, independent from the
            # task cap of16. Fail before transport if a new path violates it.
            from core.business_usage import UsageError
            raise UsageError()
        self._states.append("uncertain")
        return len(self._states) - 1

    def finish(self, index: int | None, state: str) -> None:
        if index is not None and 0 <= index < len(self._states):
            self._states[index] = state if state in {"received", "failed", "cancelled"} else "uncertain"

    def summary(self) -> dict:
        return {"status": "observed", "sent": len(self._states),
                **{state: self._states.count(state) for state in ("received", "failed", "cancelled", "uncertain")}}


_OBSERVER: ContextVar[DispatchObservation | None] = ContextVar("business_dispatch_observation", default=None)


@contextmanager
def observe_dispatches(observation: DispatchObservation) -> Iterator[None]:
    if type(observation) is not DispatchObservation or observation._used or _OBSERVER.get() is not None:
        from core.business_usage import UsageError
        raise UsageError()
    observation._used = True
    token = _OBSERVER.set(observation)
    try:
        yield
    finally:
        _OBSERVER.reset(token)


def current_observation() -> DispatchObservation | None:
    return _OBSERVER.get()

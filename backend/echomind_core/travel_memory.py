"""Explicit travel memory port; MySQL remains the authoritative source.

Original MemoryManager context/compression/profile methods are reused. Summary
maintenance runs only when a trusted caller asks, stages original Redis writes
in memory, then CAS-publishes a bounded cache without deleting source messages.
Original profile upserts are intercepted as inference proposals, never consent
or database writes. No resources, remote calls or maintenance start on import.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, replace
import inspect
from typing import Any, Awaitable, Callable

from core.llm_utils import RuntimeFailure, bounded_json, strict_json
from echomind_core.execution import ExecutionError, ExecutionGate
from echomind_core.provider import MODEL
from echomind_core.runtime import CandidateResources
from echomind_core.storage import StorageError, _digest, _encode
from echomind_core.travel_bridge import _checked_preferences
from echomind_core.travel_package import FrozenTravelDomainPackage
from echomind_core.travel_stages import StageClient
from echomind_core.upstream.agents.agent_orchestrator import Request
from echomind_core.upstream.memory.conversation_memory import MemoryContext, MemoryManager

VERSION = "travel-memory-port-v1"
MAX_CACHE_BYTES = 65_536


class _Value:
    def __init__(self, value):
        self.value = value

    def __await__(self):
        async def result():
            return deepcopy(self.value)
        return result().__await__()


class _StagedRedis:
    """Private command facade for ORIGINAL _compress's WATCH/MULTI sequence."""
    def __init__(self, messages, summary):
        self.values = {"working": list(reversed(messages)), "summary": summary}

    def pipeline(self, transaction=True):
        if transaction is not True:
            raise ExecutionError("travel_memory_commands")
        return _StagedPipeline(self)

    async def lrange(self, key, start, end):
        return self.command("lrange", key, start, end)

    def command(self, name, *args):
        if name == "get" and args == ("summary",):
            return self.values["summary"]
        if name == "lrange" and args[0] == "working":
            return self.values["working"][args[1]:None if args[2] == -1 else args[2] + 1]
        if name == "setex" and args[0] == "summary":
            self.values["summary"] = args[2]
            return True
        if name == "ltrim" and args[0] == "working":
            self.values["working"] = self.command("lrange", *args)
            return True
        if name == "expire" and args[0] == "working":
            return True
        raise ExecutionError("travel_memory_commands")


class _StagedPipeline:
    def __init__(self, redis):
        self.redis, self.queue, self.watching, self.in_multi = redis, [], False, False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *unused):
        return None

    async def watch(self, *keys):
        if keys != ("working", "summary"):
            raise ExecutionError("travel_memory_commands")
        self.watching = True

    def multi(self):
        self.in_multi = True

    def _command(self, name, *args):
        if self.watching and not self.in_multi:
            return _Value(self.redis.command(name, *args))
        self.queue.append((name, args))
        return self

    def lrange(self, *args):
        return self._command("lrange", *args)

    def get(self, *args):
        return self._command("get", *args)

    def setex(self, *args):
        return self._command("setex", *args)

    def ltrim(self, *args):
        return self._command("ltrim", *args)

    def expire(self, *args):
        return self._command("expire", *args)

    async def execute(self):
        values = [self.redis.command(name, *args) for name, args in self.queue]
        self.queue = []
        return values


class _ProfileProposals:
    def __init__(self):
        self.values = []

    def upsert(self, *, ids, documents, metadatas):
        # Original _update_profile calls this in a worker; it writes ONLY this
        # local staging object. No Chroma client or persistent collection exists.
        if len(ids) != 1 or len(documents) != 1 or len(metadatas) != 1:
            raise ExecutionError("travel_memory_profile")
        value = strict_json(documents[0])
        if (type(value) is not dict or set(value) != {"preferences", "entities"}
                or type(value["preferences"]) is not list or len(value["preferences"]) > 10
                or any(type(item) is not str or not item.strip() or len(item) > 200 for item in value["preferences"])
                or type(value["entities"]) is not dict or len(value["entities"]) > 8
                or any(type(key) is not str or not key.strip() or len(key) > 64
                    or type(items) is not list or len(items) > 8
                    or any(type(item) is not str or not item.strip() or len(item) > 120 for item in items)
                    for key, items in value["entities"].items())):
            raise ExecutionError("travel_memory_profile")
        self.values.append(strict_json(bounded_json(value, 8192)))


class _GovernedMemory(MemoryManager):
    def __init__(self, messages, summary, preferences, package):
        self._redis = _StagedRedis(messages, summary)
        self._client, self._model = StageClient(package, "summary"), MODEL
        self._profile = _ProfileProposals()
        self.preferences = preferences
        self.episode_proposals = []

    def _wm_key(self, user_id, conv_id):
        return "working"

    def _summary_key(self, user_id, conv_id):
        return "summary"

    async def _get_profile(self, user_id):
        return {"preferences": [item["value"] for item in self.preferences], "source": "user_confirmed"}

    async def _get_working_memory(self, user_id, conv_id):
        # Original source parser/order; legacy-key migration is inapplicable to
        # an already captured private staging list and must not touch real keys.
        return self._messages_from_raw(self._redis.command("lrange", "working", 0, self.WORKING_MAX - 1))

    async def _store_episodic(self, user_id, conv_id, text, summary):
        # Inferred summaries are not the explicit user_confirmed source required
        # by IsolatedMemoryStore.store_episode. Retain candidate, never publish.
        self.episode_proposals.append({"summary": summary, "source": "model_summary", "published": False})


@dataclass(frozen=True)
class MemoryMaintenance:
    state: str
    source_revision: int
    summary_cached: bool
    profile_proposals: tuple[dict[str, Any], ...]
    episode_proposals: tuple[dict[str, Any], ...]
    version: str = VERSION


class TravelMemoryPort:
    def __init__(self, resources: CandidateResources, *, preferences: list[dict[str, Any]],
                 read_preferences: Callable[[], Awaitable[list[dict[str, Any]]]],
                 package: FrozenTravelDomainPackage):
        if (type(resources) is not CandidateResources or not callable(read_preferences)
                or type(package) is not FrozenTravelDomainPackage):
            raise ExecutionError("travel_memory_configuration")
        self.resources, self.package = resources, package
        self._preferences = _checked_preferences(deepcopy(preferences))
        self._read_preferences, self._active, self._busy = read_preferences, None, False

    def begin(self, gate: ExecutionGate) -> None:
        """Trusted session hook; provider/execution binding belongs to the runner."""
        if self._active is not None:
            raise ExecutionError("travel_memory_busy")
        self.resources._start(gate)
        self._active = gate

    def end(self) -> None:
        if self._active is not None and self.resources._active is self._active:
            self.resources._active = None
        self._active = None

    async def _check(self, revision: int):
        gate = self.resources._gate()
        if (self._active is not gate or type(revision) is not int or revision != gate.scope.input_version):
            raise ExecutionError("travel_memory_context_changed")
        try:
            value = self._read_preferences()
            if inspect.isawaitable(value):
                value = await value
            valid = _checked_preferences(deepcopy(value)) == self._preferences
        except asyncio.CancelledError:
            raise
        except Exception:
            valid = False
        if not valid:
            gate.fail("travel_context_changed")
            raise ExecutionError("travel_memory_context_changed")
        gate.check()
        return gate

    async def _cache_call(self, method: str, *args):
        try:
            return await getattr(self.resources._store._redis, method)(*args)
        except asyncio.CancelledError:
            if self._active is not None:
                self._active.fail("cancelled")
            raise
        except Exception:
            if self._active is not None:
                self._active.fail("travel_memory_unavailable")
            raise ExecutionError("travel_memory_unavailable") from None

    def _keys(self):
        store = self.resources._store
        key = store._prefix + "working:" + _digest(self.resources.scope.conversation_id)
        return key + ":revision", key, store._prefix + "travel-summary:" + _digest(self.resources.scope.conversation_id)

    async def _working(self, revision):
        await self._check(revision)
        barrier, body, summary = await self._cache_call("mget", *self._keys())
        await self._check(revision)
        try:
            marker, working = strict_json(barrier), strict_json(body)
            if (type(marker) is not dict or set(marker) != {"revision", "digest"}
                    or marker["revision"] != revision or working["revision"] != revision):
                raise ValueError()
            cached = strict_json(summary) if summary is not None else None
            if cached is not None and (cached.get("revision"), cached.get("digest")) != (revision, marker["digest"]):
                cached = None  # Different source version is not a current summary.
            if cached is not None and (set(cached) != {"version", "revision", "digest", "summary", "recent"}
                    or cached["version"] != VERSION or type(cached["summary"]) is not str
                    or len(cached["summary"]) > MemoryManager.SUMMARY_MAX_CHARS
                    or cached["recent"] != _recent(working)):
                raise ValueError()
            return marker, working, cached
        except (ValueError, TypeError, KeyError, RuntimeFailure):
            raise ExecutionError("travel_memory_cache_invalid") from None

    async def publish(self, revision: int, messages: list[dict[str, str]], state: dict[str, Any] | None = None) -> None:
        await self._check(revision)
        await self.resources.publish_cache(revision, deepcopy(messages), deepcopy(state))
        await self._check(revision)

    async def read(self, revision: int, query: str = "") -> MemoryContext:
        await self._check(revision)
        _, _, cached = await self._working(revision)
        context = await self.resources.context(revision, query)
        await self._check(revision)
        return replace(context, user_profile={"preferences": deepcopy(self._preferences),
            "source": "mysql_user_confirmed", "inferred_profile_is_authority": False},
            summary=cached["summary"] if cached else "",
            recent_messages=context.recent_messages[-5:] if cached else context.recent_messages)

    async def context_for(self, request: Request) -> dict[str, Any]:
        gate = self.resources._gate()
        gate.check(request)
        if type(request) is not Request:
            raise ExecutionError("travel_memory_context_changed")
        revision = gate.scope.input_version
        # History here is the runner's captured, owner-checked MySQL snapshot;
        # no model-returned conversation/owner/version is accepted.
        await self.publish(revision, deepcopy(request.history or []))
        context = await self.read(revision, query=request.message)
        value = {"memory_version": VERSION, "working_messages": [{"role": item.role.value, "content": item.content}
            for item in context.recent_messages], "relevant_history": context.relevant_history,
            "summary": context.summary, "user_confirmed_preferences": deepcopy(self._preferences),
            "notice": "历史与模型摘要帮助理解，不证明当前事实、用户同意或正式草案；本次明确要求优先。"}
        return strict_json(bounded_json(value, 16_000))

    async def maintain(self, revision: int, *, infer_preferences: bool = False) -> MemoryMaintenance:
        if type(infer_preferences) is not bool or self._busy:
            raise ExecutionError("travel_memory_busy")
        gate = await self._check(revision)
        marker, working, cached = await self._working(revision)
        staged = _GovernedMemory(working["messages"], cached["summary"] if cached else "", self._preferences, self.package)
        self._busy = True
        try:
            # These are the original function objects/control flow. The staged
            # write/client ports above are the explicit safety/domain changes.
            await _drain_original(MemoryManager._compress(staged, gate.scope.owner_id, gate.scope.conversation_id), gate)
            await self._check(revision)
            if infer_preferences:
                staged._client = StageClient(self.package, "profile")
                await _drain_original(MemoryManager._update_profile(staged, gate.scope.owner_id, gate.scope.conversation_id), gate)
                await self._check(revision)
            summary = staged._redis.values["summary"]
            changed = summary != (cached["summary"] if cached else "")
            if changed:
                if type(summary) is not str or not summary.strip() or len(summary) > MemoryManager.SUMMARY_MAX_CHARS:
                    raise ExecutionError("travel_memory_summary_invalid")
                value = _encode({"version": VERSION, "revision": revision, "digest": marker["digest"],
                    "summary": summary, "recent": _recent(working)})
                # CAS protects against another process publishing B while the
                # current model worked on A. This never trims MySQL/Redis source.
                script = """local raw=redis.call('GET',KEYS[1]); if not raw then return 0 end
                local row=cjson.decode(raw); if row.revision~=tonumber(ARGV[1]) or row.digest~=ARGV[2] then return 0 end
                redis.call('SET',KEYS[2],ARGV[3],'EX',ARGV[4]); return 1"""
                keys = self._keys()
                result = await self._cache_call("eval", script, 2, keys[0], keys[2], revision,
                    marker["digest"], value, self.resources._store.config.ttl_seconds)
                if result != 1:
                    raise ExecutionError("travel_memory_context_changed")
                await self._check(revision)
            proposals = tuple({"source": "model_inference", "consent": False,
                "awaiting_user_confirmation": True, "value": deepcopy(value)} for value in staged._profile.values)
            state = "summary_cached" if changed else "no_summary_change"
            if infer_preferences and not proposals:
                state = "profile_unavailable" if not changed else "summary_cached_profile_unavailable"
            return MemoryMaintenance(state, revision, changed, proposals, tuple(staged.episode_proposals))
        except asyncio.CancelledError:
            gate.fail("cancelled")
            raise
        except StorageError:
            gate.fail("travel_memory_unavailable")
            raise ExecutionError("travel_memory_unavailable") from None
        finally:
            self._busy = False


def _recent(working):
    # Adapter timestamps can change on idempotent cache reconstruction; the
    # source message role/content, rather than cache-write time, is the identity.
    return [{"role": row["role"], "content": row["content"]}
        for row in (strict_json(raw) for raw in working["messages"][-5:])]


async def _drain_original(operation, gate):
    """Cancel publication immediately, drain already-started original work.

    The original profile method uses to_thread. Cancelling its await does not
    stop that worker, so keep the staged operation alive while latching the
    shared gate: no next model send or persistent publication can start. Its
    existing provider deadline still bounds an in-flight request; it may bill.
    """
    worker = asyncio.create_task(operation)
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        gate.fail("cancelled")
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if worker.done() and not worker.cancelled():
            worker.exception()
        raise

"""Opt-in, owner-scoped storage adapters for the preserved EchoMind core.

Nothing here reads environment variables, connects to default services, seeds
documents, downloads embeddings, writes MySQL, or performs automatic profiling.
MySQL remains authoritative. A caller must supply an explicit embedding function
and freshly checked source revisions; these adapters cannot authenticate them.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
from typing import Any, TypeVar

import chromadb
from chromadb.telemetry.product import ProductTelemetryClient, ProductTelemetryEvent
import redis.asyncio as redis
from overrides import override

from echomind_core.upstream.memory.conversation_memory import MemoryContext, MemoryManager, Message
from echomind_core.upstream.core.concurrency import redis_lease
from echomind_core.upstream.tooling.knowledge_base import KnowledgeBase

EmbeddingFunction = Callable[[Sequence[str]], Awaitable[Sequence[Sequence[float]]]]
T = TypeVar("T")
_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_MODEL = re.compile(r"[A-Za-z0-9_.:/-]{1,128}\Z")
_VERSION = "echo-storage-v1"
_MAX_BYTES = 65_536
_COLLECTION_LIMITS = {"knowledge": 128, "episodes": 128, "preferences": 32}


class StorageError(RuntimeError):
    """Safe enum-like codes only; no path, text, or raw service exceptions."""


class NoTelemetry(ProductTelemetryClient):
    """Explicit Chroma component: no exporter, home-directory ID, or network."""

    @override
    def capture(self, event: ProductTelemetryEvent) -> None:
        return None


def _digest(*parts: str) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=True).encode()).hexdigest()


def _text(value: Any, maximum: int = 4096) -> str:
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise StorageError("storage_input")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        raise StorageError("storage_input") from None
    return value


def _revision(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= 2**53 - 1:
        raise StorageError("storage_revision")
    return value


def _encode(value: Any) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError):
        raise StorageError("storage_input") from None
    if len(raw.encode()) > _MAX_BYTES:
        raise StorageError("storage_input_limit")
    return raw


def _private_directory(path: Path, *, create: bool = False) -> Path:
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise StorageError("storage_path")
    for parent in (*reversed(path.parents), path):
        if parent.is_symlink():
            raise StorageError("storage_path_link")
    if create and not path.exists():
        # Only one leaf may be created, inside an already-private parent.
        _private_directory(path.parent)
        path.mkdir(mode=0o700)
    try:
        info = path.lstat()
    except OSError:
        raise StorageError("storage_path") from None
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise StorageError("storage_path_permissions")
    return path


def _private_tree(path: Path) -> None:
    """Existing Chroma files cannot redirect writes into another data store."""
    for index, entry in enumerate(path.rglob("*")):
        if index >= 4096:
            raise StorageError("storage_path_limit")
        info = entry.lstat()
        if (not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                or info.st_uid != os.getuid() or info.st_mode & 0o7022
                or stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
            raise StorageError("storage_path_entry")


@dataclass(frozen=True)
class StorageConfig:
    namespace: str
    redis_socket: Path
    chroma_path: Path
    embedding_model: str
    embedding_dimensions: int
    ttl_seconds: int = 86_400

    def validate(self) -> None:
        try:
            self._validate()
        except StorageError:
            raise
        except Exception:
            raise StorageError("storage_configuration") from None

    def _validate(self) -> None:
        if (type(self.namespace) is not str or not _NAME.fullmatch(self.namespace)
                or type(self.embedding_model) is not str or not _MODEL.fullmatch(self.embedding_model)
                or type(self.embedding_dimensions) is not int or not 2 <= self.embedding_dimensions <= 4096
                or type(self.ttl_seconds) is not int or not 1 <= self.ttl_seconds <= 86_400):
            raise StorageError("storage_configuration")
        if not isinstance(self.redis_socket, Path) or not isinstance(self.chroma_path, Path):
            raise StorageError("storage_path")
        socket = Path(self.redis_socket)
        if not socket.is_absolute() or ".." in socket.parts or socket.is_symlink():
            raise StorageError("storage_socket")
        _private_directory(socket.parent)
        try:
            info = socket.lstat()
        except OSError:
            raise StorageError("storage_socket") from None
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise StorageError("storage_socket_permissions")
        _private_tree(_private_directory(Path(self.chroma_path), create=True))


async def _drain_thread(function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Cancellation waits for the synchronous DB operation to actually finish."""
    worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        # Retrieve worker errors without replacing cancellation or logging input.
        if worker.done() and not worker.cancelled():
            worker.exception()
        raise


class _ScopedCollection:
    """Inject explicit vectors and mandatory scope into the original algorithms."""

    def __init__(self, collection: Any, scope: str, vectors: Mapping[str, Sequence[float]],
                 limit: int = _COLLECTION_LIMITS["knowledge"]):
        self.collection, self.scope, self.vectors, self.limit = collection, scope, vectors, limit

    def _bounded_count(self) -> int:
        count = self.collection.count()
        if count > self.limit:
            raise StorageError("storage_collection_limit")
        return count

    def _where(self, where: Any = None) -> dict[str, Any]:
        mandatory = [{"scope": self.scope}, {"storage_version": _VERSION}]
        return {"$and": mandatory + ([where] if where else [])}

    def _embedding(self, text: str) -> list[float]:
        try:
            return list(self.vectors[text])
        except KeyError:
            raise StorageError("storage_explicit_embedding_required") from None

    def get(self, **kwargs: Any) -> Any:
        self._bounded_count()
        kwargs["where"] = self._where(kwargs.get("where"))
        kwargs["limit"] = self.limit
        return self.collection.get(**kwargs)

    def count(self) -> int:
        self._bounded_count()
        return len(self.collection.get(where=self._where(), include=[])["ids"])

    def upsert(self, *, ids: list[str], documents: list[str], metadatas: list[dict[str, Any]]) -> None:
        count = self._bounded_count()
        existing = self.collection.get(ids=ids, where=self._where(), include=[])["ids"]
        if count + len(set(ids) - set(existing)) > self.limit:
            raise StorageError("storage_collection_limit")
        self.collection.upsert(ids=ids, documents=documents,
            embeddings=[self._embedding(text) for text in documents],
            metadatas=[{**meta, "scope": self.scope, "storage_version": _VERSION} for meta in metadatas])

    def delete(self, *, ids: list[str]) -> None:
        self.collection.delete(ids=ids, where=self._where())

    def query(self, *, query_texts: list[str], n_results: int, where: Any = None) -> Any:
        return self.collection.query(query_embeddings=[self._embedding(text) for text in query_texts],
            n_results=n_results, where=self._where(where))


class _SafeMemory(MemoryManager):
    """Reuse original context assembly, but never construct its unsafe defaults."""

    def __init__(self, store: IsolatedMemoryStore, conversation: str, revision: int,
                 working: dict[str, Any], vectors: Mapping[str, Sequence[float]]):
        self.store, self.conversation_id, self.revision = store, conversation, revision
        self.working = working
        self.collection = _ScopedCollection(store._collections["episodes"], store._scope, vectors,
                                            _COLLECTION_LIMITS["episodes"])
        # Original get_context reads only these two cache keys after callbacks.
        self._redis = self

    async def get(self, key: str) -> str | None:
        return _encode(self.working.get("state", {})) if key == "state" else None

    def _summary_key(self, user_id: str, conv_id: str) -> str:
        return "summary"

    def _state_key(self, user_id: str, conv_id: str) -> str:
        return "state"

    async def _get_working_memory(self, user_id: str, conv_id: str) -> list[Message]:
        return self._messages_from_raw(list(reversed(self.working.get("messages", []))))

    async def _search_episodic(self, user_id: str, conv_id: str, query: str) -> list[str]:
        if not query.strip():
            return []
        def search() -> list[str]:
            count = self.collection.count()
            if not count:
                return []
            results = self.collection.query(query_texts=[query], n_results=min(self.HISTORY_TOP_K, count),
                                            where={"conversation": _digest(self.conversation_id)})
            docs = self._extract_docs(results)
            if len(docs) < self.HISTORY_TOP_K:
                fallback = self.collection.query(query_texts=[query], n_results=min(self.HISTORY_TOP_K, count))
                docs.extend(self._extract_docs(fallback))
            return self._dedupe_texts(docs)[:self.HISTORY_TOP_K]
        return await _drain_thread(search)

    async def _get_profile(self, user_id: str) -> dict[str, Any]:
        return await self.store._read_preferences()


class IsolatedMemoryStore:
    """Explicit candidate storage, not imported by the active application.

    Redis is an expendable conversation cache, never the quota/identity source.
    Preference tombstones and pending markers are intentionally non-expiring;
    a persistent marker failure closes writes/reads rather than using old data.
    There is no automatic cache rebuild or unknown-outcome retry.
    """

    def __init__(self) -> None:
        raise StorageError("use_explicit_open")

    @classmethod
    async def open(cls, config: StorageConfig, owner_id: str,
                   embedder: EmbeddingFunction) -> IsolatedMemoryStore:
        if not isinstance(config, StorageConfig):
            raise StorageError("storage_configuration")
        owner_id = _text(owner_id, 128)
        if not callable(embedder):
            raise StorageError("storage_configuration")
        await _drain_thread(config.validate)
        self = object.__new__(cls)
        self.config, self._owner, self._embedder = config, owner_id, embedder
        self._scope = _digest(config.namespace, owner_id)
        self._prefix = f"echo:{self._scope}:"
        self._closed = False
        self._operations: set[asyncio.Task[Any]] = set()
        self._lock = asyncio.Lock()
        self._write_lock = threading.RLock()
        self._redis = redis.Redis(unix_socket_path=str(config.redis_socket), decode_responses=True,
                                 socket_connect_timeout=2, socket_timeout=2, retry_on_timeout=False,
                                 health_check_interval=0)
        try:
            await self._redis.ping()
            redis_config = await self._redis.config_get("appendonly", "appendfsync", "maxmemory-policy", "port", "maxmemory", "dir")
            if (redis_config.get("appendonly") != "yes" or redis_config.get("appendfsync") != "always"
                    or redis_config.get("maxmemory-policy") != "noeviction" or redis_config.get("port") != "0"
                    or not str(redis_config.get("maxmemory", "")).isdigit()
                    or not 1 <= int(redis_config["maxmemory"]) <= 64 * 1024 * 1024):
                raise StorageError("storage_redis_persistence_configuration")
            await _drain_thread(_private_directory, Path(redis_config.get("dir", "")))
            def initialize() -> tuple[Any, dict[str, Any]]:
                client = chromadb.PersistentClient(path=str(config.chroma_path),
                    settings=chromadb.Settings(anonymized_telemetry=False, allow_reset=False,
                        chroma_product_telemetry_impl="echomind_core.storage.NoTelemetry"))
                expected = {"scope": self._scope, "storage_version": _VERSION,
                            "embedding_model": config.embedding_model,
                            "embedding_dimensions": config.embedding_dimensions, "hnsw:space": "cosine"}
                collections = {}
                for kind in ("knowledge", "episodes", "preferences"):
                    # Chroma 0.5 collection names are bounded to 63 characters.
                    # Full 256-bit scope remains mandatory metadata/filter.
                    name = f"echo-{kind}-{self._scope[:40]}"
                    try:
                        collection = client.get_collection(name, embedding_function=None)
                    except chromadb.errors.InvalidCollectionException:
                        collection = client.create_collection(name, metadata=expected, embedding_function=None)
                    if collection.metadata != expected:
                        raise StorageError("storage_embedding_version_mismatch")
                    collections[kind] = collection
                return client, collections
            self._chroma, self._collections = await _drain_thread(initialize)
        except asyncio.CancelledError:
            await self._redis.aclose()
            raise
        except StorageError:
            await self._redis.aclose()
            raise
        except Exception:
            await self._redis.aclose()
            raise StorageError("storage_open_failed") from None
        return self

    def _check(self) -> None:
        if self._closed:
            raise StorageError("storage_closed")

    async def _run(self, action: Callable[[], Awaitable[T]]) -> T:
        self._check()
        task = asyncio.current_task()
        assert task is not None
        self._operations.add(task)
        try:
            return await action()
        except (StorageError, asyncio.CancelledError):
            raise
        except Exception:
            raise StorageError("storage_unavailable") from None
        finally:
            self._operations.discard(task)

    async def _vectors(self, texts: Sequence[str]) -> dict[str, list[float]]:
        unique = list(dict.fromkeys(texts))
        if not unique:
            return {}
        if len(unique) > 10 or sum(len(t.encode()) for t in unique) > _MAX_BYTES:
            raise StorageError("storage_embedding_input_limit")
        vectors = await self._embedder(tuple(unique))
        if len(vectors) != len(unique):
            raise StorageError("storage_embedding_invalid")
        output = {}
        for text, vector in zip(unique, vectors):
            if (len(vector) != self.config.embedding_dimensions or
                    any(type(v) not in (int, float) or not math.isfinite(v) for v in vector) or
                    not any(vector)):
                raise StorageError("storage_embedding_invalid")
            output[text] = [float(v) for v in vector]
        return output

    def _knowledge(self, vectors: Mapping[str, Sequence[float]]) -> KnowledgeBase:
        kb = object.__new__(KnowledgeBase)
        kb._write_lock = self._write_lock
        kb._collection = _ScopedCollection(self._collections["knowledge"], self._scope, vectors)
        return kb

    async def add_documents(self, documents: list[dict[str, str]]) -> int:
        documents = json.loads(_encode(documents))
        async def action() -> int:
            if type(documents) is not list or not 1 <= len(documents) <= 32:
                raise StorageError("storage_documents_limit")
            _encode(documents)
            chunks = []
            for document in documents:
                if type(document) is not dict or set(document) - {"title", "content", "document_id"}:
                    raise StorageError("storage_document")
                _text(document.get("title"), 256)
                content = _text(document.get("content"), 32_000)
                if "document_id" in document:
                    _text(document["document_id"], 128)
                chunks.extend(KnowledgeBase._chunk_text(None, content, 500))
            vectors = await self._vectors(chunks)
            async with self._lock:
                async with redis_lease(self._redis, self._prefix + "vector-write", wait_s=2, lease_s=15):
                    return await _drain_thread(self._knowledge(vectors).add_documents, documents)
        return await self._run(action)

    async def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        async def action() -> list[dict[str, Any]]:
            _text(query)
            if type(top_k) is not int or not 1 <= top_k <= 20:
                raise StorageError("storage_top_k")
            vectors = await self._vectors([query])
            result = await _drain_thread(self._knowledge(vectors).search, query, top_k)
            _encode(result)
            return result
        return await self._run(action)

    async def publish_working(self, conversation_id: str, revision: int,
                              messages: list[dict[str, Any]], state: dict[str, Any] | None = None) -> None:
        messages, state = json.loads(_encode(messages)), json.loads(_encode(state))
        async def action() -> None:
            _text(conversation_id, 128)
            _revision(revision)
            if type(messages) is not list or len(messages) > 20 or (state is not None and type(state) is not dict):
                raise StorageError("storage_working_input")
            normalized = []
            for message in messages:
                if type(message) is not dict or set(message) != {"role", "content"} or message["role"] not in ("user", "assistant"):
                    raise StorageError("storage_working_input")
                normalized.append(_encode({"role": message["role"], "content": _text(message["content"]),
                    "ts": datetime.now().isoformat(), "metadata": {"source": "authoritative_snapshot"}}))
            body = _encode({"revision": revision, "messages": normalized, "state": state or {}})
            # Digest excludes adapter timestamps so same source snapshot can be idempotent.
            fingerprint = _digest(_encode(messages), _encode(state or {}))
            key = self._prefix + "working:" + _digest(conversation_id)
            script = """local raw=redis.call('GET',KEYS[1]); if raw then
              local old=cjson.decode(raw); local incoming=tonumber(ARGV[1]);
              if old.revision>incoming then return -1 end
              if old.revision==incoming and old.digest~=ARGV[2] then return -2 end end
              if redis.call('SISMEMBER',KEYS[3],ARGV[5])==0 and redis.call('SCARD',KEYS[3])>=64 then return -3 end
              redis.call('SADD',KEYS[3],ARGV[5]);
              redis.call('SET',KEYS[1],cjson.encode({revision=tonumber(ARGV[1]),digest=ARGV[2]}));
              redis.call('SET',KEYS[2],ARGV[3],'EX',ARGV[4]); return 1"""
            result = await self._redis.eval(script, 3, key + ":revision", key, self._prefix + "working-ids",
                                            revision, fingerprint, body, self.config.ttl_seconds, _digest(conversation_id))
            if result != 1:
                raise StorageError({-1: "storage_stale_revision", -2: "storage_revision_conflict",
                                    -3: "storage_working_limit"}.get(result, "storage_unavailable"))
        await self._run(action)

    async def store_episode(self, conversation_id: str, revision: int, text: str, *, source: str) -> None:
        """Explicit source snapshot only; no automatic model-generated summary."""
        async def action() -> None:
            _text(conversation_id, 128)
            _revision(revision)
            _text(text)
            if source != "user_confirmed":
                raise StorageError("storage_consent_required")
            vectors = await self._vectors([text])
            collection = _ScopedCollection(self._collections["episodes"], self._scope, vectors,
                                            _COLLECTION_LIMITS["episodes"])
            async with redis_lease(self._redis, self._prefix + "vector-write", wait_s=2, lease_s=15):
                existing = await _drain_thread(collection.get, ids=[_digest(conversation_id, str(revision))],
                                                include=["documents"])
                if existing["documents"] and existing["documents"] != [text]:
                    raise StorageError("storage_revision_conflict")
                await _drain_thread(collection.upsert, ids=[_digest(conversation_id, str(revision))],
                    documents=[text], metadatas=[{"conversation": _digest(conversation_id),
                        "source_revision": revision, "source": source}])
        await self._run(action)

    async def get_context(self, conversation_id: str, revision: int, query: str = "") -> MemoryContext:
        async def action() -> MemoryContext:
            _text(conversation_id, 128)
            _revision(revision)
            if query:
                _text(query)
            key = self._prefix + "working:" + _digest(conversation_id)
            preference_generation = await self._redis.get(self._prefix + "preference-generation")
            barrier, raw = await self._redis.mget(key + ":revision", key)
            if barrier and json.loads(barrier).get("revision") != revision:
                raise StorageError("storage_stale_revision")
            working = json.loads(raw) if raw else {"revision": revision, "messages": [], "state": {}}
            if working.get("revision") != revision:
                raise StorageError("storage_stale_revision")
            vectors = await self._vectors([query]) if query else {}
            # Empty query must not trigger upstream's implicit last-message embedding.
            memory = _SafeMemory(self, conversation_id, revision, working, vectors)
            result = await MemoryManager.get_context(memory, self._owner, conversation_id, query or " ")
            _encode({"recent": [message.content for message in result.recent_messages],
                     "history": result.relevant_history, "profile": result.user_profile, "state": result.state})
            latest_barrier, latest_generation = await self._redis.mget(
                key + ":revision", self._prefix + "preference-generation")
            if latest_barrier != barrier or latest_generation != preference_generation:
                raise StorageError("storage_stale_revision")
            return result
        return await self._run(action)

    async def cache_explicit_preference(self, preference_id: str, revision: int, text: str,
                                        *, consent: bool, source: str) -> None:
        _text(text)
        if consent is not True or source != "user":
            raise StorageError("storage_consent_required")
        await self._preference(preference_id, revision, text)

    async def revoke_preference(self, preference_id: str, revision: int) -> None:
        await self._preference(preference_id, revision, None)

    async def _preference(self, preference_id: str, revision: int, text: str | None) -> None:
        async def action() -> None:
            _text(preference_id, 128)
            _revision(revision)
            key = self._prefix + "preference:" + _digest(preference_id)
            async with redis_lease(self._redis, self._prefix + "vector-write", wait_s=2, lease_s=15):
                raw = await self._redis.get(key)
                old = json.loads(raw) if raw else None
                fingerprint = _digest("active" if text is not None else "revoked", text or "")
                if old and old["revision"] >= revision:
                    if old["revision"] == revision and old.get("digest") == fingerprint and old["state"] in ("ready", "revoked"):
                        return
                    raise StorageError("storage_stale_revision")
                def check_capacity() -> None:
                    collection = _ScopedCollection(self._collections["preferences"], self._scope, {},
                                                    _COLLECTION_LIMITS["preferences"])
                    if collection.count() >= _COLLECTION_LIMITS["preferences"] and not collection.get(ids=[_digest(preference_id)])["ids"]:
                        raise StorageError("storage_collection_limit")
                await _drain_thread(check_capacity)
                marker = {"revision": revision, "digest": fingerprint, "state": "pending"}
                # Register before any embedding/write. A pending NEW preference
                # has no Chroma row yet, but must still block stale cache reads.
                reserve = """if redis.call('SISMEMBER',KEYS[1],ARGV[1])==0
                  and redis.call('SCARD',KEYS[1])>=tonumber(ARGV[3]) then return 0 end
                  redis.call('SADD',KEYS[1],ARGV[1]); redis.call('SET',KEYS[2],ARGV[2]);
                  redis.call('INCR',KEYS[3]); return 1"""
                reserved = await self._redis.eval(reserve, 3, self._prefix + "preference-ids", key,
                    self._prefix + "preference-generation", _digest(preference_id), _encode(marker),
                    _COLLECTION_LIMITS["preferences"])
                if reserved != 1:
                    raise StorageError("storage_collection_limit")
                document = text if text is not None else "revoked"
                vectors = await self._vectors([document]) if text is not None else {document: [1.0] + [0.0] * (self.config.embedding_dimensions - 1)}
                collection = _ScopedCollection(self._collections["preferences"], self._scope, vectors,
                                                _COLLECTION_LIMITS["preferences"])
                await _drain_thread(collection.upsert, ids=[_digest(preference_id)], documents=[document],
                    metadatas=[{"preference_key": _digest(preference_id), "source_revision": revision,
                                "source": "user", "revoked": text is None}])
                marker["state"] = "revoked" if text is None else "ready"
                await self._redis.set(key, _encode(marker))
        await self._run(action)

    async def _read_preferences(self) -> dict[str, Any]:
        registered = await self._redis.smembers(self._prefix + "preference-ids")
        if len(registered) > _COLLECTION_LIMITS["preferences"] or any(
                type(key) is not str or not re.fullmatch(r"[a-f0-9]{64}", key) for key in registered):
            raise StorageError("storage_preference_registry")
        ordered = sorted(registered)
        raw_markers = await self._redis.mget([self._prefix + "preference:" + key for key in ordered]) if ordered else []
        markers = {}
        for key, raw in zip(ordered, raw_markers):
            if not raw:
                raise StorageError("storage_preference_marker_missing")
            marker = json.loads(raw)
            if (type(marker) is not dict or set(marker) != {"revision", "digest", "state"}
                    or type(marker["digest"]) is not str or not re.fullmatch(r"[a-f0-9]{64}", marker["digest"])
                    or marker["state"] not in ("pending", "ready", "revoked")):
                raise StorageError("storage_preference_marker_invalid")
            _revision(marker["revision"])
            if marker["state"] == "pending":
                raise StorageError("storage_preference_unresolved")
            markers[key] = marker
        rows = await _drain_thread(_ScopedCollection(self._collections["preferences"], self._scope, {},
                                                   _COLLECTION_LIMITS["preferences"]).get,
                                   include=["documents", "metadatas"])
        result, found = [], set()
        for text, meta in zip(rows["documents"], rows["metadatas"]):
            key = meta.get("preference_key")
            if key not in markers or key in found:
                raise StorageError("storage_preference_registry")
            found.add(key)
            marker = markers[key]
            revoked = marker["state"] == "revoked"
            if (marker["revision"] != meta.get("source_revision") or meta.get("source") != "user"
                    or type(meta.get("revoked")) is not bool or meta["revoked"] != revoked
                    or marker["digest"] != _digest("revoked" if revoked else "active", "" if revoked else _text(text))
                    or revoked and text != "revoked"):
                raise StorageError("storage_preference_unresolved")
            if not revoked:
                result.append({"text": text, "source": "user", "revision": marker["revision"]})
        if found != registered:
            raise StorageError("storage_preference_unresolved")
        return {"preferences": result} if result else {}

    async def maintain(self, *_: Any) -> None:
        raise StorageError("storage_automatic_profile_disabled")

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        current = asyncio.current_task()
        operations = [task for task in tuple(self._operations) if task is not current]
        for task in operations:
            task.cancel()
        if operations:
            await asyncio.gather(*operations, return_exceptions=True)
        await self._redis.aclose()
        # Chroma 0.5 has no public close API. Do not call shared private System.stop:
        # another owner in this process may share it. OS/process exit closes files.

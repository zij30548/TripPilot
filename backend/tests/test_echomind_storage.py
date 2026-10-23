"""Actual private Redis/Chroma tests; vectors are controlled, not semantic evals."""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

_READY = all(importlib.util.find_spec(name) for name in ("chromadb", "redis", "jsonschema"))
if _READY:
    from echomind_core.storage import IsolatedMemoryStore, StorageConfig, StorageError, _drain_thread

_REDIS = shutil.which("redis-server")


async def fixture_vectors(texts):
    """Deterministic fixture only: no remote/local learned model or download."""
    return [[1.0, float("住宿" in text), float("博物馆" in text), float("费用" in text)] for text in texts]


@unittest.skipUnless(_READY and _REDIS, "requires opt-in Chroma/Redis and local redis-server")
class StorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        base = "/private/tmp" if Path("/private/tmp").is_dir() else None
        self.temp = tempfile.TemporaryDirectory(prefix="echo-storage-", dir=base)
        self.path = Path(self.temp.name)
        self.socket = self.path / "redis.sock"
        self.chroma = self.path / "chroma"
        self.process = None
        self.stores = []
        real_connect = socket.socket.connect
        def private_only(connection, address):
            if connection.family in (socket.AF_INET, socket.AF_INET6):
                raise AssertionError("offline storage test attempted internet connection")
            return real_connect(connection, address)
        self.network = patch.object(socket.socket, "connect", private_only)
        self.network.start()
        self.start_redis()
        self.config = StorageConfig("controlled-test", self.socket, self.chroma, "fixture-v1", 4)

    def start_redis(self):
        self.process = subprocess.Popen([_REDIS, "--port", "0", "--unixsocket", str(self.socket),
            "--unixsocketperm", "700", "--dir", str(self.path), "--appendonly", "yes",
            "--appendfsync", "always", "--save", "", "--maxmemory", "64mb", "--maxmemory-policy", "noeviction",
            "--logfile", str(self.path / "redis.log")],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            if self.socket.exists():
                return
            if self.process.poll() is not None:
                self.fail("private Redis exited before socket creation")
            time.sleep(.02)
        self.fail("private Redis did not create socket")

    def stop_redis(self):
        if self.process is not None:
            self.process.terminate()
            self.process.wait(timeout=5)
            self.process = None

    async def asyncTearDown(self):
        for store in self.stores:
            await store.aclose()
        self.stop_redis()
        self.network.stop()
        self.temp.cleanup()

    async def open(self, owner="owner-a", config=None, embedder=fixture_vectors):
        store = await IsolatedMemoryStore.open(config or self.config, owner, embedder)
        self.stores.append(store)
        return store

    async def test_real_collection_reuses_original_chunks_updates_and_owner_filter(self):
        first = await self.open()
        other = await self.open("owner-b")
        from echomind_core.upstream.tooling.knowledge_base import KnowledgeBase
        content = "住宿说明。" * 120
        expected = KnowledgeBase._chunk_text(None, content, 500)
        self.assertEqual(await first.add_documents([{"title": "住宿", "content": content}]), len(expected))
        results = await first.search("住宿")
        self.assertEqual({r["content"] for r in results}, set(expected))
        self.assertTrue(all(r["version"] == hashlib.sha256(content.encode()).hexdigest() for r in results))
        self.assertEqual(await other.search("住宿"), [])
        await first.add_documents([{"title": "住宿", "content": "新的住宿说明"}])
        self.assertEqual([r["content"] for r in await first.search("住宿")], ["新的住宿说明"])
        await first.aclose()
        # Closing one owner cannot stop the shared Chroma system for another.
        await other.add_documents([{"title": "独立", "content": "独立资料"}])
        self.assertEqual(len(await other.search("独立")), 1)

    async def test_working_revision_conflict_aba_and_owner_context(self):
        store = await self.open()
        other = await self.open("owner-b")
        await store.publish_working("conversation", 1, [{"role": "user", "content": "住宿"}], {"step": 1})
        context = await store.get_context("conversation", 1)
        self.assertEqual(context.recent_messages[0].content, "住宿")
        self.assertEqual(context.state, {"step": 1})
        await store.publish_working("conversation", 2, [{"role": "assistant", "content": "B"}])
        with self.assertRaisesRegex(StorageError, "stale_revision"):
            await store.publish_working("conversation", 1, [{"role": "user", "content": "住宿"}])
        with self.assertRaisesRegex(StorageError, "revision_conflict"):
            await store.publish_working("conversation", 2, [{"role": "assistant", "content": "A"}])
        with self.assertRaisesRegex(StorageError, "stale_revision"):
            await store.get_context("conversation", 1)
        self.assertEqual((await other.get_context("conversation", 1)).recent_messages, [])

    async def test_working_ttl_does_not_expire_revision_barrier(self):
        config = StorageConfig("controlled-test", self.socket, self.chroma, "fixture-v1", 4, ttl_seconds=1)
        store = await self.open(config=config)
        await store.publish_working("conversation", 4, [{"role": "user", "content": "A"}])
        await asyncio.sleep(1.1)
        self.assertEqual((await store.get_context("conversation", 4)).recent_messages, [])
        with self.assertRaisesRegex(StorageError, "stale_revision"):
            await store.publish_working("conversation", 3, [{"role": "user", "content": "old"}])

    async def test_consent_revoke_tombstone_and_no_profile_llm(self):
        store = await self.open()
        with self.assertRaisesRegex(StorageError, "consent_required"):
            await store.cache_explicit_preference("walking", 1, "喜欢步行", consent=False, source="user")
        with self.assertRaisesRegex(StorageError, "consent_required"):
            await store.cache_explicit_preference("walking", 1, "喜欢步行", consent=True, source="model")
        await store.cache_explicit_preference("walking", 1, "喜欢步行", consent=True, source="user")
        profile = (await store.get_context("conversation", 0)).user_profile
        self.assertEqual(profile["preferences"], [{"text": "喜欢步行", "source": "user", "revision": 1}])
        await store.revoke_preference("walking", 2)
        self.assertEqual((await store.get_context("conversation", 0)).user_profile, {})
        with self.assertRaisesRegex(StorageError, "stale_revision"):
            await store.cache_explicit_preference("walking", 1, "喜欢步行", consent=True, source="user")
        with self.assertRaisesRegex(StorageError, "automatic_profile_disabled"):
            await store.maintain()
        await store.cache_explicit_preference("walking", 3, "现在喜欢公交", consent=True, source="user")
        self.assertEqual((await store.get_context("conversation", 0)).user_profile["preferences"][0]["revision"], 3)
        await store.cache_explicit_preference("literal", 1, "revoked", consent=True, source="user")
        with self.assertRaisesRegex(StorageError, "stale_revision"):
            await store.revoke_preference("literal", 1)

    async def test_interrupted_preference_never_replays_old_value(self):
        store = await self.open()
        await store.cache_explicit_preference("walking", 1, "旧值", consent=True, source="user")
        with patch.object(store, "_vectors", side_effect=StorageError("controlled_failure")):
            with self.assertRaisesRegex(StorageError, "controlled_failure"):
                await store.cache_explicit_preference("walking", 2, "新值", consent=True, source="user")
        with self.assertRaisesRegex(StorageError, "preference_unresolved"):
            await store.get_context("conversation", 0)
        with self.assertRaisesRegex(StorageError, "stale_revision"):
            await store.cache_explicit_preference("walking", 2, "新值", consent=True, source="user")

    async def test_new_pending_preference_without_chroma_row_blocks_context(self):
        store = await self.open()
        with patch.object(store, "_vectors", side_effect=StorageError("controlled_failure")):
            with self.assertRaisesRegex(StorageError, "controlled_failure"):
                await store.cache_explicit_preference("first", 1, "首次偏好", consent=True, source="user")
        self.assertEqual(store._collections["preferences"].count(), 0)
        self.assertEqual(await store._redis.scard(store._prefix + "preference-ids"), 1)
        with self.assertRaisesRegex(StorageError, "preference_unresolved"):
            await store.get_context("conversation", 0)
        with self.assertRaisesRegex(StorageError, "stale_revision"):
            await store.cache_explicit_preference("first", 1, "首次偏好", consent=True, source="user")
        # Explicitly newer authoritative revision can resolve; no hidden replay.
        await store.revoke_preference("first", 2)
        self.assertEqual((await store.get_context("conversation", 0)).user_profile, {})

    async def test_preference_registry_limit_applies_even_without_chroma_rows(self):
        store = await self.open()
        await store._redis.sadd(store._prefix + "preference-ids", *[f"{number:064x}" for number in range(32)])
        calls = []
        async def embed(texts):
            calls.append(texts)
            return await fixture_vectors(texts)
        store._embedder = embed
        with self.assertRaisesRegex(StorageError, "collection_limit"):
            await store.cache_explicit_preference("new", 1, "超过登记数量", consent=True, source="user")
        self.assertEqual(await store._redis.scard(store._prefix + "preference-ids"), 32)
        self.assertEqual(calls, [])

    async def test_revocation_waits_for_cancelled_writer_then_blocks_old_reinsert(self):
        store = await self.open()
        other = await self.open()
        from echomind_core.storage import _ScopedCollection
        entered, release = threading.Event(), threading.Event()
        original = _ScopedCollection.upsert
        def blocked(collection, **kwargs):
            if kwargs["documents"] == ["旧偏好"]:
                entered.set()
                release.wait(5)
            return original(collection, **kwargs)
        with patch.object(_ScopedCollection, "upsert", blocked):
            writing = asyncio.create_task(store.cache_explicit_preference("walking", 1, "旧偏好", consent=True, source="user"))
            await asyncio.to_thread(entered.wait, 2)
            writing.cancel()
            revoking = asyncio.create_task(other.revoke_preference("walking", 2))
            await asyncio.sleep(.03)
            self.assertFalse(revoking.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await writing
            await revoking
        self.assertEqual((await other.get_context("conversation", 0)).user_profile, {})
        with self.assertRaisesRegex(StorageError, "stale_revision"):
            await store.cache_explicit_preference("walking", 1, "旧偏好", consent=True, source="user")

    async def test_explicit_episodes_reuse_context_without_default_download(self):
        calls = []
        async def embed(texts):
            calls.extend(texts)
            return await fixture_vectors(texts)
        store = await self.open(embedder=embed)
        self.assertEqual(calls, [])
        self.assertIsNone(store._collections["episodes"]._embedding_function)
        await store.store_episode("one", 1, "博物馆计划", source="user_confirmed")
        with self.assertRaisesRegex(StorageError, "revision_conflict"):
            await store.store_episode("one", 1, "不能覆盖相同版本", source="user_confirmed")
        await store.store_episode("two", 1, "住宿计划", source="user_confirmed")
        context = await store.get_context("one", 1, "博物馆")
        self.assertEqual(set(context.relevant_history), {"博物馆计划", "住宿计划"})
        self.assertEqual(calls, ["博物馆计划", "不能覆盖相同版本", "住宿计划", "博物馆"])
        self.assertEqual((await (await self.open("other")).get_context("one", 1, "博物馆")).relevant_history, [])

    async def test_embedding_version_dimension_and_invalid_vectors_rejected(self):
        store = await self.open()
        changed = StorageConfig("controlled-test", self.socket, self.chroma, "fixture-v2", 4)
        with self.assertRaisesRegex(StorageError, "embedding_version_mismatch"):
            await self.open(config=changed)
        changed = StorageConfig("controlled-test", self.socket, self.chroma, "fixture-v1", 8)
        with self.assertRaisesRegex(StorageError, "embedding_version_mismatch"):
            await self.open(config=changed)
        async def wrong(texts):
            return [[float("nan"), 0, 0, 0] for _ in texts]
        store._embedder = wrong
        with self.assertRaisesRegex(StorageError, "embedding_invalid"):
            await store.search("资料")

    async def test_scope_metadata_filter_and_collection_capacity_are_enforced(self):
        store = await self.open()
        from echomind_core.storage import _ScopedCollection
        wrapped = _ScopedCollection(store._collections["knowledge"], store._scope,
                                    {"one": [1., 0., 0., 0.], "two": [1., 0., 0., 0.], "three": [1., 0., 0., 0.]}, 2)
        wrapped.upsert(ids=["one", "two"], documents=["one", "two"], metadatas=[{}, {}])
        with self.assertRaisesRegex(StorageError, "collection_limit"):
            wrapped.upsert(ids=["three"], documents=["three"], metadatas=[{}])
        store._collections["episodes"].upsert(ids=["foreign"], documents=["foreign owner"],
            embeddings=[[1., 0., 0., 0.]], metadatas=[{"scope": "foreign", "storage_version": "echo-storage-v1"}])
        self.assertEqual((await store.get_context("conversation", 0, "anything")).relevant_history, [])

    async def test_revision_change_while_embedding_rejects_late_context(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def delayed(texts):
            started.set()
            await release.wait()
            return await fixture_vectors(texts)
        store = await self.open(embedder=delayed)
        await store.publish_working("conversation", 1, [{"role": "user", "content": "A"}])
        pending = asyncio.create_task(store.get_context("conversation", 1, "query"))
        await started.wait()
        await store.publish_working("conversation", 2, [{"role": "user", "content": "B"}])
        release.set()
        with self.assertRaisesRegex(StorageError, "stale_revision"):
            await pending

    async def test_open_has_no_default_embedding_download_or_telemetry_export(self):
        from chromadb.telemetry.product.posthog import Posthog
        with patch.object(Posthog, "capture", side_effect=AssertionError("telemetry must not be constructed")):
            store = await self.open()
            self.assertFalse(store._chroma.get_settings().anonymized_telemetry)
            self.assertTrue(all(c._embedding_function is None for c in store._collections.values()))

    async def test_batch_limit_prevents_hidden_embedding_fanout(self):
        calls = []
        async def embed(texts):
            calls.append(texts)
            return await fixture_vectors(texts)
        store = await self.open(embedder=embed)
        docs = [{"title": str(i), "content": "资料" + str(i)} for i in range(11)]
        with self.assertRaisesRegex(StorageError, "embedding_input_limit"):
            await store.add_documents(docs)
        self.assertEqual(calls, [])

    async def test_private_path_socket_symlink_and_configuration_fail_closed(self):
        public = self.path / "public"
        public.mkdir(mode=0o755)
        with self.assertRaisesRegex(StorageError, "path_permissions"):
            await self.open(config=StorageConfig("controlled-test", self.socket, public, "fixture-v1", 4))
        alias = self.path / "alias"
        alias.symlink_to(self.chroma)
        with self.assertRaisesRegex(StorageError, "path_link"):
            await self.open(config=StorageConfig("controlled-test", self.socket, alias, "fixture-v1", 4))
        link = self.path / "alias.sock"
        link.symlink_to(self.socket)
        with self.assertRaisesRegex(StorageError, "storage_socket"):
            await self.open(config=StorageConfig("controlled-test", link, self.chroma, "fixture-v1", 4))
        with self.assertRaisesRegex(StorageError, "configuration"):
            await self.open(config=StorageConfig("bad:name", self.socket, self.chroma, "fixture-v1", 4))
        with self.assertRaisesRegex(StorageError, "configuration"):
            await self.open(config=StorageConfig(None, self.socket, self.chroma, "fixture-v1", 4))
        self.chroma.mkdir(mode=0o700, exist_ok=True)
        (self.chroma / "chroma.sqlite3").symlink_to(self.path / "outside.sqlite3")
        with self.assertRaisesRegex(StorageError, "path_entry"):
            await self.open()

    async def test_redis_restart_aof_preserves_revision_and_revocation(self):
        store = await self.open()
        await store.publish_working("conversation", 2, [{"role": "user", "content": "旅行"}])
        await store.cache_explicit_preference("walking", 1, "步行", consent=True, source="user")
        await store.revoke_preference("walking", 2)
        await store.aclose()
        self.stop_redis()
        self.start_redis()
        reopened = await self.open()
        self.assertEqual((await reopened.get_context("conversation", 2)).recent_messages[0].content, "旅行")
        with self.assertRaisesRegex(StorageError, "stale_revision"):
            await reopened.cache_explicit_preference("walking", 1, "步行", consent=True, source="user")
        with self.assertRaisesRegex(StorageError, "stale_revision"):
            await reopened.publish_working("conversation", 1, [{"role": "user", "content": "old"}])

    async def test_real_chroma_survives_distinct_processes(self):
        code = '''import asyncio,json,sys
from pathlib import Path
from echomind_core.storage import IsolatedMemoryStore,StorageConfig
async def embed(texts): return [[1.,0.,0.,0.] for _ in texts]
async def run():
 s=await IsolatedMemoryStore.open(StorageConfig("child-test",Path(sys.argv[1]),Path(sys.argv[2]),"fixture-v1",4),"owner",embed)
 if sys.argv[3]=="write": await s.add_documents([{"title":"持久资料","content":"真实磁盘内容"}])
 else: print(json.dumps(await s.search("内容"),ensure_ascii=True))
 await s.aclose()
asyncio.run(run())
'''
        args = [sys.executable, "-B", "-c", code, str(self.socket), str(self.path / "child-chroma")]
        for operation in ("write", "read"):
            process = await asyncio.create_subprocess_exec(*args, operation, stdout=asyncio.subprocess.PIPE,
                                                         stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(process.communicate(), 30)
            self.assertEqual(process.returncode, 0, stderr.decode()[-1000:])
            if operation == "read":
                self.assertEqual(json.loads(stdout)[0]["content"], "真实磁盘内容")

    async def test_redis_failure_no_fallback_and_close_rejects(self):
        store = await self.open()
        self.stop_redis()
        with self.assertRaisesRegex(StorageError, "storage_unavailable"):
            await store.publish_working("conversation", 1, [])
        await store.aclose()
        with self.assertRaisesRegex(StorageError, "storage_closed"):
            await store.search("资料")

    async def test_existing_redis_without_durable_tombstones_is_rejected(self):
        store = await self.open()
        await store._redis.config_set("appendonly", "no")
        with self.assertRaisesRegex(StorageError, "redis_persistence_configuration"):
            await self.open()

    async def test_cancel_worker_drains_before_release_and_repeat_cancel(self):
        entered, finish = threading.Event(), threading.Event()
        def blocking():
            entered.set()
            finish.wait(5)
            return True
        task = asyncio.create_task(_drain_thread(blocking))
        await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(.03)
        task.cancel()
        self.assertFalse(task.done())
        finish.set()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_close_drains_active_collection_worker_before_releasing_lease(self):
        store = await self.open()
        observer = await self.open()
        entered, release = threading.Event(), threading.Event()
        from echomind_core.upstream.tooling.knowledge_base import KnowledgeBase
        def blocking(documents):
            entered.set()
            release.wait(5)
            return 1
        with patch.object(KnowledgeBase, "add_documents", side_effect=blocking):
            operation = asyncio.create_task(store.add_documents([{"title": "测试", "content": "受控内容"}]))
            await asyncio.to_thread(entered.wait, 2)
            closing = asyncio.create_task(store.aclose())
            await asyncio.sleep(.03)
            self.assertFalse(closing.done())
            self.assertIsNotNone(await observer._redis.get(store._prefix + "vector-write"))
            release.set()
            await closing
            with self.assertRaises(asyncio.CancelledError):
                await operation
            self.assertIsNone(await observer._redis.get(store._prefix + "vector-write"))


if __name__ == "__main__":
    unittest.main()

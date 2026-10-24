"""Full production ASGI/MySQL admission plus real private Redis/Chroma.

Only outbound model transport, classifications and vectors are CONTROLLED.
These scenarios prove protocol/state wiring, never natural-language quality.
The DB is a new random loopback test database; no application store is read.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import unittest
from unittest.mock import patch
from uuid import uuid4

if any(importlib.util.find_spec(name) is None for name in ("chromadb", "redis", "jsonschema", "prometheus_client", "pymysql")):
    raise unittest.SkipTest("explicit echomind extra and isolated MySQL required")

import httpx
from sqlalchemy import text

from core.config import Settings, get_settings
from echomind_core.embedding import DIMENSIONS, EMBEDDING_MODEL
from echomind_core.integration_full import close_full_services
from echomind_core.runtime import CandidateResources
from echomind_core.travel_domain import TravelIntent
from echomind_core.upstream.agents.agent_orchestrator import AgentOrchestrator, ResponseComposer
from tests import test_echomind_runtime as resource_fixture
from tests.mysql_support import temporary_mysql
from tests.test_echomind_travel_engine import FABRICATED, native_text, native_tools
from tests.test_full_resources import template_files
from memory.chat_storage import ChatStore
from tools.echomind_travel_controlled import ControlledUpstream, ORIGIN, controlled_app, initialize


class FullControlledUpstream(ControlledUpstream):
    def __init__(self):
        super().__init__()
        self.intent = TravelIntent.CLARIFY
        self.sent: list[dict] = []
        self.hold_agent = False
        self.agent_entered, self.agent_release = asyncio.Event(), asyncio.Event()
        self.hold_memory = False
        self.memory_entered, self.memory_release = asyncio.Event(), asyncio.Event()

    async def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != "dashscope.aliyuncs.com" or request.method != "POST":
            raise AssertionError("unplanned external endpoint")
        payload = json.loads(request.content)
        self.sent.append({"path": request.url.path, "sha256": hashlib.sha256(request.content).hexdigest()})
        if request.url.path.endswith("/embeddings"):
            if self.hold_memory and len(self.sent) == 1:
                self.memory_entered.set()
                await self.memory_release.wait()
            vector = [0.0] * DIMENSIONS
            vector[list(TravelIntent).index(self.intent)] = 1.0
            return httpx.Response(200, json={"object": "list", "model": EMBEDDING_MODEL,
                "data": [{"object": "embedding", "index": index, "embedding": vector}
                    for index in range(len(payload["input"]))], "usage": {"total_tokens": 4}})
        if request.url.path != "/compatible-mode/v1/chat/completions":
            raise AssertionError("unplanned model path")
        if "tools" not in payload:
            system = payload["messages"][0]["content"]
            response = native_text(json.dumps({"intent": self.intent.value, "confidence": .99,
                "reasoning": "controlled classifier only"}) if "阶段契约：classifier" in system else FABRICATED)
        else:
            if self.hold_agent:
                self.agent_entered.set()
                await self.agent_release.wait()
            names = {tool["function"]["name"] for tool in payload["tools"]}
            if names == {"travel_schedule"}:
                response = native_tools(("controlled_day", "travel_schedule",
                    {"action": "read_schedule", "date": None, "day": 1}))
            elif names == {"travel_budget"}:
                response = native_tools(("controlled_budget", "travel_budget", {"action": "read_budget"}))
            else:
                response = native_tools(("controlled_clarify", "travel_clarify", {"reason": "date_missing"}))
        return httpx.Response(200, json=response)


@unittest.skipUnless(shutil.which("redis-server"), "requires private local Redis, never auto installed")
class FullTravelHTTPIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await resource_fixture.RuntimeTests.asyncSetUp(self)
        # The reused resource fixture forbids all INET. This suite additionally
        # permits ONLY the explicitly configured loopback MySQL test port.
        self.network.stop()
        real_connect = socket.socket.connect
        def local_test_only(sock, address):
            if sock.family != socket.AF_UNIX and not (
                    sock.family == socket.AF_INET and address[0] == "127.0.0.1" and address[1] == 13306):
                raise AssertionError("non-test network forbidden")
            return real_connect(sock, address)
        self.network = patch.object(socket.socket, "connect", local_test_only)
        self.network.start()
        self.engine, cleanup = temporary_mysql()
        self.addCleanup(cleanup)
        anchor = self.path / "usage.identity"
        anchor.write_text(uuid4().hex + "\n", encoding="ascii")
        anchor.chmod(0o600)
        initialize(self.engine, anchor)
        self.upstream = FullControlledUpstream()
        self.app, _ = self.enterContext(controlled_app(self.engine, anchor, upstream=self.upstream))
        self.template_root = self.path / "templates"
        self.template_root.mkdir(mode=0o700)
        self.full_settings = Settings(_env_file=None, qwen_api_key="controlled-not-real",
            qwen_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            assistant_runtime="echomind_travel_full_v2", assistant_full_template_root=self.template_root,
            assistant_full_redis_socket=self.socket, assistant_full_chroma_path=self.path / "chroma",
            assistant_full_namespace="full-http-controlled", assistant_full_budget="collaboration")
        self.enterContext(patch.dict(self.app.dependency_overrides, {get_settings: lambda: self.full_settings}))
        self.api = await self.enterAsyncContext(httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
            base_url=ORIGIN, follow_redirects=False))
        self.headers, self.owner = await self.bootstrap(self.api)
        template_files(self.template_root, self.owner)
        created = await self.api.post("/chat/conversations", headers=self.headers,
            json={"operation_id": str(uuid4())})
        self.assertEqual(created.status_code, 200, created.text)
        self.view = created.json()
        self.workspace_version = str(uuid4())
        self.closed_resources = []
        original_close = CandidateResources.aclose
        async def observe_close(resource):
            await original_close(resource)
            self.closed_resources.append(resource)
        self.enterContext(patch.object(CandidateResources, "aclose", observe_close))

    async def asyncTearDown(self):
        await close_full_services()
        await resource_fixture.RuntimeTests.asyncTearDown(self)

    async def bootstrap(self, client):
        seed = await client.get("/workspace/session")
        response = await client.post("/workspace/session", json={},
            headers={"Origin": ORIGIN, "X-CSRF-Token": seed.json()["csrf_token"]})
        self.assertEqual(response.status_code, 200, response.text)
        view = response.json()
        return {"Origin": ORIGIN, "X-CSRF-Token": view["csrf_token"]}, view["user"]["id"]

    async def submit(self, message, *, operation=None, revision=None):
        return await self.api.post(f"/chat/conversations/{self.view['id']}/assistant-turns",
            headers=self.headers | {"X-TripPilot-Surface": "dialogue-v3"}, json={
                "operation_id": operation or str(uuid4()),
                "expected_revision": self.view["revision"] if revision is None else revision, "text": message,
                "dialogue_context": {"workspace_version": self.workspace_version, "display": "none", "items": []}})

    async def state(self, client=None):
        return await (client or self.api).get(f"/chat/conversations/{self.view['id']}/assistant-state",
            headers={"X-TripPilot-Surface": "dialogue-v3"})

    def attempt_rows(self):
        with self.engine.connect() as db:
            return db.execute(text("SELECT service,state FROM usage_attempts ORDER BY created_at,id")).fetchall()

    async def test_actual_full_admission_clarification_exact_receipt_and_no_duplicate_send(self):
        self.assertIsNone(self.view["requirements"]["start_date"])
        self.assertIsNone(self.view["requirements"]["end_date"])
        operation = str(uuid4())
        response = await self.submit("还没定日期，先问我", operation=operation)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("请说明出发和结束日期", response.json()["messages"][-1]["text"])
        self.assertNotIn(FABRICATED, response.text)
        state = await self.state()
        self.assertEqual(state.status_code, 200, state.text)
        data = state.json()
        self.assertEqual(data["schema_version"], "assistant-state-v2")
        self.assertEqual((data["task"]["model_calls"], data["task"]["tool_calls"]), (4, 1))
        receipt = data["task"]["execution_receipt"]
        self.assertEqual(receipt["dispatch"]["sent"], 4)
        self.assertEqual(receipt["dispatch"]["received"], 4)
        self.assertEqual(self.attempt_rows(), [("qwen_text", "received")] * 4)
        duplicate = await self.submit("还没定日期，先问我", operation=operation)
        self.assertEqual(duplicate.status_code, 409, duplicate.text)
        self.assertEqual(len(self.upstream.sent), 4)
        self.assertEqual(len(self.attempt_rows()), 4)
        self.assertEqual(len(self.closed_resources), 1)
        self.assertTrue(self.closed_resources[0]._closed)

    async def test_actual_parallel_composer_two_read_proposals_and_cross_owner_denial(self):
        self.upstream.intent = TravelIntent.SCHEDULE
        stages = []
        original_parallel, original_compose = AgentOrchestrator.run_parallel, ResponseComposer.compose_result
        async def parallel(runtime, req, decision):
            stages.append("original_parallel")
            return await original_parallel(runtime, req, decision)
        async def compose(composer, req, responses):
            stages.append("original_composer")
            return await original_compose(composer, req, responses)
        with patch.object(AgentOrchestrator, "run_parallel", parallel), patch.object(ResponseComposer, "compose_result", compose):
            response = await self.submit("查看当前行程和费用")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(stages, ["original_parallel", "original_composer"])
        self.assertNotIn(FABRICATED, response.text)
        state = (await self.state()).json()
        self.assertEqual((state["task"]["model_calls"], state["task"]["tool_calls"]), (6, 2))
        self.assertEqual(state["task"]["execution_receipt"]["dispatch"]["sent"], 6)
        self.assertEqual(self.attempt_rows(), [("qwen_text", "received")] * 6)
        receipt = await self.api.get(f"/chat/conversations/{self.view['id']}/dialogue-action",
            headers={"X-TripPilot-Surface": "dialogue-v3"})
        self.assertEqual(receipt.status_code, 200, receipt.text)
        projected = receipt.json()
        self.assertEqual(projected["workspace_version"], self.workspace_version)
        self.assertEqual({item["name"] for item in projected["actions"]}, {"read_schedule", "read_budget"})
        other = await self.enterAsyncContext(httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url=ORIGIN))
        await self.bootstrap(other)
        self.assertEqual((await self.state(other)).status_code, 404)
        self.assertEqual(len(self.upstream.sent), 6)

    async def test_missing_template_fails_before_turn_and_knowledge_answer_stays_disabled(self):
        (self.template_root / self.owner / "receipt.json").unlink()
        response = await self.submit("先问我日期")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["detail"]["code"], "assistant_full_runtime_not_ready")
        state = (await self.state()).json()
        self.assertIsNone(state["task"])
        self.assertEqual(state["revision"], self.view["revision"])
        answer = await self.api.post("/knowledge/answer", headers=self.headers, json={"enabled": True})
        self.assertEqual(answer.status_code, 503)
        self.assertEqual(answer.json()["detail"]["code"], "knowledge_answer_disabled")
        self.assertEqual(self.upstream.sent, [])
        self.assertEqual(self.attempt_rows(), [])

    async def test_sent_cancel_preserves_exact_attempts_no_action_and_drained_resources(self):
        self.upstream.hold_agent = True
        task = asyncio.create_task(self.submit("还没定日期，先问我"))
        await asyncio.wait_for(self.upstream.agent_entered.wait(), 5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        state = await self.state()
        self.assertEqual(state.status_code, 200, state.text)
        data = state.json()["task"]
        self.assertEqual(data["state"], "cancelled")
        self.assertEqual((data["model_calls"], data["tool_calls"]), (4, 0))
        self.assertEqual(data["execution_receipt"]["dispatch"]["sent"], 4)
        self.assertEqual(data["execution_receipt"]["dispatch"]["cancelled"], 1)
        rows = self.attempt_rows()
        self.assertEqual(len(rows), 4)
        self.assertEqual(sum(row.state == "cancelled" for row in rows), 1)
        self.assertTrue(all(row.service == "qwen_text" for row in rows))
        actions = await self.api.get(f"/chat/conversations/{self.view['id']}/dialogue-action",
            headers={"X-TripPilot-Surface": "dialogue-v3"})
        self.assertEqual(actions.status_code, 200, actions.text)
        self.assertEqual(actions.json()["actions"], [])
        self.assertEqual(len(self.closed_resources), 1)
        self.assertTrue(self.closed_resources[0]._closed)

    async def test_other_worker_version_change_rejects_a_late_native_success(self):
        self.upstream.hold_agent = True
        operation = str(uuid4())
        task = asyncio.create_task(self.submit("还没定日期，先问我", operation=operation))
        await asyncio.wait_for(self.upstream.agent_entered.wait(), 5)
        # Real authoritative change by a simulated second server worker. It
        # cannot access this process's _ACTIVE map, so the old native response
        # deliberately arrives and must lose the version check, not overwrite.
        changed = await asyncio.to_thread(ChatStore(self.engine).cancel_turn, self.owner, self.view["id"], operation)
        self.upstream.agent_release.set()
        response = await task
        self.assertEqual(response.status_code, 502, response.text)
        current = (await self.api.get(f"/chat/conversations/{self.view['id']}")).json()
        self.assertEqual(current["revision"], changed["revision"])
        self.assertEqual(current["last_turn"]["state"], "cancelled")
        self.assertFalse(any(row["role"] == "assistant" for row in current["messages"]))
        actions = await self.api.get(f"/chat/conversations/{self.view['id']}/dialogue-action",
            headers={"X-TripPilot-Surface": "dialogue-v3"})
        self.assertEqual(actions.status_code, 200, actions.text)
        self.assertEqual(actions.json()["actions"], [])
        self.assertEqual(len(self.upstream.sent), 4)
        self.assertEqual(len(self.attempt_rows()), 4)
        self.assertTrue(self.closed_resources[0]._closed)

    async def test_other_worker_cancels_during_memory_no_later_fusion_or_agent_is_sent(self):
        self.upstream.hold_memory = True
        operation = str(uuid4())
        task = asyncio.create_task(self.submit("还没定日期，先问我", operation=operation))
        await asyncio.wait_for(self.upstream.memory_entered.wait(), 5)
        changed = await asyncio.to_thread(ChatStore(self.engine).cancel_turn,
            self.owner, self.view["id"], operation)
        self.upstream.memory_release.set()
        response = await task
        self.assertEqual(response.status_code, 502, response.text)
        current = (await self.api.get(f"/chat/conversations/{self.view['id']}")).json()
        self.assertEqual(current["revision"], changed["revision"])
        self.assertEqual(current["last_turn"]["state"], "cancelled")
        self.assertFalse(any(row["role"] == "assistant" for row in current["messages"]))
        self.assertEqual(len(self.upstream.sent), 1)  # Already-sent memory only.
        self.assertTrue(self.upstream.sent[0]["path"].endswith("/embeddings"))
        self.assertEqual(self.attempt_rows(), [("qwen_text", "received")])
        state = (await self.state()).json()["task"]
        self.assertEqual(state["execution_receipt"]["dispatch"]["sent"], 1)
        self.assertEqual(state["execution_receipt"]["dispatch"]["received"], 1)
        self.assertEqual(state["tool_calls"], 0)
        self.assertTrue(self.closed_resources[0]._closed)


if __name__ == "__main__":
    unittest.main()

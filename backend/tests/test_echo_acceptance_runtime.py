"""Only local journals, real temporary product quotas and closed MockTransport."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zlib

import httpx

from agents.agent_orchestrator import AgentOrchestrator, AgentRequest
from agents.tools import ToolSpec
from core.config import Settings
from core.qwen_gateway import QwenGateway
from tools import echo_acceptance_runtime as runtime
from tests.usage_fixture import UsageFixture

ROUTE = "/chat/conversations/12345678-1234-4234-8234-123456789abc/assistant-turns"
URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
TOOLS = [{"type": "function", "function": {"name": "inspect_trip", "description": "受控只读",
          "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}}]


def payload(question="controlled api01"):
    return dict(runtime.PARAMETERS, messages=[{"role": "system", "content": "controlled system"},
                {"role": "user", "content": json.dumps({"message": question, "context": {}}, separators=(",", ":"))}], tools=TOOLS)


def native(*, identifier="call_1", arguments="{}", final=False):
    return {"id": "controlled-request", "model": runtime.MODEL,
            "choices": [{"finish_reason": "stop" if final else "tool_calls", "message": {"role": "assistant",
                "content": "受控公开建议" if final else None,
                **({} if final else {"tool_calls": [{"id": identifier, "type": "function", "function": {"name": "inspect_trip", "arguments": arguments}}]})}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10}}


class Harness:
    def __init__(self, root: Path, *, initial_payload: dict | None = None, continuation_sources: bool = False):
        self.repo = root / "repo"
        self.repo.mkdir()
        (self.repo / "frozen.py").write_text("controlled")
        old = {}
        for i in range(16):
            path = root / f"old-{i}.jsonl"
            path.write_text('{"event":"old-occupied"}\n')
            old[str(path)] = runtime.digest(path.read_bytes())
        descriptor = runtime.payload_descriptors(initial_payload or payload())
        files = {"frozen.py": runtime.digest((self.repo / "frozen.py").read_bytes())}
        if continuation_sources:
            for relative in runtime.CONTINUATION_FILES:
                path = self.repo / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("controlled original version")
                files[relative] = runtime.digest(path.read_bytes())
        self.protocol = {"schema_version": "echo-live-acceptance-v1", "authorization_id": runtime.AUTHORIZATION,
            "source_commit": "a" * 40, "source_files": files,
            "old_ledgers": old, "frozen_artifacts": {}, "parameters": runtime.PARAMETERS,
            "scopes": [{"id": name, "route": ROUTE, "text": "controlled " + name,
                        "max_model_calls": limit, "max_amap_calls": 2 if name == "chrome02" else 0,
                        "amap_paths": ["/v5/place/text"] if name == "chrome02" else [],
                        "system_sha256": descriptor["system_sha256"], "tools_sha256": descriptor["tools_sha256"]}
                       for name, limit in (("api01", 3), ("api02", 1), ("chrome01", 3), ("chrome02", 1))] + [
                        {"id": "business_poi" if continuation_sources else "poi", "route": "/places/search", "text": None, "max_model_calls": 0, "max_amap_calls": 2,
                         "amap_paths": ["/v5/place/text"], "amap_query_allowlist": [{"path": "/v5/place/text", "params": {"keywords": "外滩", "city": "上海"}}]},
                        {"id": "business_walking" if continuation_sources else "walking", "route": "/trips/schedule-preview", "text": None, "max_model_calls": 0, "max_amap_calls": 4,
                         "amap_paths": ["/v3/direction/walking"]}]}
        self.directory, self.claim = root / "batch", root / "authorization.json"
        runtime.freeze(self.directory, self.protocol, repository_root=self.repo, authorization_path=self.claim)
        self.gate = self.reopen()

    def reopen(self, continuation=None):
        return runtime.AcceptanceGate(self.directory, repository_root=self.repo, authorization_path=self.claim, continuation=continuation)


class GateIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.harness = Harness(self.root)

    def test_authorization_is_exclusive_and_cannot_be_reset_with_another_directory(self):
        with self.assertRaises(runtime.AcceptanceStopped):
            runtime.freeze(self.root / "another-batch", self.harness.protocol,
                           repository_root=self.harness.repo, authorization_path=self.harness.claim)
        self.assertFalse((self.root / "another-batch").exists())
        self.assertEqual(self.harness.gate.verify()["model_calls"], 0)

    def test_changed_source_or_old_ledger_is_rejected_without_updating_hash(self):
        original = (self.harness.directory / "protocol.json").read_bytes()
        (self.harness.repo / "frozen.py").write_text("changed")
        with self.assertRaises(runtime.AcceptanceStopped):
            self.harness.reopen()
        self.assertEqual((self.harness.directory / "protocol.json").read_bytes(), original)
        self.assertTrue((self.harness.directory / "paused.json").exists())

    def test_prior_ledger_hash_change_cannot_be_silently_rebased(self):
        old = Path(next(iter(self.harness.protocol["old_ledgers"])))
        old.write_text('{"event":"changed-old-count"}\n')
        with self.assertRaises(runtime.AcceptanceStopped):
            self.harness.gate.verify()

    def test_same_scope_cannot_be_used_again_even_if_zero_calls_were_needed(self):
        with self.harness.gate.scope("api01", "operation-1"):
            pass
        with self.assertRaises(runtime.AcceptanceStopped):
            with self.harness.reopen().scope("api01", "new-operation"):
                self.fail("must not bind a replacement")

    def test_uncertain_reserved_attempt_blocks_restart_and_stays_occupied(self):
        with self.harness.gate.scope("api01", "operation-1"):
            attempt, _, _ = self.harness.gate.reserve(httpx.Request("POST", URL, json=payload()))
            self.assertGreater(attempt, 0)
        with self.assertRaises(runtime.AcceptanceStopped):
            self.harness.reopen()
        rows = self.harness.gate._rows()
        self.assertEqual(sum(row["event"] == "attempt" for row in rows), 1)

    def test_truncated_journal_prefix_cannot_recover_allowance(self):
        with self.harness.gate.scope("api01", "operation-1"):
            pass
        path = self.harness.directory / "calls.jsonl"
        path.write_bytes(path.read_bytes().splitlines(keepends=True)[0])
        with self.assertRaises(runtime.AcceptanceStopped):
            self.harness.reopen()


class TransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.harness = Harness(self.root)
        self.usage = UsageFixture()
        self.addCleanup(self.usage.close)

    async def test_all_eight_preplanned_rounds_share_global_and_product_counts(self):
        observed = []
        def upstream(request):
            observed.append(request.content)
            # The attempt is already durable before this closed transport runs.
            self.assertEqual(sum(row["event"] == "attempt" for row in self.harness.gate._rows()), len(observed))
            return httpx.Response(200, json=native(identifier=f"call_{len(observed)}"))
        with self.usage.bind(ROUTE):
            async with runtime.GatedMeteredAsyncClient(gate=self.harness.gate, transport=httpx.MockTransport(upstream)) as client:
                for name, limit in (("api01", 3), ("api02", 1), ("chrome01", 3), ("chrome02", 1)):
                    body = payload("controlled " + name)
                    with self.harness.gate.scope(name, "operation-" + name, route=ROUTE, text="controlled " + name):
                        for index in range(limit):
                            await client.post(URL, json=body)
                            selected = self.harness.gate.active[name].last_response["tool_call"]
                            body["messages"].extend([{"role": "assistant", "content": None, "tool_calls": [selected]},
                                {"role": "tool", "tool_call_id": selected["id"], "content": json.dumps({"step": index})}])
        state = self.harness.reopen().verify()
        self.assertEqual(state["model_calls"], 8)
        self.assertEqual(state["remaining_model_calls"], 0)
        self.assertEqual(self.usage.store.own_usage("a" * 32)["services"]["qwen_text"]["used"], 8)
        self.assertEqual(len(observed), 8)

    async def test_network_failure_occupies_and_pauses_without_retry(self):
        sent = []
        def upstream(request):
            sent.append(request)
            raise httpx.ConnectError("controlled failure", request=request)
        with self.usage.bind(ROUTE):
            async with runtime.GatedMeteredAsyncClient(gate=self.harness.gate, transport=httpx.MockTransport(upstream)) as client:
                with self.assertRaises(httpx.ConnectError):
                    with self.harness.gate.scope("api01", "operation-1"):
                        await client.post(URL, json=payload())
        self.assertEqual(len(sent), 1)
        self.assertEqual(self.usage.store.own_usage("a" * 32)["services"]["qwen_text"]["used"], 1)
        with self.assertRaises(runtime.AcceptanceStopped):
            self.harness.reopen()

    async def test_bad_parameters_stop_before_product_or_upstream_dispatch(self):
        sent = []
        with self.usage.bind(ROUTE):
            async with runtime.GatedMeteredAsyncClient(gate=self.harness.gate, transport=httpx.MockTransport(
                    lambda request: sent.append(request) or httpx.Response(200))) as client:
                with self.assertRaises(runtime.AcceptanceStopped):
                    with self.harness.gate.scope("api01", "operation-1"):
                        await client.post(URL, json=payload() | {"max_tokens": 4096})
        self.assertEqual(sent, [])
        self.assertEqual(sum(row["event"] == "attempt" for row in self.harness.gate._rows()), 0)

    async def test_amap_query_allowlist_and_two_directions_do_not_capture_keys(self):
        observed = []
        two_entered = asyncio.Event()
        walking_entered = 0
        async def upstream(request):
            nonlocal walking_entered
            observed.append(request.url.path)
            if request.url.path == "/v3/direction/walking":
                walking_entered += 1
                if walking_entered == 2:
                    two_entered.set()
                await asyncio.wait_for(two_entered.wait(), .5)
            return httpx.Response(200, json={"status": "1"})
        with self.usage.bind("/places/search"):
            async with runtime.GatedMeteredAsyncClient(gate=self.harness.gate, transport=httpx.MockTransport(upstream)) as client:
                with self.harness.gate.scope("poi", "poi-operation"):
                    await client.get("https://restapi.amap.com/v5/place/text", params={"key": "PRIVATE_KEY", "keywords": "外滩", "city": "上海"})
                with self.harness.gate.scope("walking", "walking-operation"):
                    await asyncio.gather(*[client.get("https://restapi.amap.com/v3/direction/walking", params={"key": "PRIVATE_KEY", "origin": origin, "destination": destination})
                        for origin, destination in (("1,2", "3,4"), ("3,4", "1,2"))])
        self.assertEqual(self.harness.reopen().verify()["amap_physical_calls"], 3)
        self.assertEqual(walking_entered, 2)
        self.assertNotIn("PRIVATE_KEY", (self.harness.directory / "calls.jsonl").read_text())

    async def test_count_persistence_failure_blocks_transport_and_keeps_partial_evidence(self):
        sent = []
        with self.usage.bind(ROUTE):
            async with runtime.GatedMeteredAsyncClient(gate=self.harness.gate, transport=httpx.MockTransport(
                    lambda request: sent.append(request) or httpx.Response(200))) as client:
                with self.assertRaises((OSError, runtime.AcceptanceStopped)):
                    with self.harness.gate.scope("api01", "operation-1"):
                        with patch.object(runtime.os, "fsync", side_effect=OSError("controlled disk error")):
                            await client.post(URL, json=payload())
        self.assertEqual(sent, [])
        self.assertGreater(len(list((self.harness.directory / "events").iterdir())), 1)
        with self.assertRaises(runtime.AcceptanceStopped):
            self.harness.reopen()

    async def test_sent_cancellation_is_counted_once_and_cannot_resume(self):
        entered = asyncio.Event()
        class WaitingBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'{"choices":'
                entered.set()
                await asyncio.Event().wait()
        with self.usage.bind(ROUTE):
            async with runtime.GatedMeteredAsyncClient(gate=self.harness.gate, transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, stream=WaitingBody()))) as client:
                with self.harness.gate.scope("api01", "operation-1"):
                    task = asyncio.create_task(client.post(URL, json=payload()))
                    await entered.wait()
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
        self.assertEqual(sum(row["event"] == "attempt" for row in self.harness.gate._rows()), 1)
        self.assertEqual(self.usage.store.own_usage("a" * 32)["services"]["qwen_text"]["used"], 1)
        with self.assertRaises(runtime.AcceptanceStopped):
            self.harness.reopen()

    async def test_real_runtime_feedback_normalizes_whitespace_without_changing_arguments(self):
        async def handler(arguments):
            return {"message": "受控工具结果"}
        provider_request = AgentRequest("controlled api01", "a" * 32, "12345678-1234-4234-8234-123456789abc", "operation-1", {})
        # Capture exactly the production-generated first payload offline; no send.
        captured = []
        class CaptureProvider:
            async def complete(self, messages, tools, *, max_tokens):
                captured.append(dict(runtime.PARAMETERS, messages=messages, tools=tools))
                raise RuntimeError("offline_capture_complete")
        spec = ToolSpec("inspect_trip", "受控只读", TOOLS[0]["function"]["parameters"], handler)
        with self.assertRaises(RuntimeError):
            await AgentOrchestrator(CaptureProvider(), [spec]).run(provider_request)
        second_root = self.root / "runtime"
        second_root.mkdir()
        h = Harness(second_root, initial_payload=captured[0])
        seen = []
        def upstream(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200, json=native(arguments="{  }", final=len(seen) == 2))
        settings = Settings(_env_file=None, qwen_api_key="controlled-placeholder", qwen_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
        with self.usage.bind(ROUTE), h.gate.scope("api01", "operation-1"):
            async with runtime.GatedMeteredAsyncClient(gate=h.gate, transport=httpx.MockTransport(upstream)) as client:
                outcome = await AgentOrchestrator(QwenGateway(settings, client), [spec]).run(provider_request)
        self.assertEqual(outcome.trace["model_calls"], 2)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[1]["messages"][-2]["tool_calls"][0]["function"]["arguments"], "{}")
        self.assertEqual(h.reopen().verify()["model_calls"], 2)

    async def test_safe_projection_never_keeps_private_thinking_headers_or_invalid_arguments(self):
        raw = native()
        raw["headers"] = {"authorization": "PRIVATE_HEADER"}
        raw["choices"][0]["message"]["reasoning_content"] = "PRIVATE_THINKING"
        projected = runtime.safe_response(runtime.canonical(raw), payload())
        self.assertEqual(projected["output_tokens"], 10)
        encoded = json.dumps(projected)
        self.assertNotIn("PRIVATE_HEADER", encoded)
        self.assertNotIn("PRIVATE_THINKING", encoded)
        raw = native(arguments='{"unknown":"PRIVATE_ARGUMENT"}')
        self.assertNotIn("PRIVATE_ARGUMENT", json.dumps(runtime.safe_response(runtime.canonical(raw), payload())))

    async def test_gzip_deflate_projection_is_bounded_and_retains_index_zero_metadata(self):
        envelope = native()
        envelope["choices"][0]["message"]["tool_calls"][0]["index"] = 0
        raw = runtime.canonical(envelope)
        for encoding, compressed in (("gzip", gzip.compress(raw)), ("deflate", zlib.compress(raw))):
            projected = runtime.safe_response(compressed, payload(), content_encoding=encoding)
            self.assertEqual(projected["native_index"], 0)
            self.assertEqual(projected["request_id"], "controlled-request")
            self.assertEqual(projected["output_tokens"], 10)
            self.assertEqual(projected["decoded_body_bytes"], len(raw))
            self.assertEqual(projected["response_sha256"], runtime.digest(compressed))
        for compressed, encoding in ((gzip.compress(b"x" * 40000), "gzip"), (b"invalid gzip", "gzip"), (raw, "unsupported")):
            projected = runtime.safe_response(compressed, payload(), content_encoding=encoding)
            self.assertIn("validation_failure", projected)
            self.assertEqual(projected["diagnostic_error"], "compression_invalid_unsupported_or_over_limit")
            self.assertNotIn("content", projected)

    async def test_gzip_real_stream_projection_does_not_change_business_decoding(self):
        envelope = native()
        envelope["choices"][0]["message"]["tool_calls"][0]["index"] = 0
        compressed = gzip.compress(runtime.canonical(envelope))
        class GzipBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield compressed[:10]
                yield compressed[10:]
        with self.usage.bind(ROUTE), self.harness.gate.scope("api01", "operation-1"):
            async with runtime.GatedMeteredAsyncClient(gate=self.harness.gate, transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, stream=GzipBody(), headers={"content-encoding": "gzip"}))) as client:
                response = await client.post(URL, json=payload())
                self.assertEqual(response.json(), envelope)
        projection = next(row["projection"] for row in self.harness.gate._rows() if row["event"] == "result")
        self.assertEqual(projection["native_index"], 0)
        self.assertEqual(projection["content_encoding"], "gzip")


class ContinuationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.h = Harness(self.root, continuation_sources=True)
        self.usage = UsageFixture()
        self.addCleanup(self.usage.close)
        # One original HTTP-200 structural failure, not a new live call or a
        # repaired grade. Its exact protocol, journal and pause remain intact.
        with self.usage.bind(ROUTE), self.h.gate.scope("api01", "original-operation"):
            async with runtime.GatedMeteredAsyncClient(gate=self.h.gate, transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json={"choices": []}))) as client:
                await client.post(URL, json=payload())
        self.h.gate._pause("native_protocol_compatibility_review")
        self.prefix = (self.h.directory / "calls.jsonl").read_bytes()
        self.original_protocol = (self.h.directory / "protocol.json").read_bytes()
        self.original_pause = (self.h.directory / "paused.json").read_bytes()
        changed = ["backend/core/llm_utils.py", "backend/tools/echo_acceptance_runtime.py"]
        for relative in changed:
            (self.h.repo / relative).write_text("controlled repaired version")
        artifact = self.root / "continuation-server.py"
        artifact.write_text("# controlled new server entry, no credentials\n")
        self.manifest = {"schema_version": "echo-live-continuation-v1", "authorization_id": runtime.AUTHORIZATION,
            "original_protocol_sha256": runtime.digest(self.original_protocol),
            "ledger_prefix_sha256": runtime.digest(self.prefix), "ledger_prefix_bytes": len(self.prefix),
            "original_pause_sha256": runtime.digest(self.original_pause), "source_commit": "b" * 40,
            "source_files": {relative: runtime.digest((self.h.repo / relative).read_bytes()) for relative in self.h.protocol["source_files"]},
            "allowed_changed_files": changed, "allowed_scopes": sorted(runtime.CONTINUATION_SCOPES),
            "new_artifacts": {str(artifact): runtime.digest(artifact.read_bytes())},
            "change_description": "Only strict optional native index zero compatibility and bounded diagnostic decompression."}

    def create(self):
        return runtime.create_continuation(self.h.directory, self.manifest, repository_root=self.h.repo, authorization_path=self.h.claim)

    async def test_same_ledger_remaining_scope_and_new_rows_bound_to_repair_manifest(self):
        path = self.create()
        gate = self.h.reopen(path)
        self.assertEqual(gate.verify()["model_calls"], 1)
        self.assertEqual(gate.verify()["remaining_model_calls"], 7)
        with self.usage.bind(ROUTE), gate.scope("api02", "remaining-operation"):
            async with runtime.GatedMeteredAsyncClient(gate=gate, transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json=native(final=True)))) as client:
                await client.post(URL, json=payload("controlled api02"))
        self.assertEqual(self.h.reopen(path).verify()["model_calls"], 2)
        current = (self.h.directory / "calls.jsonl").read_bytes()
        self.assertEqual(current[:len(self.prefix)], self.prefix)
        self.assertEqual((self.h.directory / "protocol.json").read_bytes(), self.original_protocol)
        self.assertEqual((self.h.directory / "paused.json").read_bytes(), self.original_pause)
        added = [runtime.strict_json(line) for line in current[len(self.prefix):].splitlines()]
        self.assertTrue(all(row["continuation_sha256"] == runtime.digest(path.read_bytes()) for row in added))

    async def test_original_api01_unused_rounds_cannot_be_reassigned_or_resent(self):
        path = self.create()
        gate = self.h.reopen(path)
        with self.assertRaises(runtime.AcceptanceStopped):
            with gate.scope("api01", "new-attempt"):
                self.fail("original operation must stay burned")
        self.assertTrue((self.h.directory / "continuation-paused.json").exists())
        self.assertEqual((self.h.directory / "paused.json").read_bytes(), self.original_pause)

    async def test_no_continuation_overwrite_and_changed_version_or_new_artifact_blocks(self):
        path = self.create()
        with self.assertRaises(FileExistsError):
            self.create()
        Path(next(iter(self.manifest["new_artifacts"]))).write_text("changed entry")
        with self.assertRaises(runtime.AcceptanceStopped):
            self.h.reopen(path)

    async def test_wrong_prefix_or_nonallowlisted_change_never_creates_manifest(self):
        self.manifest["ledger_prefix_sha256"] = "0" * 64
        with self.assertRaises(runtime.AcceptanceStopped):
            self.create()
        self.assertFalse((self.h.directory / "continuation-v1.json").exists())
        self.manifest["ledger_prefix_sha256"] = runtime.digest(self.prefix)
        (self.h.repo / "frozen.py").write_text("unrelated change")
        self.manifest["source_files"]["frozen.py"] = runtime.digest((self.h.repo / "frozen.py").read_bytes())
        self.manifest["allowed_changed_files"].append("frozen.py")
        with self.assertRaises(runtime.AcceptanceStopped):
            self.create()

    async def test_uncertain_continuation_attempt_blocks_restart_without_new_allowance(self):
        path = self.create()
        gate = self.h.reopen(path)
        with gate.scope("api02", "remaining-operation"):
            gate.reserve(httpx.Request("POST", URL, json=payload("controlled api02")))
        with self.assertRaises(runtime.AcceptanceStopped):
            self.h.reopen(path)
        self.assertEqual(sum(row["event"] == "attempt" for row in gate._rows()), 2)
        self.assertEqual((self.h.directory / "paused.json").read_bytes(), self.original_pause)


if __name__ == "__main__":
    unittest.main()

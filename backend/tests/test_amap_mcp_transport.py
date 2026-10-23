"""Real isolated durable quota storage, controlled wire; no external requests."""
from __future__ import annotations

import asyncio
from contextlib import closing
import gzip
import io
import json
import logging
import os
import sqlite3
import threading
import unittest
from unittest.mock import patch

import httpx

from core.business_usage import DailyLimit, UsageError
from tests.usage_fixture import UsageFixture
from tooling.providers.amap_mcp_transport import (
    ENDPOINT, MAX_INPUT_BYTES, MAX_RESPONSE_BYTES, MCPTransportError,
    create_http_client, endpoint_for, quiet_sdk_logs,
)

KEY = "1a" * 16
PATH = "/chat/conversations/11111111-1111-4111-8111-111111111111/places"
ASSISTANT_PATH = PATH.removesuffix("places") + "assistant-turns"
INITIALIZE = {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
    "protocolVersion": "2025-11-25", "capabilities": {},
    "clientInfo": {"name": "TripPilot", "version": "test-v1"},
}}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
CALL = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
    "name": "maps_text_search", "arguments": {"keywords": "公园", "city": "310000", "citylimit": True},
}}


def response(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    if "id" not in payload:
        return httpx.Response(202)
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": {}})


async def controls(client: httpx.AsyncClient) -> None:
    for payload in (INITIALIZE, INITIALIZED, LIST):
        await client.post(endpoint_for(KEY), json=payload)


class AmapMCPTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.fixture = UsageFixture()
        self.addCleanup(self.fixture.close)

    def rows(self):
        with closing(sqlite3.connect(self.fixture.store.settings.business_usage_path)) as db:
            return db.execute("SELECT owner_id, service, state, dispatched_at FROM usage_attempts ORDER BY rowid").fetchall()

    async def test_only_actual_call_consumes_one_durable_unit_and_no_retry(self) -> None:
        sent = []
        def wire(request):
            sent.append(json.loads(request.content)["method"])
            return response(request)
        with self.fixture.bind(PATH):
            async with create_http_client(KEY, transport=httpx.MockTransport(wire)) as client:
                await controls(client)
                self.assertEqual(self.rows(), [])
                await client.post(endpoint_for(KEY), json=CALL)
                with self.assertRaises(MCPTransportError):
                    await client.post(endpoint_for(KEY), json=CALL)
        self.assertEqual(sent, ["initialize", "notifications/initialized", "tools/list", "tools/call"])
        self.assertEqual([row[1:3] for row in self.rows()], [("amap_poi", "received")])
        self.assertIsNotNone(self.rows()[0][3])

    async def test_owner_request_scope_and_store_binding_cannot_be_reused(self) -> None:
        with self.assertRaises(UsageError):
            create_http_client(KEY, transport=httpx.MockTransport(response))
        for path in ("/places/search", "/saved-trips", PATH + "/extra", PATH.replace("4111", "1111")):
            with self.fixture.bind(path), self.assertRaises(MCPTransportError):
                create_http_client(KEY, transport=httpx.MockTransport(response))
        sent = []
        with self.fixture.bind(ASSISTANT_PATH):
            async with create_http_client(KEY, transport=httpx.MockTransport(lambda request: sent.append(request) or response(request))) as client:
                with self.fixture.bind(ASSISTANT_PATH, "b" * 32), self.assertRaises(MCPTransportError):
                    await client.post(endpoint_for(KEY), json=INITIALIZE)
        self.assertEqual(sent, [])
        self.assertEqual(self.rows(), [])

    async def test_fixed_endpoint_no_redirects_auth_or_environment_proxy(self) -> None:
        for key in ("", "secret", "a" * 31, "g" * 32, "a" * 32 + "\n"):
            with self.assertRaises(MCPTransportError):
                endpoint_for(key)
        sent = []
        for url in ("http://mcp.amap.com/mcp?key=" + KEY, "https://example.test/mcp?key=" + KEY,
                    endpoint_for(KEY) + "&extra=1", ENDPOINT + "/?key=" + KEY,
                    "https://user:password@mcp.amap.com/mcp?key=" + KEY):
            with self.fixture.bind(PATH):
                async with create_http_client(KEY, transport=httpx.MockTransport(lambda request: sent.append(request) or response(request))) as client:
                    with self.assertRaises(MCPTransportError):
                        await client.post(url, json=INITIALIZE)
        with self.fixture.bind(PATH), patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:19876"}):
            async with create_http_client(KEY, transport=httpx.MockTransport(response)) as client:
                self.assertFalse(client.trust_env)
                self.assertEqual(client._mounts, {})
                with self.assertRaises(MCPTransportError):
                    await client.post(endpoint_for(KEY), json=INITIALIZE, follow_redirects=True)
                with self.assertRaises(MCPTransportError):
                    await client.post(endpoint_for(KEY), json=INITIALIZE, auth=("x", "y"))
        self.assertEqual(sent, [])

    async def test_get_event_stream_is_local_405_delete_and_arbitrary_rpc_rejected(self) -> None:
        sent = []
        with self.fixture.bind(PATH):
            async with create_http_client(KEY, transport=httpx.MockTransport(lambda request: sent.append(request) or response(request))) as client:
                result = await client.get(endpoint_for(KEY))
                self.assertEqual(result.status_code, 405)
                with self.assertRaises(MCPTransportError):
                    await client.delete(endpoint_for(KEY))
            async with create_http_client(KEY, transport=httpx.MockTransport(response)) as client:
                with self.assertRaises(MCPTransportError):
                    await client.post(endpoint_for(KEY), json={"jsonrpc": "2.0", "id": 0, "method": "tools/call"})
        self.assertEqual(sent, [])
        self.assertEqual(self.rows(), [])

    async def test_invalid_tool_arguments_unknown_fields_ids_and_json_fail_before_quota(self) -> None:
        bad = [CALL | {"id": 1}, CALL | {"other": True}]
        for arguments in ({"keywords": "公园", "city": "310000", "citylimit": False},
                          {"keywords": "公园", "city": "上海", "citylimit": True},
                          {"keywords": "x" * 81, "city": "310000", "citylimit": True},
                          {"keywords": " ", "city": "310000", "citylimit": True},
                          {"keywords": "x\nrole", "city": "310000", "citylimit": True},
                          {"keywords": "公园", "city": "310000", "citylimit": True, "url": "https://example.test"}):
            bad.append(CALL | {"params": {"name": "maps_text_search", "arguments": arguments}})
        bad.append(CALL | {"params": CALL["params"] | {"name": "maps_direction_transit_integrated"}})
        sent = []
        for payload in bad:
            with self.fixture.bind(PATH):
                async with create_http_client(KEY, transport=httpx.MockTransport(lambda request: sent.append(request) or response(request))) as client:
                    await controls(client)
                    with self.assertRaises(MCPTransportError):
                        await client.post(endpoint_for(KEY), json=payload)
        for body in (b'{"jsonrpc":"2.0","id":2,"id":3,"method":"tools/call"}',
                     b'{"jsonrpc":"2.0","id":NaN,"method":"tools/call"}', b'{'):
            with self.fixture.bind(PATH):
                async with create_http_client(KEY, transport=httpx.MockTransport(response)) as client:
                    await controls(client)
                    with self.assertRaises(MCPTransportError):
                        await client.post(endpoint_for(KEY), content=body, headers={"content-type": "application/json"})
        self.assertEqual(len(sent), 3 * len(bad))
        self.assertEqual(self.rows(), [])

    async def test_input_and_response_incremental_limits(self) -> None:
        class Chunks(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"a" * (MAX_RESPONSE_BYTES // 2)
                yield b"b" * (MAX_RESPONSE_BYTES // 2 + 1)
        with self.fixture.bind(PATH):
            async with create_http_client(KEY, transport=httpx.MockTransport(response)) as client:
                with self.assertRaises(MCPTransportError) as error:
                    await client.post(endpoint_for(KEY), content=b"a" * (MAX_INPUT_BYTES + 1), headers={"content-type": "application/json"})
                self.assertEqual(error.exception.code, "mcp_input_limit")
        for upstream in (lambda _: httpx.Response(200, headers={"content-length": str(MAX_RESPONSE_BYTES + 1)}),
                         lambda _: httpx.Response(200, stream=Chunks())):
            with self.fixture.bind(PATH):
                async with create_http_client(KEY, transport=httpx.MockTransport(lambda request: upstream(request) if json.loads(request.content)["method"] == "tools/call" else response(request))) as client:
                    await controls(client)
                    with self.assertRaises(MCPTransportError) as error:
                        await client.post(endpoint_for(KEY), json=CALL)
                    self.assertEqual(error.exception.code, "mcp_response_limit")
        self.assertEqual([row[2] for row in self.rows()], ["failed", "failed"])

    async def test_complete_sse_is_accepted_incomplete_resumable_and_wrong_id_rejected(self) -> None:
        good = 'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{}}\n\n'
        cases = [(good, True), (good.replace("\n", "\r\n"), True),
                 (good.rstrip("\n"), False),
                 ('id: event1\n' + good, False), ('retry: 1\n' + good, False),
                 ('event: ping\ndata: {}\n\n', False), ('data: {}\n\n', False),
                 (good + good, False), (good.replace('"id":2', '"id":3'), False),
                 ('event: message\n\n', False), ('data: {"jsonrpc":"2.0","id":2,\n\n', False)]
        for body, accepted in cases:
            with self.fixture.bind(PATH):
                async with create_http_client(KEY, transport=httpx.MockTransport(
                        lambda request: httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
                        if json.loads(request.content)["method"] == "tools/call" else response(request))) as client:
                    await controls(client)
                    if accepted:
                        result = await client.post(endpoint_for(KEY), json=CALL)
                        self.assertEqual(result.text, body)
                    else:
                        with self.assertRaises(MCPTransportError):
                            await client.post(endpoint_for(KEY), json=CALL)
        # Received is a transport fact, not a claim that an MCP answer passed.
        self.assertEqual([row[2] for row in self.rows()], ["received"] * len(cases))

    async def test_duplicate_or_nonfinite_response_and_redirect_are_rejected_without_followup(self) -> None:
        for body in (b'{"jsonrpc":"2.0","id":2,"result":{},"result":{}}',
                     b'{"jsonrpc":"2.0","id":2,"result":{"x":NaN}}',
                     b'{"jsonrpc":"2.0","id":true,"result":{}}'):
            with self.fixture.bind(PATH):
                async with create_http_client(KEY, transport=httpx.MockTransport(lambda request:
                        httpx.Response(200, content=body, headers={"content-type": "application/json"})
                        if json.loads(request.content)["method"] == "tools/call" else response(request))) as client:
                    await controls(client)
                    with self.assertRaises(MCPTransportError):
                        await client.post(endpoint_for(KEY), json=CALL)
        sent = []
        with self.fixture.bind(PATH):
            async with create_http_client(KEY, transport=httpx.MockTransport(lambda request:
                    sent.append(request) or httpx.Response(302, headers={"location": "https://example.test/"}))) as client:
                with self.assertRaises(MCPTransportError) as error:
                    await client.post(endpoint_for(KEY), json=INITIALIZE)
        self.assertEqual(error.exception.code, "mcp_redirect")
        self.assertEqual(len(sent), 1)

    async def test_connection_timeout_and_cancel_after_send_consume_once_no_retry(self) -> None:
        for exception_type in (httpx.ConnectError, httpx.ReadTimeout):
            sent = []
            def wire(request):
                if json.loads(request.content)["method"] == "tools/call":
                    sent.append(request)
                    raise exception_type("controlled", request=request)
                return response(request)
            with self.fixture.bind(PATH):
                async with create_http_client(KEY, transport=httpx.MockTransport(wire)) as client:
                    await controls(client)
                    with self.assertRaises(exception_type):
                        await client.post(endpoint_for(KEY), json=CALL)
            self.assertEqual(len(sent), 1)
        entered = asyncio.Event()
        async def blocked(request):
            if json.loads(request.content)["method"] == "tools/call":
                entered.set()
                await asyncio.Future()
            return response(request)
        with self.fixture.bind(PATH):
            async with create_http_client(KEY, transport=httpx.MockTransport(blocked)) as client:
                await controls(client)
                task = asyncio.create_task(client.post(endpoint_for(KEY), json=CALL))
                await entered.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                with self.assertRaises(MCPTransportError):
                    await client.post(endpoint_for(KEY), json=CALL)
                self.assertTrue(client.mcp_failed.is_set())
        self.assertEqual([row[2] for row in self.rows()], ["failed", "failed", "cancelled"])

    async def test_persistent_quota_concurrency_and_counter_fault_prevent_wire(self) -> None:
        self.fixture.store.settings.business_daily_limits["amap_poi"] = DailyLimit(account=1, **{"global": 1})
        sent = []
        def wire(request):
            if json.loads(request.content)["method"] == "tools/call":
                sent.append(request)
            return response(request)
        with self.fixture.bind(PATH):
            async with create_http_client(KEY, transport=httpx.MockTransport(wire)) as first, create_http_client(KEY, transport=httpx.MockTransport(wire)) as second:
                await asyncio.gather(controls(first), controls(second))
                values = await asyncio.gather(first.post(endpoint_for(KEY), json=CALL), second.post(endpoint_for(KEY), json=CALL), return_exceptions=True)
        self.assertEqual(sum(isinstance(value, UsageError) for value in values), 1)
        self.assertEqual(len(sent), 1)
        # A new client/operation does not reset persistent quota.
        with self.fixture.bind(PATH):
            async with create_http_client(KEY, transport=httpx.MockTransport(wire)) as client:
                await controls(client)
                with self.assertRaises(UsageError):
                    await client.post(endpoint_for(KEY), json=CALL)
        self.assertEqual(len(sent), 1)
        self.assertEqual(len(self.rows()), 1)

    async def test_reservation_cancel_drains_worker_and_does_not_dispatch(self) -> None:
        entered, release = threading.Event(), threading.Event()
        reserve = self.fixture.store.reserve
        def blocked(owner, service):
            entered.set()
            release.wait(3)
            return reserve(owner, service)
        sent = []
        def wire(request):
            if json.loads(request.content)["method"] == "tools/call":
                sent.append(request)
            return response(request)
        with self.fixture.bind(PATH), patch.object(self.fixture.store, "reserve", blocked):
            async with create_http_client(KEY, transport=httpx.MockTransport(wire)) as client:
                await controls(client)
                task = asyncio.create_task(client.post(endpoint_for(KEY), json=CALL))
                while not entered.is_set():
                    await asyncio.sleep(.001)
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertEqual(sent, [])
        self.assertEqual([row[2:] for row in self.rows()], [("reserved", None)])

    async def test_global_persistent_quota_is_shared_by_two_distinct_owners(self) -> None:
        self.fixture.store.settings.business_daily_limits["amap_poi"] = DailyLimit(account=5, **{"global": 1})
        sent = []
        def wire(request):
            if json.loads(request.content)["method"] == "tools/call":
                sent.append(request)
            return response(request)
        async def operate(owner):
            with self.fixture.bind(PATH, owner):
                async with create_http_client(KEY, transport=httpx.MockTransport(wire)) as client:
                    await controls(client)
                    return await client.post(endpoint_for(KEY), json=CALL)
        results = await asyncio.gather(operate("a" * 32), operate("b" * 32), return_exceptions=True)
        errors = [result for result in results if isinstance(result, UsageError)]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].scope, "global")
        self.assertEqual(len(sent), 1)
        self.assertEqual(len(self.rows()), 1)
        self.assertIn(self.rows()[0][0], {"a" * 32, "b" * 32})

    async def test_counter_failure_blocks_before_wire(self) -> None:
        sent = []
        with self.fixture.bind(PATH):
            async with create_http_client(KEY, transport=httpx.MockTransport(lambda request: sent.append(request) or response(request))) as client:
                await controls(client)
                with patch.object(self.fixture.store, "reserve", side_effect=UsageError()), self.assertRaises(UsageError):
                    await client.post(endpoint_for(KEY), json=CALL)
        self.assertEqual(len(sent), 3)
        self.assertEqual(self.rows(), [])

    async def test_safe_failure_latch_preserves_quota_error_not_raw_network_details(self) -> None:
        quota = UsageError("business_quota_exhausted", service="amap_poi", scope="account")
        with self.fixture.bind(PATH):
            async with create_http_client(KEY, transport=httpx.MockTransport(response)) as client:
                await controls(client)
                self.assertFalse(client.mcp_failed.is_set())
                waiter = asyncio.create_task(client.mcp_failed.wait())
                with patch.object(self.fixture.store, "reserve", side_effect=quota), self.assertRaises(UsageError):
                    await client.post(endpoint_for(KEY), json=CALL)
                self.assertTrue(await asyncio.wait_for(waiter, .5))
                self.assertIs(client.mcp_failure, quota)
                with self.assertRaises(MCPTransportError):
                    await client.post(endpoint_for(KEY), json=CALL)
                self.assertIs(client.mcp_failure, quota)
        def network(request):
            if json.loads(request.content)["method"] == "tools/call":
                raise httpx.ConnectError("private RPC and key " + KEY, request=request)
            return response(request)
        with self.fixture.bind(PATH):
            async with create_http_client(KEY, transport=httpx.MockTransport(network)) as client:
                await controls(client)
                with self.assertRaises(httpx.ConnectError):
                    await client.post(endpoint_for(KEY), json=CALL)
                self.assertIsInstance(client.mcp_failure, MCPTransportError)
                self.assertEqual(str(client.mcp_failure), "mcp_transport")
                self.assertNotIn(KEY, str(client.mcp_failure))

    async def test_response_session_headers_and_decompression_are_bounded(self) -> None:
        compressed = gzip.compress(b"x" * (MAX_RESPONSE_BYTES + 1))
        class Compressed(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield compressed
        for produce in (
            lambda _: httpx.Response(200, json={"jsonrpc": "2.0", "id": 2, "result": {}},
                                    headers={"mcp-session-id": "x" * 129}),
            lambda _: httpx.Response(200, json={"jsonrpc": "2.0", "id": 2, "result": {}},
                                    headers={"mcp-protocol-version": "x" * 33}),
            lambda _: httpx.Response(200, stream=Compressed(), headers={
                "content-encoding": "gzip", "content-type": "application/json", "content-length": str(len(compressed))}),
        ):
            with self.fixture.bind(PATH):
                async with create_http_client(KEY, transport=httpx.MockTransport(lambda request:
                        produce(request) if json.loads(request.content)["method"] == "tools/call" else response(request))) as client:
                    await controls(client)
                    with self.assertRaises(MCPTransportError):
                        await client.post(endpoint_for(KEY), json=CALL)
        self.assertEqual([row[2] for row in self.rows()], ["failed"] * 3)

    async def test_serialized_control_wire_and_single_tool_call_even_under_race(self) -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        methods = []
        async def wire(request):
            method = json.loads(request.content)["method"]
            methods.append(method)
            if method == "initialize":
                entered.set()
                await release.wait()
            return response(request)
        with self.fixture.bind(PATH):
            async with create_http_client(KEY, transport=httpx.MockTransport(wire)) as client:
                initial = asyncio.create_task(client.post(endpoint_for(KEY), json=INITIALIZE))
                await entered.wait()
                notification = asyncio.create_task(client.post(endpoint_for(KEY), json=INITIALIZED))
                await asyncio.sleep(0)
                self.assertEqual(methods, ["initialize"])
                release.set()
                await asyncio.gather(initial, notification)
                await client.post(endpoint_for(KEY), json=LIST)
                results = await asyncio.gather(client.post(endpoint_for(KEY), json=CALL),
                                               client.post(endpoint_for(KEY), json=CALL), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, MCPTransportError) for result in results), 1)
        self.assertEqual(methods, ["initialize", "notifications/initialized", "tools/list", "tools/call"])
        self.assertEqual(len(self.rows()), 1)

    async def test_repeated_cancel_waits_for_response_close_before_returning(self) -> None:
        body_entered, entered, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        closed = []
        class Closing(httpx.AsyncByteStream):
            async def __aiter__(self):
                body_entered.set()
                yield b"prefix"
                await asyncio.Future()
            async def aclose(self):
                entered.set()
                await release.wait()
                closed.append(True)
        def wire(request):
            if json.loads(request.content)["method"] == "tools/call":
                return httpx.Response(200, stream=Closing())
            return response(request)
        with self.fixture.bind(PATH):
            async with create_http_client(KEY, transport=httpx.MockTransport(wire)) as client:
                await controls(client)
                task = asyncio.create_task(client.post(endpoint_for(KEY), json=CALL))
                await asyncio.wait_for(body_entered.wait(), 2)
                task.cancel()
                await asyncio.wait_for(entered.wait(), 2)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertEqual(closed, [True])
        self.assertEqual(self.rows()[0][2], "cancelled")

    async def test_sdk_and_http_debug_logs_do_not_expose_key_rpc_or_session(self) -> None:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        root = logging.getLogger()
        level = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        try:
            quiet_sdk_logs()
            for name in ("mcp.client.streamable_http", "mcp.client.session", "mcp.shared.session", "client"):
                logging.getLogger(name).debug("private-key %s private-RPC private-session", KEY)
            with self.fixture.bind(PATH):
                async with create_http_client(KEY, transport=httpx.MockTransport(response)) as client:
                    await controls(client)
            logging.getLogger("trippilot.test.safe").warning("safe-application-event")
        finally:
            root.setLevel(level)
            root.removeHandler(handler)
        output = stream.getvalue()
        self.assertNotIn(KEY, output)
        self.assertNotIn("private-RPC", output)
        self.assertNotIn("private-session", output)
        self.assertIn("safe-application-event", output)

    async def test_business_version_hook_does_not_run_on_controls_and_denial_precedes_quota(self) -> None:
        checked, sent = [], []
        async def current():
            checked.append(True)
            raise ValueError("private revision and content " + KEY)
        def wire(request):
            sent.append(json.loads(request.content)["method"])
            return response(request)
        with self.fixture.bind(PATH):
            async with create_http_client(KEY, transport=httpx.MockTransport(wire), before_business=current) as client:
                await controls(client)
                self.assertEqual(checked, [])
                with self.assertRaises(MCPTransportError) as error:
                    await client.post(endpoint_for(KEY), json=CALL)
                self.assertEqual(error.exception.code, "mcp_context")
                self.assertNotIn(KEY, str(error.exception))
                self.assertTrue(client.mcp_failed.is_set())
        self.assertEqual(len(checked), 1)
        self.assertEqual(sent, ["initialize", "notifications/initialized", "tools/list"])
        self.assertEqual(self.rows(), [])

    async def test_version_change_during_reservation_blocks_wire_without_refunding_attempt(self) -> None:
        state = {"valid": True}
        checked, sent = [], []
        reserve = self.fixture.store.reserve
        def changed(owner, service):
            identifier = reserve(owner, service)
            state["valid"] = False
            return identifier
        async def current():
            checked.append(state["valid"])
            if not state["valid"]:
                raise RuntimeError("private obsolete input")
        def wire(request):
            sent.append(json.loads(request.content)["method"])
            return response(request)
        with self.fixture.bind(PATH), patch.object(self.fixture.store, "reserve", changed):
            async with create_http_client(KEY, transport=httpx.MockTransport(wire), before_business=current) as client:
                await controls(client)
                with self.assertRaises(MCPTransportError) as error:
                    await client.post(endpoint_for(KEY), json=CALL)
                self.assertEqual(error.exception.code, "mcp_context")
                self.assertEqual(client.mcp_failure.code, "mcp_context")
        self.assertEqual(checked, [True, False])
        self.assertEqual(sent, ["initialize", "notifications/initialized", "tools/list"])
        self.assertEqual([row[1:3] for row in self.rows()], [("amap_poi", "failed")])
        self.assertIsNotNone(self.rows()[0][3])

    async def test_trusted_business_hook_preserves_safe_usage_error_identity(self) -> None:
        quota = UsageError("business_quota_exhausted", service="amap_poi", scope="global")
        async def current():
            raise quota
        with self.fixture.bind(PATH):
            async with create_http_client(KEY, transport=httpx.MockTransport(response), before_business=current) as client:
                await controls(client)
                with self.assertRaises(UsageError) as error:
                    await client.post(endpoint_for(KEY), json=CALL)
                self.assertIs(error.exception, quota)
                self.assertIs(client.mcp_failure, quota)
        self.assertEqual(self.rows(), [])


if __name__ == "__main__":
    unittest.main()

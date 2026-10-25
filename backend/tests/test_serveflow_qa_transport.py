"""No external I/O: the live guard is exercised with an in-memory transport."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.serveflow_qa_transport import MODEL, PhysicalSendGuard, project_response, verify_sources


class Ledger:
    def __init__(self):
        self.events = []

    def verify(self, **kwargs):
        self.events.append("verify")

    def claim(self, **kwargs):
        self.events.append("claim")
        return "attempt-01"

    def settle(self, identity, **kwargs):
        self.events.append(("settle", kwargs))


def setup_guard(tmp_path):
    source = tmp_path / "source.py"
    source.write_text("fixed")
    ledger = Ledger()
    guard = PhysicalSendGuard(ledger=ledger, binding={"source_hashes": {
        "source.py": hashlib.sha256(b"fixed").hexdigest()}}, root=tmp_path,
        official_host="dashscope.aliyuncs.com", evidence=tmp_path)
    request = httpx.Request("POST", "https://dashscope.aliyuncs.com/apps/anthropic/v1/messages",
                            json={"model": MODEL, "messages": []})
    return ledger, guard, request


def test_before_send_and_safe_projection(tmp_path):
    ledger, guard, request = setup_guard(tmp_path)
    async def upstream(_, req):
        assert ledger.events == ["verify", "claim"]
        return httpx.Response(200, json={"model": MODEL, "content": [
            {"type": "thinking", "thinking": "never store this"},
            {"type": "text", "text": "test reply"}], "usage": {"input_tokens": 9, "output_tokens": 2}}, request=req)
    asyncio.run(guard.send(upstream, object(), request))
    assert ledger.events[-1][1]["state"] == "received"
    data = (tmp_path / "attempt-01.json").read_text()
    assert "never store this" not in data
    assert "test reply" in data
    assert "messages" not in data


@pytest.mark.parametrize("url", ["https://evil.test/apps/anthropic/v1/messages",
    "http://dashscope.aliyuncs.com/apps/anthropic/v1/messages",
    "https://dashscope.aliyuncs.com/v1/messages",
    "https://dashscope.aliyuncs.com/apps/anthropic/v1/messages?query=x"])
def test_unplanned_external_never_claims(tmp_path, url):
    ledger, guard, request = setup_guard(tmp_path)
    request = httpx.Request("POST", url, json={"model": MODEL})
    async def upstream(*args):
        pytest.fail("must not send")
    with pytest.raises(RuntimeError, match="unplanned"):
        asyncio.run(guard.send(upstream, object(), request))
    assert ledger.events == []


def test_version_changes_never_claim(tmp_path):
    ledger, guard, request = setup_guard(tmp_path)
    (tmp_path / "source.py").write_text("changed")
    async def upstream(*args):
        pytest.fail("must not send")
    with pytest.raises(RuntimeError, match="version_mismatch"):
        asyncio.run(guard.send(upstream, object(), request))
    assert ledger.events == []


@pytest.mark.parametrize("exception", [httpx.ConnectError("private arbitrary exception"), asyncio.CancelledError()])
def test_uncertain_dispatch_stays_spent(tmp_path, exception):
    ledger, guard, request = setup_guard(tmp_path)
    async def upstream(*args):
        raise exception
    with pytest.raises(type(exception)):
        asyncio.run(guard.send(upstream, object(), request))
    assert ledger.events[:2] == ["verify", "claim"]
    assert ledger.events[-1][1]["state"] == "unknown"
    assert "private" not in str(ledger.events)


@pytest.mark.parametrize("status", [400, 401, 404, 429, 500])
def test_auth_failure_pauses_without_storing_error_body(tmp_path, status):
    ledger, guard, request = setup_guard(tmp_path)
    async def upstream(_, req):
        return httpx.Response(status, json={"error": "private key content"}, request=req)
    asyncio.run(guard.send(upstream, object(), request))
    assert ledger.events[-1][1]["state"] == "failed"
    assert "private key" not in (tmp_path / "attempt-01.json").read_text()


def test_evidence_write_failure_pauses_before_fallback(tmp_path, monkeypatch):
    ledger, guard, request = setup_guard(tmp_path)
    ledger.pause = lambda code: ledger.events.append(("pause", code))
    def fail(*args):
        raise OSError("private disk error")
    monkeypatch.setattr("tools.serveflow_qa_transport.private_json", fail)
    async def upstream(_, req):
        return httpx.Response(200, json={"content": []}, request=req)
    with pytest.raises(OSError):
        asyncio.run(guard.send(upstream, object(), request))
    assert ledger.events[-1] == ("pause", "storage_failure")


def test_projection_keeps_native_tools_without_thinking_or_unknown_fields():
    projected = project_response({"content": [{"type": "tool_use", "id": "toolu_demo",
        "name": "read_demo_order", "input": {"order_id": "DEMO-1001"}, "secret": "discard"}],
        "headers": {"authorization": "discard"}})
    assert projected["blocks"] == [{"type": "tool_use", "id": "toolu_demo", "name": "read_demo_order",
                                     "input": {"order_id": "DEMO-1001"}}]
    assert "discard" not in json.dumps(projected)

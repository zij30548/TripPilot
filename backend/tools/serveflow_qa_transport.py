"""Opt-in test-only physical-send guard for the four-case ServeFlow QA batch.

Not imported by the product. The caller supplies a separately authorized frozen
protocol, a private persistent Ledger and its exact official Beijing host. All
SDK clients in this isolated process must have max_retries=0. No credentials or
request bodies are recorded. Text/tool projections are synthetic test outputs;
thinking blocks and arbitrary response fields are deliberately omitted.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import time

MODEL = "qwen3.7-flash-2026-07-15"
PHASES = {
    "_llm_recognize": "intent",
    "_rewrite_query": "retrieval_rewrite",
    "_rerank": "retrieval_rerank",
    "_update_profile": "memory_profile",
    "_summarize": "memory_summary",
    "_merge_summary": "memory_merge",
    "compose": "composer",
    "_call_llm": "agent",
}


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sources(root: Path, source_hashes: dict[str, str]) -> None:
    for name, expected in source_hashes.items():
        relative = Path(name)
        if (relative.is_absolute() or ".." in relative.parts
                or not re.fullmatch(r"[a-f0-9]{64}", expected)
                or (root / relative).is_symlink()
                or fingerprint(root / relative) != expected):
            raise RuntimeError("qa_source_version_mismatch")


def private_json(path: Path, value: dict) -> None:
    """Exclusive creation: never overwrite an earlier observation."""
    data = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def project_response(data: dict) -> dict:
    result = {"model": "unknown", "stop_reason": None, "blocks": [], "usage": {}}
    model = data.get("model")
    if isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", model):
        result["model"] = model
    if data.get("stop_reason") in {"end_turn", "tool_use", "max_tokens", "stop_sequence"}:
        result["stop_reason"] = data["stop_reason"]
    usage = data.get("usage")
    if type(usage) is dict:
        for key in ("input_tokens", "output_tokens"):
            item = usage.get(key)
            result["usage"][key] = item if type(item) is int and 0 <= item <= 10_000_000 else None
    content = data.get("content")
    if type(content) is list:
        for block in content[:32]:
            if type(block) is not dict:
                continue
            if block.get("type") == "text" and type(block.get("text")) is str:
                result["blocks"].append({"type": "text", "text": block["text"][:16000]})
            elif block.get("type") == "tool_use":
                name, identity, args = block.get("name"), block.get("id"), block.get("input")
                safe = {"type": "tool_use", "name": "unrecognized", "id": "invalid", "input": None}
                if isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_]{1,100}", name):
                    safe["name"] = name
                if isinstance(identity, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,200}", identity):
                    safe["id"] = identity
                # Only fixed synthetic order/search fields are evidence here.
                if type(args) is dict and set(args) <= {"order_id", "query", "top_k", "keyword"}:
                    if all((type(v) is str and len(v) <= 2000) or (type(v) is int and 0 <= v <= 20)
                           for v in args.values()):
                        safe["input"] = args
                result["blocks"].append(safe)
    return result


class PhysicalSendGuard:
    def __init__(self, *, ledger, binding: dict, root: Path, official_host: str,
                 evidence: Path):
        if not (official_host == "dashscope.aliyuncs.com" or re.fullmatch(
                r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.cn-beijing\.maas\.aliyuncs\.com", official_host)):
            raise ValueError("qa_official_host_required")
        self.ledger, self.binding, self.root = ledger, binding, root
        self.host, self.evidence = official_host, evidence

    async def send(self, original, transport, request):
        url = request.url
        if url.host in {"localhost", "127.0.0.1", "::1"}:
            return await original(transport, request)
        if (request.method != "POST" or url.scheme != "https" or url.host != self.host
                or url.port not in {None, 443} or url.path != "/apps/anthropic/v1/messages"
                or url.query or url.fragment or url.username or url.password):
            raise RuntimeError("qa_unplanned_external_request")
        verify_sources(self.root, self.binding["source_hashes"])
        self.ledger.verify(binding=self.binding)
        body = request.content
        if len(body) > 512 * 1024:
            raise RuntimeError("qa_payload_limit")
        payload = json.loads(body)
        if payload.get("model") != MODEL or payload.get("stream", False) is not False:
            raise RuntimeError("qa_fixed_model_required")
        attempt = self.ledger.claim(payload_sha256=hashlib.sha256(body).hexdigest(), model=MODEL)
        names = {frame.function for frame in inspect.stack(context=0)}
        phase = next((value for key, value in PHASES.items() if key in names), "unclassified")
        start = time.monotonic()
        try:
            response = await original(transport, request)
            raw = await response.aread()
            if len(raw) > 2 * 1024 * 1024:
                raise RuntimeError("qa_response_limit")
        except BaseException as exc:
            code = "cancelled" if type(exc).__name__ == "CancelledError" else "transport_error"
            self.ledger.settle(attempt, state="unknown", metadata={
                "duration_ms": min(120000, int((time.monotonic() - start) * 1000)),
                "error_code": code,
            })
            raise
        metadata = {
            "status_code": response.status_code,
            "duration_ms": min(120000, int((time.monotonic() - start) * 1000)),
            "response_sha256": hashlib.sha256(raw).hexdigest(),
            "request_model": MODEL,
            "upstream_model": "unknown", "input_tokens": None, "output_tokens": None,
        }
        projected = {}
        if response.status_code == 200:
            try:
                projected = project_response(json.loads(raw))
                metadata.update(projected["usage"])
                metadata["upstream_model"] = projected["model"]
            except (ValueError, TypeError, AttributeError):
                projected = {"parse_error": True}
        request_id = response.headers.get("request-id") or response.headers.get("x-request-id")
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,150}", request_id):
            request_id = "unknown"
        try:
            private_json(self.evidence / f"{attempt}.json", {
                "attempt": attempt, "phase": phase, "payload_sha256": hashlib.sha256(body).hexdigest(),
                "request_model": MODEL, "provider_request_id": request_id,
                "metadata": metadata, "response_projection": projected,
            })
        except BaseException:
            self.ledger.pause("storage_failure")
            raise
        if response.status_code != 200:
            metadata["error_code"] = "upstream_error"
            self.ledger.settle(attempt, state="failed", metadata=metadata)
        else:
            self.ledger.settle(attempt, state="received", metadata=metadata)
        return response


def install(guard: PhysicalSendGuard):
    """Test process only; transport identity prevents accidental second wrapping."""
    import anthropic
    import httpx
    if getattr(httpx.AsyncHTTPTransport, "_serveflow_qa_guard", False):
        raise RuntimeError("qa_guard_already_installed")
    original_init = anthropic.AsyncAnthropic.__init__
    original_send = httpx.AsyncHTTPTransport.handle_async_request

    def no_retry_init(self, *args, **kwargs):
        kwargs["max_retries"] = 0
        original_init(self, *args, **kwargs)

    async def guarded(self, request):
        return await guard.send(original_send, self, request)

    anthropic.AsyncAnthropic.__init__ = no_retry_init
    httpx.AsyncHTTPTransport.handle_async_request = guarded
    httpx.AsyncHTTPTransport._serveflow_qa_guard = True

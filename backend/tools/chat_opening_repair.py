"""Four-operation local sending guard; no app, database, retry or CLI.

Claim files are irreversible reservations, not provider billing proof. An
unknown/cancelled send remains occupied. Payloads, auth and raw output are never
persisted. The caller still supplies normal production durable usage metering.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

import httpx

from core.config import Settings
from core.llm_utils import strict_json
from tooling.providers.qwen import MODEL, beijing_endpoint

VERSION = "chat-opening-repair-v1"
OPERATIONS = {"diag01": "diagnostic", "verify01": "final", "verify02": "final", "chrome01": "final"}
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_LABEL = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")


class RepairStopped(RuntimeError):
    pass


def _stop(code: str) -> None:
    raise RepairStopped("opening_repair_" + code)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read(path: Path, limit: int = 1024 * 1024) -> bytes:
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        _stop("unsafe_path")
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= limit:
            _stop("unsafe_file")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            current = os.fstat(stream.fileno())
            raw = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
        if (len(raw) != before.st_size or len(raw) > limit or
            (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino) or
            (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns)):
            _stop("file_changed")
        return raw
    except OSError:
        _stop("file_unavailable")


def _write(path: Path, value: Any) -> None:
    raw = _canonical(value) + b"\n"
    if len(raw) > 65536:
        _stop("record_limit")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        _stop("record_exists_or_write_failed_preserve")


def _bindings(values: Any) -> dict[str, str]:
    if (type(values) is not dict or len(values) > 64 or any(type(k) is not str or not _LABEL.fullmatch(k)
            or type(v) is not str or not _HASH.fullmatch(v) for k, v in values.items())):
        _stop("hash_bindings_required")
    return dict(sorted(values.items()))


class OpeningRepair:
    def __init__(self, folder: Path, settings: Settings, binding: dict):
        self.folder, self.settings, self.binding = folder, settings, binding

    @staticmethod
    def _config(folder: Path, settings: Settings, grant_sha256: str, prior_ledger_hashes: dict) -> dict:
        if (not isinstance(folder, Path) or not folder.is_absolute() or ".." in folder.parts
                or any(p.is_symlink() for p in (folder, *folder.parents))
                or type(settings) is not Settings or settings.qwen_model != MODEL
                or type(grant_sha256) is not str or not _HASH.fullmatch(grant_sha256)):
            _stop("trusted_configuration_required")
        try:
            endpoint = beijing_endpoint(settings)
        except Exception:
            _stop("official_beijing_endpoint_required")
        return {"schema_version": VERSION, "limit": 4, "operations": OPERATIONS,
            "model": MODEL, "endpoint_sha256": _hash(endpoint.encode()), "grant_sha256": grant_sha256,
            "prior_ledger_hashes": _bindings(prior_ledger_hashes), "automatic_retry": False}

    @classmethod
    def create(cls, folder: Path, settings: Settings, *, grant_sha256: str,
               prior_ledger_hashes: dict[str, str] | None = None) -> OpeningRepair:
        binding = cls._config(folder, settings, grant_sha256, prior_ledger_hashes or {})
        try:
            folder.mkdir(mode=0o700)
        except OSError:
            _stop("batch_must_be_new")
        _write(folder / "binding.json", binding)
        return cls.open(folder, settings, grant_sha256=grant_sha256, prior_ledger_hashes=prior_ledger_hashes)

    @classmethod
    def open(cls, folder: Path, settings: Settings, *, grant_sha256: str,
             prior_ledger_hashes: dict[str, str] | None = None) -> OpeningRepair:
        binding = cls._config(folder, settings, grant_sha256, prior_ledger_hashes or {})
        value = cls(folder, settings, binding)
        value.verify()
        return value

    def verify(self) -> None:
        mode = self.folder.lstat()
        if mode.st_uid != os.geteuid() or not stat.S_ISDIR(mode.st_mode) or stat.S_IMODE(mode.st_mode) != 0o700:
            _stop("private_batch_required")
        try:
            raw = _read(self.folder / "binding.json")
            if strict_json(raw) != self.binding or _canonical(strict_json(raw)) != _canonical(self.binding):
                _stop("binding_changed")
            config = self._config(self.folder, self.settings, self.binding["grant_sha256"], self.binding["prior_ledger_hashes"])
            if _canonical(config) != _canonical(self.binding):
                _stop("configuration_changed")
        except (ValueError, TypeError, KeyError):
            _stop("binding_invalid")
        if (self.folder / "paused.json").exists():
            _stop("paused_no_retry")
        for op, phase in OPERATIONS.items():
            claim, result = self.folder / f"{op}-claim.json", self.folder / f"{op}-result.json"
            if not claim.exists() and not result.exists():
                continue
            if not claim.exists() or not result.exists():
                _stop("uncertain_attempt_no_retry")
            try:
                c, r = strict_json(_read(claim)), strict_json(_read(result))
                if (type(c) is not dict or set(c) != {"operation", "phase", "phase_sha256", "payload_sha256", "request_bytes", "state", "model"}
                    or (c["operation"], c["phase"], c["state"], c["model"]) != (op, phase, "claimed_before_send", MODEL)
                    or type(c["request_bytes"]) is not int or not 0 < c["request_bytes"] <= 48000
                    or any(type(c[k]) is not str or not _HASH.fullmatch(c[k]) for k in ("phase_sha256", "payload_sha256"))
                    or type(r) is not dict or set(r) != {"state", "sent", "status", "response_bytes", "response_sha256"}
                    or r["state"] != "received" or r["sent"] is not True or type(r["status"]) is not int or r["status"] != 200
                    or type(r["response_bytes"]) is not int or not 0 <= r["response_bytes"] <= 32768
                    or type(r["response_sha256"]) is not str or not _HASH.fullmatch(r["response_sha256"])):
                    _stop("uncertain_receipt_no_retry")
            except (ValueError, TypeError, KeyError):
                _stop("receipt_invalid_no_retry")

    def _phase(self, name: str, sources: dict[str, Path], prompt_sha256: str, schema_sha256: str) -> dict:
        if name not in {"diagnostic", "final"} or type(sources) is not dict or not 1 <= len(sources) <= 128:
            _stop("phase_required")
        hashes = _bindings({"prompt": prompt_sha256, "schema": schema_sha256})
        rows, size = {}, 0
        for label, path in sorted(sources.items()):
            if type(label) is not str or not _LABEL.fullmatch(label) or not isinstance(path, Path) or path.name.startswith("."):
                _stop("source_required")
            raw = _read(path, 8 * 1024 * 1024)
            size += len(raw)
            if size > 16 * 1024 * 1024:
                _stop("source_total_limit")
            rows[label] = {"path_sha256": _hash(str(path).encode()), "sha256": _hash(raw)}
        return {"schema_version": VERSION, "phase": name, "sources": rows, **hashes}

    def freeze_phase(self, name: str, *, sources: dict[str, Path], prompt_sha256: str, schema_sha256: str) -> dict:
        self.verify()
        value = self._phase(name, sources, prompt_sha256, schema_sha256)
        _write(self.folder / f"{name}-phase.json", value)
        return {"phase": name, "phase_sha256": _hash(_canonical(value))}

    def transport(self, operation: str, *, sources: dict[str, Path], prompt_sha256: str,
                  schema_sha256: str, inner: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncBaseTransport:
        if operation not in OPERATIONS:
            _stop("unknown_operation")
        return _ClaimTransport(self, operation, dict(sources), prompt_sha256, schema_sha256,
            inner if inner is not None else httpx.AsyncHTTPTransport(retries=0, trust_env=False))


class _ClaimTransport(httpx.AsyncBaseTransport):
    def __init__(self, trial, operation, sources, prompt, schema, inner):
        self.trial, self.operation, self.sources = trial, operation, sources
        self.prompt, self.schema, self.inner = prompt, schema, inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        trial, op, sent = self.trial, self.operation, False
        trial.verify()
        phase = trial._phase(OPERATIONS[op], self.sources, self.prompt, self.schema)
        if _canonical(strict_json(_read(trial.folder / f"{OPERATIONS[op]}-phase.json"))) != _canonical(phase):
            _stop("phase_changed")
        if request.method != "POST" or str(request.url) != beijing_endpoint(trial.settings):
            _stop("unexpected_endpoint")
        data = bytearray()
        async for chunk in request.stream:
            if len(data) + len(chunk) > 48000:
                _stop("request_limit")
            data.extend(chunk)
        try:
            payload = strict_json(data)
            if (type(payload) is not dict or payload.get("model") != MODEL or payload.get("stream") is not False
                    or payload.get("enable_thinking") is not False):
                _stop("unexpected_model_parameters")
        except (ValueError, TypeError, RecursionError):
            _stop("request_invalid")
        request = httpx.Request(request.method, request.url, headers=request.headers,
            content=bytes(data), extensions=request.extensions)
        _write(trial.folder / f"{op}-claim.json", {"operation": op, "phase": OPERATIONS[op],
            "phase_sha256": _hash(_canonical(phase)), "payload_sha256": _hash(bytes(data)),
            "request_bytes": len(data), "state": "claimed_before_send", "model": MODEL})
        response = None
        try:
            await asyncio.sleep(0)  # Cancellation after durable claim must not escape into a new send.
            if trial._phase(OPERATIONS[op], self.sources, self.prompt, self.schema) != phase:
                _stop("phase_changed_before_send")
            sent = True
            async with asyncio.timeout(20):
                response = await self.inner.handle_async_request(request)
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > 32768:
                        _stop("response_limit")
                    body.extend(chunk)
            _write(trial.folder / f"{op}-result.json", {"state": "received", "sent": sent,
                "status": response.status_code, "response_bytes": len(body), "response_sha256": _hash(bytes(body))})
            if response.status_code != 200:
                _write(trial.folder / "paused.json", {"reason": "upstream_status"})
            headers = [(k, v) for k, v in response.headers.multi_items() if k.lower() not in {"content-encoding", "content-length"}]
            return httpx.Response(response.status_code, headers=headers, content=bytes(body), request=request)
        except BaseException as error:
            state = "cancelled" if isinstance(error, asyncio.CancelledError) else "failed" if isinstance(error, (httpx.HTTPError, TimeoutError)) else "unknown"
            for filename, value in ((f"{op}-result.json", {"state": state, "sent": sent}), ("paused.json", {"reason": state})):
                try:
                    _write(trial.folder / filename, value)
                except RepairStopped:
                    pass  # Preserve any committed receipt or partial file; never overwrite to retry.
            raise
        finally:
            if response is not None:
                await response.aclose()

    async def aclose(self) -> None:
        await self.inner.aclose()

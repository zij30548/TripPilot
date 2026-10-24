"""Explicit full-runtime resource factory; no environment or paid preparation.

Templates and index receipts describe prior metered acquisition. Validation
binds files, inputs and persisted rows; it cannot independently authenticate a
receipt author's external-source claim. Missing or changed preparation fails
closed. MySQL remains authoritative; only technical Monitor state is shared.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

from core.llm_utils import RuntimeFailure, strict_json
from echomind_core.embedding import DIMENSIONS, EMBEDDING_MODEL, VECTOR_VERSION, _payload
from echomind_core.execution import CandidateScope, ExecutionError
from echomind_core.runtime import CandidateResources
from echomind_core.storage import StorageConfig, _drain_thread
from echomind_core.travel_capabilities import ReviewedKnowledgePort
from echomind_core.travel_domain import TRAVEL_DOMAIN
from echomind_core.travel_memory import TravelMemoryPort
from echomind_core.travel_observation import TravelObservationState
from echomind_core.travel_package import FrozenTravelDomainPackage
from echomind_core.travel_templates import PreparedTravelTemplates
from echomind_core.travel_vector_recall import INDEX_RECEIPT_VERSION, ReviewedVectorRecall
from tooling.travel.knowledge_national import get_index

VERSION = "travel-full-resource-factory-v1"
MAX_ACTIVE_TURNS = 2  # Per process/service, bounded native/cache resources; no queue.
RECEIPT_VERSION = "travel-template-preparation-receipt-v1"
REVISION = "intent-index-v1"
_OWNER = re.compile(r"[0-9a-f]{32}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_OP = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _private_directory(path: Path) -> None:
    if (not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts
            or any(parent.is_symlink() for parent in (path, *path.parents))):
        raise ExecutionError("full_resources_private_path_required")
    try:
        value = path.lstat()
        if (not stat.S_ISDIR(value.st_mode) or value.st_uid != os.geteuid()
                or stat.S_IMODE(value.st_mode) != 0o700):
            raise ValueError()
    except (OSError, ValueError):
        raise ExecutionError("full_resources_private_path_required") from None


def _read(root: Path, owner: str, filename: str, limit: int) -> bytes:
    _private_directory(root)
    folder = root / owner
    _private_directory(folder)
    path, descriptor = folder / filename, None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid() or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) != 0o600 or not 1 <= before.st_size <= limit):
            raise ValueError()
        result = bytearray()
        while len(result) <= limit:
            block = os.read(descriptor, min(65536, limit + 1 - len(result)))
            if not block:
                break
            result.extend(block)
        after, named = os.fstat(descriptor), path.lstat()
        if (len(result) > limit or len(result) != before.st_size
                or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode)
                    != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_mode)
                or (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino)):
            raise ValueError()
        return bytes(result)
    except (OSError, ValueError):
        raise ExecutionError("full_resources_preparation_unavailable") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _document(raw: bytes) -> dict[str, Any]:
    try:
        value = strict_json(raw)
        if type(value) is not dict:
            raise ValueError()
        return value
    except (RuntimeFailure, ValueError, TypeError, RecursionError):
        raise ExecutionError("full_resources_preparation_invalid") from None


def _operation(value: Any, payload: dict[str, Any]) -> None:
    if (type(value) is not dict or set(value) != {"operation_id", "payload_sha256", "response_sha256", "state"}
            or type(value["operation_id"]) is not str or _OP.fullmatch(value["operation_id"]) is None
            or value["state"] != "received" or value["payload_sha256"] != hashlib.sha256(canonical(payload)).hexdigest()
            or type(value["response_sha256"]) is not str or _HASH.fullmatch(value["response_sha256"]) is None):
        raise ExecutionError("full_resources_preparation_invalid")


@dataclass(frozen=True)
class FullResourceConfig:
    storage: StorageConfig
    template_root: Path
    namespace_revision: str = REVISION
    reviewed_city_id: str | None = None
    reviewed_venue_id: str | None = None
    reviewed_visit_date: date | None = None
    vector_receipt_root: Path | None = None

    def __post_init__(self) -> None:
        if (type(self.storage) is not StorageConfig or not isinstance(self.template_root, Path)
                or not self.template_root.is_absolute() or ".." in self.template_root.parts
                or self.namespace_revision != REVISION or self.storage.embedding_model != EMBEDDING_MODEL
                or type(self.storage.embedding_dimensions) is not int or self.storage.embedding_dimensions != DIMENSIONS
                or (self.reviewed_city_id is None) != (self.reviewed_venue_id is None)
                or self.reviewed_visit_date is not None and type(self.reviewed_visit_date) is not date
                or self.reviewed_venue_id is None and (self.vector_receipt_root is not None or self.reviewed_visit_date is not None)
                or self.reviewed_venue_id is not None and (
                    type(self.reviewed_city_id) is not str or type(self.reviewed_venue_id) is not str
                    or not self.reviewed_city_id or not self.reviewed_venue_id
                    or not isinstance(self.vector_receipt_root, Path) or not self.vector_receipt_root.is_absolute()
                    or ".." in self.vector_receipt_root.parts)):
            raise ExecutionError("full_resources_configuration")


@dataclass(frozen=True)
class FullPreflight:
    prepared: PreparedTravelTemplates
    artifact_file_sha256: str
    receipt_sha256: str
    vector_receipt_json: str | None

    def summary(self) -> dict[str, Any]:
        return {"version": VERSION, "prepared": self.prepared.summary(),
            "artifact_file_sha256": self.artifact_file_sha256, "receipt_sha256": self.receipt_sha256,
            "source_authenticity": "declared_received_not_independently_authenticated",
            "vector_receipt_present": self.vector_receipt_json is not None, "auto_prepare": False}


@dataclass(frozen=True)
class FullTurnResources:
    resources: CandidateResources
    memory: TravelMemoryPort
    prepared: PreparedTravelTemplates
    observation: TravelObservationState
    national: ReviewedKnowledgePort
    vector: ReviewedVectorRecall | None
    preflight: FullPreflight


class FullRuntimeServices:
    """One explicit process lifecycle; all authority-bearing ports are per turn."""
    def __init__(self, config: FullResourceConfig):
        if type(config) is not FullResourceConfig:
            raise ExecutionError("full_resources_configuration")
        self.config = config
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None
        self._active: set[asyncio.Task[Any]] = set()
        # Copy/validate the reviewed source registry once; no query or network.
        try:
            self._national = ReviewedKnowledgePort(get_index())
            if config.reviewed_city_id is not None:
                self._national._request({"city_id": config.reviewed_city_id, "venue_id": config.reviewed_venue_id,
                    "question": "资料范围核对", "limit": 1,
                    "visit_date": config.reviewed_visit_date.isoformat() if config.reviewed_visit_date else None})
        except Exception:
            raise ExecutionError("full_resources_configuration") from None
        self.observation = TravelObservationState()

    def preflight(self, owner: str) -> FullPreflight:
        if self._closed or type(owner) is not str or _OWNER.fullmatch(owner) is None:
            raise ExecutionError("full_resources_context_changed")
        templates = {key: list(texts) for key, texts in TRAVEL_DOMAIN.templates.items()}
        raw = _read(self.config.template_root, owner, "templates.json", 1024 * 1024)
        prepared = PreparedTravelTemplates.from_json(raw, templates)
        if prepared.owner_id != owner or prepared.revision != REVISION:
            raise ExecutionError("full_resources_preparation_binding")
        receipt_raw = _read(self.config.template_root, owner, "receipt.json", 65536)
        receipt = _document(receipt_raw)
        expected = {"schema_version": RECEIPT_VERSION, "owner_id": owner, "revision": REVISION,
            "model": EMBEDDING_MODEL, "dimensions": DIMENSIONS, "vector_version": VECTOR_VERSION,
            "template_hash": prepared.template_hash, "template_count": len(prepared.vectors),
            "artifact_sha256": hashlib.sha256(raw).hexdigest(), "artifact_canonical_sha256": prepared.artifact_hash}
        if (set(receipt) != set(expected) | {"operations"}
                or any(type(receipt.get(key)) is not type(value) or receipt[key] != value for key, value in expected.items())
                or type(receipt["operations"]) is not list or len(receipt["operations"]) != 3):
            raise ExecutionError("full_resources_preparation_invalid")
        texts = [text for values in templates.values() for text in values]
        for start, operation in zip(range(0, len(texts), 10), receipt["operations"], strict=True):
            _operation(operation, _payload(texts[start:start + 10], EMBEDDING_MODEL))
        if len({row["operation_id"] for row in receipt["operations"]}) != 3:
            raise ExecutionError("full_resources_preparation_invalid")
        vector_raw = None
        if self.config.reviewed_city_id is not None:
            vector_raw = _read(self.config.vector_receipt_root, owner, "index-receipt.json", 65536)
            vector = _document(vector_raw)
            if (vector.get("schema_version") != INDEX_RECEIPT_VERSION or vector.get("owner_id") != owner
                    or vector.get("namespace") != self.config.storage.namespace
                    or vector.get("city_id") != self.config.reviewed_city_id
                    or vector.get("venue_id") != self.config.reviewed_venue_id):
                raise ExecutionError("full_resources_preparation_binding")
        return FullPreflight(prepared, hashlib.sha256(raw).hexdigest(), hashlib.sha256(receipt_raw).hexdigest(),
            vector_raw.decode() if vector_raw is not None else None)

    @asynccontextmanager
    async def open_for_turn(self, scope: CandidateScope, current: Callable[[], CandidateScope],
                            preferences: list[dict[str, Any]],
                            read_preferences: Callable[[], Awaitable[list[dict[str, Any]]]],
                            package: FrozenTravelDomainPackage) -> AsyncIterator[FullTurnResources]:
        if (self._closed or type(scope) is not CandidateScope or not callable(current)
                or not callable(read_preferences) or type(package) is not FrozenTravelDomainPackage):
            raise ExecutionError("full_resources_configuration")
        checked = await _drain_thread(self.preflight, scope.owner_id)
        if self._closed or current() != scope:
            raise ExecutionError("full_resources_context_changed")
        task, resource = asyncio.current_task(), None
        if task is None:
            raise ExecutionError("full_resources_context_changed")
        # No await between admission and insertion: one application event loop
        # cannot oversubscribe. A task may not nest multiple native resources.
        if task in self._active or len(self._active) >= MAX_ACTIVE_TURNS:
            raise ExecutionError("full_resources_busy")
        self._active.add(task)
        try:
            resource = await CandidateResources.open(self.config.storage, scope, current)
            # A changed file while native storage opened must not reach a model.
            repeated = await _drain_thread(self.preflight, scope.owner_id)
            if repeated != checked or current() != scope or self._closed:
                raise ExecutionError("full_resources_context_changed")
            vector = None
            if self.config.reviewed_city_id is not None:
                vector = ReviewedVectorRecall(resource, self._national, self.config.reviewed_city_id,
                    self.config.reviewed_venue_id, self.config.reviewed_visit_date)
                await vector.attach_prepared(_document(checked.vector_receipt_json.encode()))
            memory = TravelMemoryPort(resource, preferences=preferences,
                read_preferences=read_preferences, package=package)
            yield FullTurnResources(resource, memory, checked.prepared, self.observation,
                                    self._national, vector, checked)
        finally:
            try:
                if resource is not None:
                    # Cancellation drains real store operations before reuse or
                    # close completion. Repeated cancellation cannot skip it.
                    await _drain_async(resource.aclose())
            finally:
                self._active.discard(task)

    async def aclose(self) -> None:
        if self._close_task is None:
            self._closed = True
            caller = asyncio.current_task()
            async def close_all() -> None:
                active = [task for task in self._active if task is not caller]
                for task in active:
                    task.cancel()
                try:
                    if active:
                        await asyncio.gather(*active, return_exceptions=True)
                finally:
                    await self.observation.close()
            # Shield one WHOLE cleanup operation, not each stage separately.
            # A cancelled caller cannot skip Monitor closure after draining
            # request stores; subsequent callers await the same completion.
            self._close_task = asyncio.create_task(close_all())
        await _drain_async(self._close_task)


async def _drain_async(operation: Awaitable[Any]) -> Any:
    task = asyncio.ensure_future(operation)
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
    if interrupted:
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()

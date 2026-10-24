"""Explicit finite acceptance preparation; never runtime cold-start fallback.

Caller owns the new grant, trusted settings, product UsageContext, independent
stores and bounded client factory. This module discovers no credentials, has no
CLI and never saves prompts/raw responses. Only actual validated Embedding
outputs can become a prepared artifact, with the transport's received hashes.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Any

import httpx

from core.config import Settings
from core.usage_runtime import MeteredAsyncClient, _context
from echomind_core.embedding import (
    DIMENSIONS, EMBEDDING_MODEL, VECTOR_VERSION, QwenEmbeddingClient,
    QwenIntentEmbedding, _payload,
)
from echomind_core.execution import ExecutionGate
from echomind_core.full_resources import RECEIPT_VERSION, REVISION, canonical, _private_directory
from echomind_core.provider import TaskBudget, bind_provider
from echomind_core.runtime import CandidateResources
from echomind_core.storage import _drain_thread
from echomind_core.travel_capabilities import ReviewedKnowledgePort
from echomind_core.travel_domain import TRAVEL_DOMAIN
from echomind_core.travel_templates import PreparedTravelTemplates, VERSION as TEMPLATE_VERSION, template_hash
from echomind_core.travel_vector_recall import INDEX_RECEIPT_VERSION, ReviewedVectorRecall
from tools.echomind_full_acceptance_ledger import FullAcceptanceLedger
from tools.echomind_full_acceptance_transport import (
    FullTransportObservation, _persist, full_embedding_stage,
)

VERSION = "echomind-full-acceptance-preparation-v1"
_OWNER = re.compile(r"[a-f0-9]{32}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
ClientFactory = Callable[[str], AbstractAsyncContextManager[tuple[MeteredAsyncClient, FullTransportObservation]]]


class PreparationStopped(RuntimeError):
    """Fixed diagnostics only; no input, local path or provider body."""


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _safe_owner(owner: Any) -> str:
    if type(owner) is not str or _OWNER.fullmatch(owner) is None:
        raise PreparationStopped("preparation_owner_required")
    context = _context()
    if (context.owner_id != owner or re.fullmatch(
            r"/chat/conversations/[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}/assistant-turns",
            context.request_path) is None):
        raise PreparationStopped("preparation_usage_binding_changed")
    return owner


def _directory(root: Path, owner: str, names: tuple[str, ...]) -> Path:
    _private_directory(root)
    folder = root / owner
    try:
        folder.mkdir(mode=0o700)
    except FileExistsError:
        pass
    _private_directory(folder)
    for name in names:
        try:
            (folder / name).lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise PreparationStopped("preparation_output_unavailable") from None
        raise PreparationStopped("preparation_output_already_exists")
    return folder


def _write(folder: Path, documents: tuple[tuple[str, bytes], ...]) -> None:
    """Exclusive private writes; preserve all partial/uncertain files for audit."""
    _private_directory(folder)
    descriptor = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o700):
            raise PreparationStopped("preparation_private_output_required")
        for name, raw in documents:
            if name not in {"templates.json", "receipt.json", "index-receipt.json"} or not 1 <= len(raw) <= 1024 * 1024:
                raise PreparationStopped("preparation_output_invalid")
            handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
            try:
                remaining = memoryview(raw)
                while remaining:
                    count = os.write(handle, remaining)
                    if count <= 0:
                        raise OSError("write_failed")
                    remaining = remaining[count:]
                os.fsync(handle)
            finally:
                os.close(handle)
            os.fsync(descriptor)
    except OSError:
        raise PreparationStopped("preparation_write_failed_preserve_files") from None
    finally:
        os.close(descriptor)


async def _durable(function: Callable[[], Any]) -> Any:
    result, interrupted = await _persist(function)
    if interrupted:
        import asyncio
        raise asyncio.CancelledError
    return result


async def _stop(ledger: FullAcceptanceLedger) -> None:
    try:
        await _durable(lambda: ledger.pause("execution_error"))
    except BaseException:
        pass  # Unknown durable state stays occupied, never repaired/refunded.


def _operation_receipt(ledger: FullAcceptanceLedger, observation: FullTransportObservation,
                       op: str, stage: str, payload: dict[str, Any]) -> tuple[dict, dict]:
    if type(observation) is not FullTransportObservation or len(observation.records) != 1:
        raise PreparationStopped("preparation_one_received_attempt_required")
    row = observation.records[0]
    # Hash actual provider JSON serialization separately from canonical receipt
    # binding. No request is dispatched and no prompt/body is persisted here.
    wire_hash = _digest(httpx.Request("POST", "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings", json=payload).content)
    if (type(row) is not dict or set(row) != {"attempt_id", "stage", "payload_sha256", "state", "metadata", "response"}
            or row["stage"] != stage or row["state"] != "received" or row["payload_sha256"] != wire_hash
            or type(row["metadata"]) is not dict or type(row["metadata"].get("status_code")) is not int
            or row["metadata"].get("status_code") != 200
            or row["metadata"].get("request_model") != EMBEDDING_MODEL
            or type(row["metadata"].get("response_sha256")) is not str
            or _HASH.fullmatch(row["metadata"]["response_sha256"]) is None
            or canonical(row["response"]) != canonical({"structure": "valid", "vector_count": len(payload["input"]), "dimensions": DIMENSIONS})):
        raise PreparationStopped("preparation_response_binding_invalid")
    state = ledger.verify()
    attempt = state["attempts"].get(row["attempt_id"])
    if (state["paused"] or type(attempt) is not dict or attempt["op_id"] != op
            or attempt["stage"] != stage or attempt["state"] != "received"
            or attempt["payload_sha256"] != wire_hash or canonical(attempt["metadata"]) != canonical(row["metadata"])):
        raise PreparationStopped("preparation_ledger_binding_invalid")
    receipt = {"operation_id": row["attempt_id"], "state": "received",
        "payload_sha256": _digest(canonical(payload)), "response_sha256": row["metadata"]["response_sha256"]}
    safe = {"operation_id": op, "attempt_id": row["attempt_id"], "stage": stage,
        "wire_payload_sha256": wire_hash, "canonical_payload_sha256": receipt["payload_sha256"],
        "response_sha256": receipt["response_sha256"], "metadata": row["metadata"]}
    return receipt, safe


def _arguments(settings: Settings, client_for: ClientFactory, ledger: FullAcceptanceLedger,
               binding_for: Callable[[str], str]) -> None:
    if (type(settings) is not Settings or type(ledger) is not FullAcceptanceLedger
            or not callable(client_for) or not callable(binding_for)):
        raise PreparationStopped("preparation_trusted_arguments_required")


async def prepare_templates(root: Path, owner: str, revision: str, settings: Settings,
                            client_for: ClientFactory, ledger: FullAcceptanceLedger,
                            binding_for: Callable[[str], str]) -> dict[str, Any]:
    _arguments(settings, client_for, ledger, binding_for)
    owner = _safe_owner(owner)
    if revision != REVISION or type(revision) is not str:
        raise PreparationStopped("preparation_revision_invalid")
    folder = await _drain_thread(_directory, root, owner, ("templates.json", "receipt.json"))
    templates = {key: list(values) for key, values in TRAVEL_DOMAIN.templates.items()}
    texts = [text for values in templates.values() for text in values]
    if len(texts) != 24:
        raise PreparationStopped("preparation_template_count_changed")
    payloads = [_payload(texts[start:start + 10], EMBEDDING_MODEL) for start in range(0, len(texts), 10)]
    vectors, receipts, observed = [], [], []
    try:
        for op, payload in zip(("prep01", "prep02", "prep03"), payloads, strict=True):
            _safe_owner(owner)
            await _durable(lambda op=op: ledger.start_operation(op, binding_for(op)))
            async with client_for(op) as (client, observation):
                if type(client) is not MeteredAsyncClient:
                    raise PreparationStopped("preparation_exact_metered_client_required")
                with full_embedding_stage("templates.embedding"), bind_provider(settings, client,
                        TaskBudget(model_limit=1, tool_limit=1, token_limit=2048, seconds=30)):
                    response = await QwenEmbeddingClient().embed(payload["input"])
            receipt, safe = _operation_receipt(ledger, observation, op, "templates.embedding", payload)
            vectors.extend([list(vector) for vector in response.vectors])
            receipts.append(receipt)
            observed.append(safe)
            await _durable(lambda op=op: ledger.seal_operation(op))
        artifact = {"schema_version": TEMPLATE_VERSION, "owner_id": owner, "revision": revision,
            "model": EMBEDDING_MODEL, "dimensions": DIMENSIONS, "vector_version": VECTOR_VERSION,
            "template_hash": template_hash(templates),
            "fingerprint": QwenIntentEmbedding(owner, revision).template_fingerprint(templates), "vectors": vectors}
        raw = canonical(artifact)
        prepared = PreparedTravelTemplates.from_json(raw, templates)
        receipt = {"schema_version": RECEIPT_VERSION, "owner_id": owner, "revision": revision,
            "model": EMBEDDING_MODEL, "dimensions": DIMENSIONS, "vector_version": VECTOR_VERSION,
            "template_hash": prepared.template_hash, "template_count": len(vectors),
            "artifact_sha256": _digest(raw), "artifact_canonical_sha256": prepared.artifact_hash,
            "operations": receipts}
        receipt_raw = canonical(receipt)
        await _durable(lambda: _write(folder, (("templates.json", raw), ("receipt.json", receipt_raw))))
        return {"schema_version": VERSION, "kind": "templates", "model": EMBEDDING_MODEL,
            "template_count": len(vectors), "artifact_sha256": _digest(raw),
            "artifact_canonical_sha256": prepared.artifact_hash, "receipt_sha256": _digest(receipt_raw),
            "operations": observed, "source": "metered_received_outputs", "quality_acceptance": "not_assessed"}
    except BaseException:
        await _stop(ledger)
        raise


async def prepare_index(root: Path, resources: CandidateResources, reviewed: ReviewedKnowledgePort,
                        city_id: str, venue_id: str, settings: Settings, client_for: ClientFactory,
                        ledger: FullAcceptanceLedger, binding_for: Callable[[str], str]) -> dict[str, Any]:
    _arguments(settings, client_for, ledger, binding_for)
    if type(resources) is not CandidateResources or type(reviewed) is not ReviewedKnowledgePort:
        raise PreparationStopped("preparation_resources_required")
    owner = _safe_owner(resources.scope.owner_id)
    if resources._closed or resources.current() != resources.scope or resources._active is not None:
        raise PreparationStopped("preparation_resources_changed")
    folder = await _drain_thread(_directory, root, owner, ("index-receipt.json",))
    vector = ReviewedVectorRecall(resources, reviewed, city_id, venue_id)
    texts = list(dict.fromkeys(chunk for entry in vector._mappings.values() for chunk in entry["chunks"]))
    payload = _payload(texts, EMBEDDING_MODEL)
    budget = TaskBudget(model_limit=1, tool_limit=1, token_limit=2048, seconds=30)
    gate = ExecutionGate(resources.scope, resources.current, budget)
    try:
        # This preparation accepts only a NEW private knowledge collection.
        count = await _drain_thread(resources._store._collections["knowledge"].count)
        if count != 0:
            raise PreparationStopped("preparation_index_not_empty")
        await _durable(lambda: ledger.start_operation("index01", binding_for("index01")))
        async with client_for("index01") as (client, observation):
            if type(client) is not MeteredAsyncClient:
                raise PreparationStopped("preparation_exact_metered_client_required")
            with full_embedding_stage("index.embedding"):
                async with resources.task(gate, settings, client):
                    summary = await vector.prepare()
        receipt_op, safe = _operation_receipt(ledger, observation, "index01", "index.embedding", payload)
        def read_rows():
            resources._store._check()
            collection = resources._store._collections["knowledge"]
            if collection.count() > 128:
                raise PreparationStopped("preparation_index_size_invalid")
            rows = collection.get(include=["documents", "metadatas", "embeddings"])
            return sorted([{"id": identifier, "document": rows["documents"][index],
                "metadata": rows["metadatas"][index], "embedding_sha256": _digest(canonical(
                    [float(value) for value in rows["embeddings"][index]]))}
                for index, identifier in enumerate(rows["ids"])], key=lambda item: item["id"])
        rows = await _drain_thread(read_rows)
        receipt = {"schema_version": INDEX_RECEIPT_VERSION, "owner_id": owner,
            "namespace": resources._store.config.namespace, "embedding_model": EMBEDDING_MODEL,
            "dimensions": DIMENSIONS, "corpus_hash": vector.corpus_hash, "city_id": vector.city_id,
            "venue_id": vector.venue_id, "chunk_count": vector._chunk_count,
            "rows_sha256": _digest(canonical(rows)), "operations": [receipt_op]}
        attached = ReviewedVectorRecall(resources, reviewed, city_id, venue_id)
        await attached.attach_prepared(receipt)
        raw = canonical(receipt)
        await _durable(lambda: _write(folder, (("index-receipt.json", raw),)))
        await _durable(lambda: ledger.seal_operation("index01"))
        return {"schema_version": VERSION, "kind": "index", "model": EMBEDDING_MODEL,
            "receipt_sha256": _digest(raw), "rows_sha256": receipt["rows_sha256"], "index": summary,
            "operations": [safe], "source": "metered_received_outputs", "quality_acceptance": "not_assessed"}
    except BaseException:
        await _stop(ledger)
        raise

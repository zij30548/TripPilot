"""Opt-in Qwen semantic vectors, never an implicit Chroma download or fallback.

The trusted caller binds the same provider/task and durable owner context as
chat. Each at-most-ten-text batch reserves one shared model attempt and one
existing qwen_text durable attempt. There is no loop-owning synchronous bridge:
async callers precompute vectors, then pass explicit embeddings to Chroma.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import math
import re
from typing import Any

import httpx

from core.llm_utils import RuntimeFailure, bounded_json, strict_json
from core.logging_filters import install_http_log_redaction
from core.usage_runtime import _context
from echomind_core.provider import ProviderError, _BOUND
from tooling.providers.qwen import QwenError, beijing_endpoint, error_category

EMBEDDING_MODEL = "text-embedding-v4"
DIMENSIONS = 1024
VECTOR_VERSION = "qwen-text-embedding-v4-1024-unit-v1"
MAX_BATCH = 10  # Official v4 batch limit, independently below the 48 KiB body cap.
MAX_TEXT_CHARS = 8000
MAX_TEXT_BYTES = 8000  # Conservative UTF-8 cap, not a tokenizer claim.
MAX_INPUT_BYTES = 48_000
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
REQUEST_SECONDS = 20.0
_SAFE_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_OWNER = re.compile(r"[0-9a-f]{32}\Z")
_ASSISTANT_PATH = re.compile(
    r"/chat/conversations/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/assistant-turns\Z")


@dataclass(frozen=True)
class EmbeddingUsage:
    input_tokens: int | None


@dataclass(frozen=True)
class EmbeddingResponse:
    vectors: tuple[tuple[float, ...], ...]
    usage: EmbeddingUsage
    request_id: str | None
    model: str | None
    requested_model: str = EMBEDDING_MODEL


def _binding(owner_id: str | None = None):
    service = _BOUND.get()
    if service is None or service.closed.is_set():
        raise ProviderError("provider_unbound")
    context = _context()  # Server-owned binding, never supplied by a model/client.
    if (type(context.owner_id) is not str or _OWNER.fullmatch(context.owner_id) is None
            or type(context.request_path) is not str or _ASSISTANT_PATH.fullmatch(context.request_path) is None
            or owner_id is not None and owner_id != context.owner_id):
        raise ProviderError("embedding_namespace")
    return service


def _payload(texts: Any, model: str) -> dict[str, Any]:
    if model != EMBEDDING_MODEL:
        raise ProviderError("configuration")
    if type(texts) is not list or not 1 <= len(texts) <= MAX_BATCH:
        raise ProviderError("embedding_input")
    try:
        if any(type(text) is not str or not text.strip() or len(text) > MAX_TEXT_CHARS
               or len(text.encode("utf-8")) > MAX_TEXT_BYTES for text in texts):
            raise ProviderError("embedding_input")
        result = {"model": EMBEDDING_MODEL, "input": list(texts),
                  "dimensions": DIMENSIONS, "encoding_format": "float"}
        bounded_json(result, MAX_INPUT_BYTES)
    except (UnicodeError, ValueError, TypeError, RuntimeFailure):
        raise ProviderError("embedding_input") from None
    return result


def unit_vector(value: Any) -> tuple[float, ...]:
    """Reject shape/non-finite/overflow/zero; never truncate or pad a vector."""
    if type(value) not in (list, tuple) or len(value) != DIMENSIONS:
        raise ProviderError("embedding_structure")
    try:
        if any(type(number) not in (int, float) for number in value):
            raise ValueError()
        numbers = tuple(float(number) for number in value)
        if not all(math.isfinite(number) for number in numbers):
            raise ValueError()
        norm = math.hypot(*numbers)
        if not math.isfinite(norm) or norm <= 0:
            raise ValueError()
        normalized = tuple(number / norm for number in numbers)
        if not all(math.isfinite(number) for number in normalized):
            raise ValueError()
        if not math.isclose(math.hypot(*normalized), 1.0, rel_tol=1e-10, abs_tol=1e-10):
            raise ValueError()
        return normalized
    except (OverflowError, ValueError, TypeError):
        raise ProviderError("embedding_structure") from None


def _response(data: Any, count: int) -> EmbeddingResponse:
    if (type(data) is not dict or set(data) - {"data", "model", "object", "usage", "id"}
            or not {"data", "object"} <= set(data) or data["object"] != "list"
            or type(data["data"]) is not list or len(data["data"]) != count):
        raise ProviderError("embedding_structure")
    model, identifier = data.get("model"), data.get("id")
    if model is not None and model != EMBEDDING_MODEL:
        raise ProviderError("embedding_structure")
    if identifier is not None and (type(identifier) is not str or _SAFE_ID.fullmatch(identifier) is None):
        raise ProviderError("embedding_structure")
    usage = data.get("usage")
    tokens = None
    if usage is not None:
        if type(usage) is not dict or set(usage) - {"prompt_tokens", "total_tokens"}:
            raise ProviderError("embedding_structure")
        for number in usage.values():
            if number is not None and (type(number) is not int or not 0 <= number <= 1_000_000):
                raise ProviderError("embedding_structure")
        prompt, total = usage.get("prompt_tokens"), usage.get("total_tokens")
        if prompt is not None and total is not None and prompt != total:
            raise ProviderError("embedding_structure")
        tokens = total if total is not None else prompt
    ordered: list[tuple[float, ...] | None] = [None] * count
    for row in data["data"]:
        if (type(row) is not dict or set(row) != {"embedding", "index", "object"}
                or row["object"] != "embedding" or type(row["index"]) is not int
                or not 0 <= row["index"] < count or ordered[row["index"]] is not None):
            raise ProviderError("embedding_structure")
        ordered[row["index"]] = unit_vector(row["embedding"])
    if any(vector is None for vector in ordered):
        raise ProviderError("embedding_structure")
    return EmbeddingResponse(tuple(ordered), EmbeddingUsage(tokens), identifier, model)  # type: ignore[arg-type]


class QwenEmbeddingClient:
    """No credentials/config constructor; use an explicit trusted task binding."""

    async def embed(self, texts: list[str], *, model: str = EMBEDDING_MODEL) -> EmbeddingResponse:
        service = _binding()
        payload = _payload(texts, model)  # Independent snapshot before the first await.
        task = asyncio.current_task()
        if task is None or service.closed.is_set():
            raise ProviderError("provider_unbound")
        service.pending.add(task)
        try:
            await service.budget.reserve_embedding()
            install_http_log_redaction()
            endpoint = beijing_endpoint(service.settings).removesuffix("chat/completions") + "embeddings"
            async with asyncio.timeout(min(REQUEST_SECONDS, service.budget.remaining_seconds)):
                async with service.client.stream(
                    "POST", endpoint, json=payload,
                    headers={"Authorization": "Bearer " + service.settings.qwen_api_key.get_secret_value().strip()},
                    follow_redirects=False, timeout=httpx.Timeout(20, connect=5),
                ) as response:
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise ProviderError("embedding_structure")
                        body.extend(chunk)
                    if response.status_code != 200:
                        raise ProviderError(error_category(response.status_code, bytes(body)))
                    result = _response(strict_json(body), len(payload["input"]))
            if service.closed.is_set():
                raise ProviderError("provider_unbound")
            return result
        except (TimeoutError, httpx.TimeoutException):
            service.budget.stop()
            raise ProviderError("timeout") from None
        except QwenError as error:
            service.budget.stop()
            raise ProviderError(error.code) from None
        except (ValueError, UnicodeError, RecursionError):
            service.budget.stop()
            raise ProviderError("embedding_structure") from None
        except httpx.HTTPError:
            service.budget.stop()
            raise ProviderError("upstream") from None
        except ProviderError:
            # The original recognizer/retrieval manager can catch exceptions
            # and return a fallback. Budget denial must also remain visible to
            # the trusted execution gate, not masquerade as complete retrieval.
            service.budget.stop()
            raise
        except BaseException:
            service.budget.stop()
            raise
        finally:
            service.pending.discard(task)


class QwenIntentEmbedding:
    """Original vectors interface, explicit owner/revision and no local fallback.

    A cache is private to this immutable binding and one template fingerprint.
    Revision/model/dimension changes cannot reuse an incompatible vector space.
    Large cold template sets may exceed the unchanged three-attempt task budget;
    trusted callers must precompute/cache in separately authorized bounded work.
    """

    enabled = True

    def __init__(self, owner_id: str, revision: str) -> None:
        if (type(owner_id) is not str or _OWNER.fullmatch(owner_id) is None
                or type(revision) is not str or _SAFE_ID.fullmatch(revision) is None):
            raise ProviderError("embedding_namespace")
        self._owner_id, self._revision = owner_id, revision
        self.remote_templates: dict[str, dict[Any, list[list[float]]]] = {}
        self.local_templates: dict = {}  # Compatibility clear(), never populated.
        self._lock = asyncio.Lock()
        self._client = QwenEmbeddingClient()

    @property
    def owner_id(self) -> str:
        return self._owner_id

    @property
    def revision(self) -> str:
        return self._revision

    def _templates(self, templates: dict) -> tuple[dict, list[str], str]:
        if type(templates) is not dict or not 1 <= len(templates) <= 32:
            raise ProviderError("embedding_input")
        snapshot = {}
        for key, group in templates.items():
            if type(group) not in (list, tuple) or not 1 <= len(group) <= 16:
                raise ProviderError("embedding_input")
            snapshot[key] = tuple(group)
        texts = [text for group in snapshot.values() for text in group]
        if len(texts) > 128:
            raise ProviderError("embedding_input")
        # Validate all batches/query before any paid attempt; no partial import
        # caused by a late invalid text. Batch limits are not bypassed by cache.
        for start in range(0, len(texts), MAX_BATCH):
            _payload(texts[start:start + MAX_BATCH], EMBEDDING_MODEL)
        fingerprint = hashlib.sha256(bounded_json(
            [self.owner_id, self.revision, VECTOR_VERSION, [(str(k), v) for k, v in snapshot.items()]],
            128 * MAX_TEXT_BYTES + 8192).encode()).hexdigest()
        return snapshot, texts, fingerprint

    def template_fingerprint(self, templates: dict) -> str:
        """Receipt key for an explicitly prepared, same-owner vector index."""
        return self._templates(templates)[2]

    def load_prepared(self, templates: dict, vectors: list, *, fingerprint: str,
                      model: str, dimensions: int) -> None:
        """Load trusted precomputed vectors; never generates them or opens a client.

        This validates structural namespace/version correspondence, not that a
        caller-provided vector is an authenticated Qwen output. Preparation must
        use the same metered client under separately explicit authorization.
        """
        _binding(self.owner_id)
        snapshot, texts, expected = self._templates(templates)
        if (fingerprint != expected or model != EMBEDDING_MODEL
                or type(dimensions) is not int or dimensions != DIMENSIONS
                or type(vectors) is not list or len(vectors) != len(texts)):
            raise ProviderError("embedding_namespace")
        normalized = [unit_vector(vector) for vector in vectors]
        groups, offset = {}, 0
        for key, group in snapshot.items():
            groups[key] = [list(vector) for vector in normalized[offset:offset + len(group)]]
            offset += len(group)
        self.remote_templates = {expected: groups}

    async def vectors(self, message: str, templates: dict, local_embed: Any = None):
        service = _binding(self.owner_id)
        snapshot, texts, fingerprint = self._templates(templates)
        _payload([message], EMBEDDING_MODEL)
        async with self._lock:
            _binding(self.owner_id)
            if fingerprint not in self.remote_templates:
                # Large original template sets are explicit index-preparation
                # work, not a license to spend the whole turn and then fail.
                calls = math.ceil(len(texts) / MAX_BATCH) + 1
                if service.budget.counts[0] + calls > service.budget.model_limit:
                    # Original recognizer catches embedding exceptions. Latch
                    # its shared runner so it cannot present a zero-score local
                    # fallback as successful semantic fusion after this denial.
                    service.budget.stop()
                    raise ProviderError("embedding_preparation_required")
                vectors = []
                for start in range(0, len(texts), MAX_BATCH):
                    result = await self._client.embed(texts[start:start + MAX_BATCH])
                    vectors.extend(result.vectors)
                groups, offset = {}, 0
                for key, group in snapshot.items():
                    groups[key] = [list(vector) for vector in vectors[offset:offset + len(group)]]
                    offset += len(group)
                self.remote_templates = {fingerprint: groups}
            groups = self.remote_templates[fingerprint]
            result = await self._client.embed([message])
            # Return independent data: upstream matching/learn code may mutate.
            return ({key: [list(vector) for vector in values] for key, values in groups.items()},
                    list(result.vectors[0]), "qwen/text-embedding-v4/1024/v1")

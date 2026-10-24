"""Immutable, namespace-bound prepared travel vectors; never a sender.

An artifact validates structure and binding, not that Qwen actually produced
the supplied vectors. Its acquisition needs a separately reviewed, authorized
metered preparation. No fake index or automatic cold-index work is provided.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

from core.llm_utils import RuntimeFailure, strict_json
from echomind_core.embedding import (
    DIMENSIONS, EMBEDDING_MODEL, VECTOR_VERSION, QwenIntentEmbedding, unit_vector,
)
from echomind_core.execution import ExecutionError
from echomind_core.travel_domain import TRAVEL_DOMAIN, TravelIntent

VERSION = "travel-intent-templates-v1"
MAX_ARTIFACT_BYTES = 1024 * 1024
_FIELDS = frozenset({"schema_version", "owner_id", "revision", "model", "dimensions",
                     "vector_version", "template_hash", "fingerprint", "vectors"})


def template_hash(templates: dict) -> str:
    """Hash exact ordered current travel leaves, never labels or cached output."""
    if (type(templates) is not dict or set(templates) != set(TravelIntent)
            or any(type(key) is not TravelIntent for key in templates)
            or any(type(values) not in (list, tuple) or tuple(values) != TRAVEL_DOMAIN.templates[key]
                   for key, values in templates.items())):
        raise ExecutionError("travel_templates_binding")
    raw = json.dumps([(key.value, list(values)) for key, values in templates.items()],
                     ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, init=False)
class PreparedTravelTemplates:
    owner_id: str
    revision: str
    fingerprint: str
    template_hash: str
    vectors: tuple[tuple[float, ...], ...]
    artifact_hash: str

    def __init__(self, *arguments: Any, **keywords: Any):
        raise ExecutionError("travel_templates_use_validated_factory")

    @classmethod
    def from_json(cls, raw: bytes | str, templates: dict) -> PreparedTravelTemplates:
        if type(raw) not in (str, bytes):
            raise ExecutionError("travel_templates_artifact")
        try:
            encoded = raw.encode("utf-8") if isinstance(raw, str) else raw
            if not 1 <= len(encoded) <= MAX_ARTIFACT_BYTES:
                raise ExecutionError("travel_templates_artifact")
            return cls.from_data(strict_json(encoded), templates)
        except (UnicodeError, ValueError, RuntimeFailure, TypeError, RecursionError):
            raise ExecutionError("travel_templates_artifact") from None

    @classmethod
    def from_data(cls, data: Any, templates: dict) -> PreparedTravelTemplates:
        digest = template_hash(templates)
        if (type(data) is not dict or set(data) != _FIELDS or data["schema_version"] != VERSION
                or type(data["owner_id"]) is not str or re.fullmatch(r"[0-9a-f]{32}", data["owner_id"]) is None
                or type(data["revision"]) is not str or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", data["revision"]) is None
                or data["model"] != EMBEDDING_MODEL or type(data["dimensions"]) is not int
                or data["dimensions"] != DIMENSIONS or data["vector_version"] != VECTOR_VERSION
                or data["template_hash"] != digest or type(data["fingerprint"]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", data["fingerprint"]) is None
                or type(data["vectors"]) is not list
                or len(data["vectors"]) != sum(len(values) for values in templates.values())
                or any(type(vector) is not list or len(vector) != DIMENSIONS for vector in data["vectors"])):
            raise ExecutionError("travel_templates_artifact")
        try:
            canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            if len(canonical) > MAX_ARTIFACT_BYTES:
                raise ExecutionError("travel_templates_artifact")
            adapter = QwenIntentEmbedding(data["owner_id"], data["revision"])
            if adapter.template_fingerprint(templates) != data["fingerprint"]:
                raise ExecutionError("travel_templates_binding")
            vectors = []
            for vector in data["vectors"]:
                if type(vector) is not list:
                    raise ExecutionError("travel_templates_artifact")
                unit_vector(vector)  # Existing finite/shape/zero/overflow checks.
                vectors.append(tuple(float(value) for value in vector))
            result = object.__new__(cls)
            for key, value in (("owner_id", data["owner_id"]), ("revision", data["revision"]),
                               ("fingerprint", data["fingerprint"]), ("template_hash", digest),
                               ("vectors", tuple(vectors)), ("artifact_hash", hashlib.sha256(canonical).hexdigest())):
                object.__setattr__(result, key, value)
            return result
        except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError, RuntimeFailure):
            raise ExecutionError("travel_templates_artifact") from None

    def load_into(self, embedding: QwenIntentEmbedding, templates: dict) -> None:
        if (type(embedding) is not QwenIntentEmbedding or embedding.owner_id != self.owner_id
                or embedding.revision != self.revision or template_hash(templates) != self.template_hash
                or embedding.template_fingerprint(templates) != self.fingerprint):
            raise ExecutionError("travel_templates_binding")
        # The ORIGINAL method checks current bound provider/owner before loading.
        # No reservation/send is created; vectors are never generated here.
        embedding.load_prepared(templates, [list(vector) for vector in self.vectors],
                                fingerprint=self.fingerprint, model=EMBEDDING_MODEL, dimensions=DIMENSIONS)

    def summary(self) -> dict[str, Any]:
        return {"schema_version": VERSION, "artifact_hash": self.artifact_hash,
                "model": EMBEDDING_MODEL, "dimensions": DIMENSIONS, "vector_version": VECTOR_VERSION,
                "template_hash": self.template_hash, "fingerprint": self.fingerprint,
                "vector_count": len(self.vectors), "source_authenticity": "not_proven_by_structure",
                "model_calls": 0}

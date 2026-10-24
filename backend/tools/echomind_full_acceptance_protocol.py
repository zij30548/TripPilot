"""Offline-only full-candidate freeze/verification. No client, key or sender.

One finite 66-attempt batch binds new preparation plus eight known scenarios.
An unlimited number of testing rounds never makes a task/batch unbounded and
never unlocks historical paused ledgers. Review labels have no model-input path.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Any
from urllib.parse import urlsplit

from tools.echomind_travel_acceptance_plan import (
    EvaluationError, ROOT, _path, _read, canonical, source_hashes, strict_json,
)

VERSION = "echomind-full-acceptance-protocol-v1"
PLAN_VERSION = "echomind-full-acceptance-plan-v1"
PLAN = "backend/evaluations/echomind-full-acceptance-v1.json"
MAX_DOCUMENT = 2 * 1024 * 1024
MAX_MANIFEST = 128 * 1024
CAP = 66
MODEL = "qwen3.7-flash-2026-07-15"
EMBEDDING_MODEL = "text-embedding-v4"
PREPARATION = (("prep01", "template_embedding", 0, 10), ("prep02", "template_embedding", 10, 10),
               ("prep03", "template_embedding", 20, 4), ("index01", "reviewed_index_embedding", 0, 5))
OPERATIONS = (("f01", "api", "single", 6), ("f02", "api", "single", 6),
              ("f03", "api", "single", 6), ("f04", "chrome", "single", 6),
              ("f05", "chrome", "single", 6), ("f06", "chrome", "collaboration", 10),
              ("f07", "api", "reference", 16), ("f08", "api", "single", 6))
OPERATION_ORDER = tuple(row[0] for row in PREPARATION + OPERATIONS)
OPERATION_CAPS = dict([(row[0], 1) for row in PREPARATION] + [(row[0], row[3]) for row in OPERATIONS])
EXPECTED_TOOLS = (
    {"inspect_trip", "travel_clarify"}, {"inspect_trip", "update_trip_requirements"},
    {"inspect_trip", "travel_places"}, {"inspect_trip", "travel_schedule"},
    {"inspect_trip", "travel_budget"}, {"travel_schedule", "travel_budget"},
    {"inspect_trip", "search_knowledge_base"}, {"inspect_trip", "travel_information", "travel_clarify"},
)
HASH = re.compile(r"[a-f0-9]{64}\Z")
LABEL = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
_PLAN_FIELDS = {"schema_version", "baseline_commit", "scope", "authorization", "runtime", "fixtures",
                "preparation", "operations", "review"}
_OP_FIELDS = {"id", "channel", "tier", "model_send_cap", "text", "preconditions", "allowed_tools",
              "expected", "required_evidence", "blocking", "non_goals"}


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _fail(code: str) -> None:
    raise EvaluationError("full_acceptance_" + code)


def _same(a: Any, b: Any) -> bool:
    # JSON canonicalization also distinguishes bool from int and 1 from 1.0.
    try:
        return canonical(a) == canonical(b)
    except (ValueError, TypeError, OverflowError, RecursionError):
        return False


def _text_list(value: Any, minimum: int = 1, maximum: int = 20) -> bool:
    return type(value) is list and minimum <= len(value) <= maximum and all(
        type(item) is str and item.strip() == item and 1 <= len(item) <= 2000
        and not any(ord(char) < 32 and char not in "\n\t" for char in item) for item in value)


def _document(path: Path, limit: int = MAX_DOCUMENT) -> dict[str, Any]:
    try:
        value = strict_json(_read(path, limit))
        if type(value) is not dict:
            _fail("invalid_document")
        return value
    except (ValueError, TypeError, RecursionError):
        _fail("invalid_document")


def load_manifest(*, root: Path = ROOT) -> dict[str, Any]:
    value = _document(_path(root, PLAN), MAX_MANIFEST)
    if (set(value) != _PLAN_FIELDS or value.get("schema_version") != PLAN_VERSION
            or re.fullmatch(r"[a-f0-9]{40}", str(value.get("baseline_commit"))) is None
            or type(value.get("scope")) is not str):
        _fail("invalid_manifest")
    authorization = {"kind": "auth-user-no-total-limit",
        "user_statement": "无限次授权你，只要给我都测到位", "batch_model_send_cap": CAP,
        "automatic_retry": False, "real_amap_calls": 0, "judge_calls": 0,
        "historical_allowances_reused": False}
    if not _same(value["authorization"], authorization):
        _fail("authorization_changed")
    runtime = value["runtime"]
    expected_runtime = {"name": "echomind_travel_full_v2", "candidate": "echomind-travel-full-v2",
        "model": MODEL, "embedding_model": EMBEDDING_MODEL, "dimensions": 1024,
        "stream": False, "enable_thinking": False, "parallel_tool_calls": False,
        "single_call_input_bytes": 48000, "single_call_output_token_cap": 2048, "provider_seconds": 20,
        "template_revision": "intent-index-v1", "tiers": {
            "single": {"models": 6, "tools": 8, "tokens": 8192, "seconds": 60},
            "collaboration": {"models": 10, "tools": 12, "tokens": 16384, "seconds": 90},
            "reference": {"models": 16, "tools": 16, "tokens": 24576, "seconds": 90}},
        "generation_parameters": {"classifier": {"max_tokens": 256, "temperature": .1},
            "roles": {"max_tokens": 1024, "temperature": .1},
            "composer": {"max_tokens": 1000, "temperature": .1, "seconds": 8},
            "rewrite": {"max_tokens": 512, "temperature": .3, "seconds": 10},
            "rerank": {"max_tokens": 2048, "temperature": .0, "seconds": 30}},
        "active_product_cutover": False}
    if not _same(runtime, expected_runtime):
        _fail("runtime_changed")
    if not _same(value["preparation"], [{"id": i, "kind": k, "input_start": start,
            "input_count": count, "model_send_cap": 1} for i, k, start, count in PREPARATION]):
        _fail("preparation_changed")
    operations = value["operations"]
    if type(operations) is not list or len(operations) != len(OPERATIONS):
        _fail("operation_matrix")
    for row, (identifier, channel, tier, cap), tools in zip(operations, OPERATIONS, EXPECTED_TOOLS):
        if (type(row) is not dict or set(row) != _OP_FIELDS
                or not _same([row["id"], row["channel"], row["tier"], row["model_send_cap"]],
                             [identifier, channel, tier, cap])
                or type(row["text"]) is not str or not 1 <= len(row["text"]) <= 4000
                or row["text"].strip() != row["text"]
                or any(not _text_list(row[key]) for key in _OP_FIELDS - {
                    "id", "channel", "tier", "model_send_cap", "text"})):
            _fail("operation_matrix")
        if len(set(row["allowed_tools"])) != len(row["allowed_tools"]) or set(row["allowed_tools"]) != tools:
            _fail("operation_tools")
    if sum(OPERATION_CAPS.values()) != CAP:
        _fail("operation_total")
    fixtures = value["fixtures"]
    if (type(fixtures) is not dict or set(fixtures) != {"owner", "requirements", "formal", "costs", "reference", "isolation"}
            or not _same(fixtures["requirements"], {"destination_id": "shanghai", "start_date": "2026-11-01",
                "end_date": "2026-11-02", "travelers": 2, "budget_cny": 3000, "transport_mode": "walking"})
            or not _same(fixtures["costs"], {"ticket": "", "meal_2026-11-01": "",
                "meal_2026-11-02": "", "accommodation": "", "other": ""})
            or not _same(fixtures["reference"], {"city_id": "310000", "venue_id": "museum_people",
                "visit_date": None, "unit_count": 5, "max_import_embedding_batches": 1})
            or not _text_list(fixtures["formal"]) or any(type(fixtures[key]) is not str for key in ("owner", "isolation"))
            or type(value["review"]) is not dict or set(value["review"]) != {
                "dimensions", "business_gate", "ordinary_failure", "pause_on", "safety_stop", "limitations"}
            or any(not _text_list(value["review"][key]) for key in ("dimensions", "pause_on", "limitations"))
            or any(type(value["review"][key]) is not str for key in ("business_gate", "ordinary_failure", "safety_stop"))):
        _fail("fixture_or_review")
    return value


def model_input(operation: dict[str, Any]) -> dict[str, str]:
    """Business text only, never labels/expected tools or an entire wire payload."""
    if (type(operation) is not dict or set(operation) != _OP_FIELDS
            or operation.get("id") not in dict((row[0], row) for row in OPERATIONS)
            or type(operation.get("text")) is not str or not 1 <= len(operation["text"]) <= 4000):
        _fail("invalid_operation")
    return {"text": operation["text"]}


def trusted_settings(endpoint: str) -> dict[str, Any]:
    """Record only an official Beijing endpoint hash; never discover secrets."""
    try:
        parsed = urlsplit(endpoint)
        host = parsed.hostname or ""
        allowed = host == "dashscope.aliyuncs.com" or re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.cn-beijing\.maas\.aliyuncs\.com", host)
        if (type(endpoint) is not str or not allowed or parsed.scheme != "https" or parsed.port is not None
                or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
                or endpoint != f"https://{host}/compatible-mode/v1/chat/completions"):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        _fail("official_beijing_endpoint_required")
    return {"runtime": "echomind_travel_full_v2", "model": MODEL, "embedding_model": EMBEDDING_MODEL,
            "dimensions": 1024, "endpoint_sha256": digest(endpoint.encode())}


def _bindings(value: Any) -> dict[str, str]:
    if (type(value) is not dict or not 1 <= len(value) <= 64
            or any(type(key) is not str or LABEL.fullmatch(key) is None
                   or type(item) is not str or HASH.fullmatch(item) is None for key, item in value.items())):
        _fail("hashed_fixture_binding_required")
    return dict(sorted(value.items()))


def _commit(root: Path) -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True,
                            check=False, timeout=5)
    value = result.stdout.strip()
    if result.returncode or re.fullmatch(r"[a-f0-9]{40}", value) is None:
        _fail("commit_unavailable")
    return value


def _sources(root: Path) -> dict[str, str]:
    """Reuse the prior source closure and additionally freeze backend tests."""
    selected = set(source_hashes(root))
    folder = _path(root, "backend/tests")
    if not folder.is_dir():
        _fail("test_source_tree_required")
    def unavailable(_: OSError) -> None:
        _fail("test_source_tree_unavailable")
    for directory, subdirs, files in os.walk(folder, followlinks=False, onerror=unavailable):
        subdirs[:] = sorted(item for item in subdirs if item != "__pycache__")
        for name in (*subdirs, *files):
            path = Path(directory) / name
            if path.is_symlink():
                _fail("test_source_symlink")
            if name in files and path.suffix == ".py":
                selected.add(path.relative_to(root).as_posix())
    if len(selected) > 10000:
        _fail("source_count_exceeded")
    size, result = 0, {}
    for name in sorted(selected):
        raw = _read(_path(root, name), 8 * 1024 * 1024)
        size += len(raw)
        if size > 64 * 1024 * 1024:
            _fail("source_total_exceeded")
        result[name] = digest(raw)
    return result


def _old_ledgers(prior: dict[str, Path]) -> dict[str, dict[str, str]]:
    if (type(prior) is not dict or not 1 <= len(prior) <= 64
            or any(type(label) is not str or LABEL.fullmatch(label) is None
                   or not isinstance(path, Path) or not path.is_absolute() for label, path in prior.items())):
        _fail("prior_ledger_paths_required")
    paths = [path.as_posix() for path in prior.values()]
    if len(set(paths)) != len(paths):
        _fail("duplicate_prior_ledger")
    result = {}
    for label, path in sorted(prior.items()):
        if ".." in path.parts or any(part.is_symlink() for part in (path, *path.parents)):
            _fail("prior_ledger_unsafe")
        result[label] = {"path_sha256": digest(path.as_posix().encode()), "sha256": digest(_read(path, MAX_DOCUMENT))}
    return result


def _materials(root: Path) -> dict[str, str]:
    names = ("backend/knowledge/national-corpus-v2.json", "backend/knowledge/national-sources-v2.json",
             "backend/echomind_core/travel_package.py", "backend/echomind_core/travel_domain.py",
             "backend/echomind_core/provider.py", "backend/echomind_core/embedding.py")
    result = {name: digest(_read(_path(root, name), 8 * 1024 * 1024)) for name in names}
    corpus = _document(_path(root, names[0]))
    if (type(corpus.get("venues")) is not list or type(corpus.get("documents")) is not list
            or any(type(row) is not dict for row in corpus["venues"] + corpus["documents"])
            or any(type(row.get("sections")) is not list for row in corpus["documents"]
                   if row.get("venue_id") == "museum_people")):
        _fail("reviewed_scope_changed")
    venues = [row for row in corpus.get("venues", []) if row.get("id") == "museum_people"]
    documents = [row for row in corpus.get("documents", []) if row.get("venue_id") == "museum_people"]
    if (len(venues) != 1 or venues[0].get("city_id") != "310000"
            or sum(len(row.get("sections", [])) for row in documents) != 5
            or {row.get("id") for row in documents} != {"museum-west", "people-2025"}):
        _fail("reviewed_scope_changed")
    return result


def _state(*, fixture_binding: dict[str, str], prior_ledgers: dict[str, Path],
           settings_summary: dict[str, Any], root: Path = ROOT, commit: str | None = None) -> dict[str, Any]:
    manifest = load_manifest(root=root)
    effective_commit = _commit(root)
    if commit is not None and commit != effective_commit:
        _fail("commit_changed")
    fields = {"runtime", "model", "embedding_model", "dimensions", "endpoint_sha256"}
    if (type(settings_summary) is not dict or set(settings_summary) != fields
            or not _same({key: settings_summary[key] for key in fields - {"endpoint_sha256"}},
                         {"runtime": "echomind_travel_full_v2", "model": MODEL,
                          "embedding_model": EMBEDDING_MODEL, "dimensions": 1024})
            or type(settings_summary["endpoint_sha256"]) is not str
            or HASH.fullmatch(settings_summary["endpoint_sha256"]) is None):
        _fail("settings_changed")
    return {"schema_version": VERSION, "code_commit": effective_commit,
        "manifest_path": PLAN, "manifest_sha256": digest(_read(_path(root, PLAN), MAX_MANIFEST)),
        "source_files": _sources(root), "material_files": _materials(root),
        "fixture_binding": _bindings(fixture_binding), "prior_ledger_hashes": _old_ledgers(prior_ledgers),
        "settings": dict(settings_summary), "authorization": manifest["authorization"],
        "operation_order": list(OPERATION_ORDER), "operation_caps": OPERATION_CAPS,
        "operation_text_hashes": {row["id"]: digest(canonical(model_input(row))) for row in manifest["operations"]},
        "max_model_sends": CAP, "real_model_calls": 0, "real_amap_calls": 0,
        "judge_calls": 0, "sender_available": False, "product_cutover": False,
        "dynamic_payload_policy": "freeze actual captured input before each first send; native feedback binds prior actual result, never labels or expected answers"}


def _private_output(path: Path) -> None:
    if (not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts
            or any(part.is_symlink() for part in (path, *path.parents))):
        _fail("private_output_required")
    try:
        mode = path.parent.lstat()
        if not stat.S_ISDIR(mode.st_mode) or mode.st_uid != os.geteuid() or stat.S_IMODE(mode.st_mode) != 0o700:
            _fail("private_output_required")
    except OSError:
        _fail("private_output_required")


def _summary(value: dict[str, Any], protocol_sha256: str) -> dict[str, Any]:
    return {"verified": True, "schema_version": VERSION, "protocol_sha256": protocol_sha256,
        "manifest_sha256": value["manifest_sha256"], "code_commit": value["code_commit"],
        "operation_count": len(OPERATION_ORDER), "max_model_sends": CAP,
        "real_model_calls": 0, "real_amap_calls": 0, "judge_calls": 0,
        "sender_available": False, "product_cutover": False}


def freeze(path: Path, **options: Any) -> dict[str, Any]:
    """Create a new 0600 freeze only after all inputs verify. Never opens a ledger."""
    _private_output(path)
    if path.exists():
        _fail("protocol_must_be_new")
    value = _state(**options)
    raw = canonical(value) + b"\n"
    if len(raw) > MAX_DOCUMENT:
        _fail("protocol_too_large")
    descriptor = None
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        # A partial/uncertain file is deliberately retained, never auto-overwritten.
        _fail("freeze_write_failed_preserve_file")
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return _summary(value, digest(raw))


def verify(path: Path, **options: Any) -> dict[str, Any]:
    _private_output(path)
    mode = path.lstat()
    if mode.st_uid != os.geteuid() or stat.S_IMODE(mode.st_mode) != 0o600:
        _fail("private_protocol_required")
    raw = _read(path, MAX_DOCUMENT)
    try:
        value = strict_json(raw)
    except (ValueError, TypeError, RecursionError):
        _fail("invalid_document")
    if not _same(value, _state(**options)):
        _fail("frozen_inputs_changed")
    return _summary(value, digest(raw))


def ledger_binding(path: Path, *, grant_sha256: str | None = None, **options: Any) -> dict[str, Any]:
    """Verified immutable identity for a separate reviewed sender/counter."""
    receipt = verify(path, **options)
    value = _document(path)
    if grant_sha256 is not None and (type(grant_sha256) is not str or HASH.fullmatch(grant_sha256) is None):
        _fail("grant_hash_required")
    return {"grant_sha256": grant_sha256 or digest(canonical(value["authorization"])),
        "protocol_sha256": receipt["protocol_sha256"],
        "code_freeze_sha256": digest(canonical(value["source_files"])),
        "prior_ledger_hashes": {label: digest(canonical(row)) for label, row in value["prior_ledger_hashes"].items()}}

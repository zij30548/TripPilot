"""Independent private journals and controlled concurrency; no paid sends."""
from concurrent.futures import ThreadPoolExecutor
import multiprocessing

import pytest

from tools.serveflow_live_ledger import CASES, LIMIT, MODEL, Ledger, LedgerError


BINDING = {"protocol_sha256": "a" * 64, "source_hashes": {"EchoMind/api/main.py": "b" * 64}}


@pytest.fixture
def ledger(tmp_path):
    tmp_path.chmod(0o700)
    return Ledger.create(tmp_path / "live.jsonl", binding=BINDING)


def claim(ledger):
    return ledger.claim(payload_sha256="c" * 64, model=MODEL)


def received(ledger, attempt):
    ledger.settle(attempt, state="received", metadata={"status_code": 200, "input_tokens": None,
        "output_tokens": None, "upstream_model": "unknown"})


def test_explicit_binding_and_exclusive_creation(ledger):
    assert ledger.verify(binding=BINDING)["attempts"] == 0
    with pytest.raises(LedgerError, match="already_exists"):
        Ledger.create(ledger.path, binding=BINDING)
    other = Ledger(ledger.path)
    with pytest.raises(LedgerError, match="binding_not_verified"):
        other.begin_case("greeting")
    with pytest.raises(LedgerError, match="binding_mismatch"):
        other.verify(binding={**BINDING, "protocol_sha256": "d" * 64})
    with pytest.raises(LedgerError, match="binding_mismatch"):
        other.verify(binding={**BINDING, "source_hashes": {"EchoMind/api/main.py": "d" * 64}})
    assert ledger.summary()["paused"]


def test_fixed_order_once_and_restart_after_complete_case(ledger):
    with pytest.raises(LedgerError, match="case_not_admissible"):
        ledger.begin_case("policy")
    for index, name in enumerate(CASES):
        ledger.begin_case(name)
        attempt = claim(ledger)
        assert ledger.summary()["attempts"] == index + 1
        received(ledger, attempt)
        ledger.finish_case(name)
        ledger = Ledger(ledger.path)
        ledger.verify(binding=BINDING)
        with pytest.raises(LedgerError, match="case_not_admissible"):
            ledger.begin_case(name)
    assert ledger.summary()["cases"] == {name: "finished" for name in CASES}
    with pytest.raises(LedgerError, match="case_not_admissible"):
        ledger.begin_case("greeting")


def test_pending_or_begun_case_never_resumes_after_restart(ledger):
    ledger.begin_case("greeting")
    attempt = claim(ledger)
    reopened = Ledger(ledger.path)
    assert reopened.summary()["pending"] == [attempt]
    with pytest.raises(LedgerError, match="not_owned"):
        reopened.verify(binding=BINDING)
    with pytest.raises(LedgerError, match="not_owned"):
        claim(reopened)
    with pytest.raises(LedgerError, match="unsettled_case"):
        ledger.finish_case("greeting")
    with pytest.raises(LedgerError, match="case_not_admissible"):
        ledger.begin_case("split_order")
    received(ledger, attempt)
    with pytest.raises(LedgerError, match="not_owned"):
        reopened.verify(binding=BINDING)


@pytest.mark.parametrize("state", ["failed", "cancelled", "unknown"])
def test_execution_failures_pause_and_never_refund(ledger, state):
    ledger.begin_case("greeting")
    attempt = claim(ledger)
    ledger.settle(attempt, state=state, metadata={"error_code": "timeout"})
    assert ledger.summary()["paused"] and ledger.summary()["remaining"] == 19
    with pytest.raises(LedgerError, match="attempt_not_admissible"):
        claim(ledger)
    with pytest.raises(LedgerError, match="already_settled"):
        received(ledger, attempt)
    ledger.finish_case("greeting")
    with pytest.raises(LedgerError, match="case_not_admissible"):
        ledger.begin_case("split_order")


@pytest.mark.parametrize("status", [401, 403, 429])
def test_authentication_and_rate_limit_response_pause(ledger, status):
    ledger.begin_case("greeting")
    ledger.settle(claim(ledger), state="received", metadata={"status_code": status})
    assert ledger.summary()["paused"]


def try_claim(ledger):
    try:
        return claim(ledger)
    except LedgerError:
        return None


def test_thread_race_cannot_overspend(ledger):
    ledger.begin_case("greeting")
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: try_claim(ledger), range(45)))
    ids = [value for value in results if value]
    assert len(ids) == len(set(ids)) == LIMIT
    assert ledger.summary()["attempts"] == LIMIT and ledger.summary()["remaining"] == 0
    for attempt in ids:
        received(ledger, attempt)
    ledger.finish_case("greeting")
    reopened = Ledger(ledger.path)
    reopened.verify(binding=BINDING)
    reopened.begin_case("split_order")
    with pytest.raises(LedgerError, match="attempt_not_admissible"):
        claim(reopened)


def process_claims(ledger, queue):
    queue.put([try_claim(ledger) for _ in range(15)])


def test_independent_processes_share_same_limit(ledger):
    ledger.begin_case("greeting")
    context = multiprocessing.get_context("fork")
    queue = context.Queue()
    processes = [context.Process(target=process_claims, args=(ledger, queue)) for _ in range(3)]
    for process in processes:
        process.start()
    results = [queue.get(timeout=10) for _ in processes]
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    ids = [attempt for group in results for attempt in group if attempt]
    assert len(ids) == len(set(ids)) == LIMIT
    assert ledger.summary()["attempts"] == LIMIT


@pytest.mark.parametrize("damage", ["truncate", "edit", "duplicate_key"])
def test_corruption_fail_closed(ledger, damage):
    raw = ledger.path.read_bytes()
    if damage == "truncate":
        raw = raw[:-1]
    elif damage == "edit":
        raw = raw.replace(b"greeting", b"altering", 1)
    else:
        raw = raw.replace(b'"seq":0', b'"seq":0,"seq":0', 1)
    ledger.path.write_bytes(raw)
    with pytest.raises(LedgerError):
        ledger.verify(binding=BINDING)


def test_fsync_failure_never_grants_send_or_resume(ledger, monkeypatch):
    ledger.begin_case("greeting")
    import tools.echomind_travel_trial_ledger as primitives
    monkeypatch.setattr(primitives.os, "fsync", lambda _: (_ for _ in ()).throw(OSError("controlled")))
    with pytest.raises(LedgerError, match="storage_failure"):
        claim(ledger)
    with pytest.raises(LedgerError, match="storage_failure"):
        claim(ledger)
    reopened = Ledger(ledger.path)
    assert reopened.summary()["attempts"] == 1
    assert reopened.summary()["paused"]
    with pytest.raises(LedgerError, match="not_owned"):
        reopened.verify(binding=BINDING)


@pytest.mark.parametrize("metadata", [
    {"raw": "never logged"}, {"error_code": "private provider text"},
    {"request_model": "other-model"}, {"input_tokens": float("nan")},
    {"status_code": True}, {"upstream_model": []},
])
def test_metadata_whitelist_rejects_unsafe_values(ledger, metadata):
    ledger.begin_case("greeting")
    attempt = claim(ledger)
    with pytest.raises(LedgerError):
        ledger.settle(attempt, state="received", metadata=metadata)
    assert ledger.summary()["pending"] == [attempt] and ledger.summary()["paused"]
    assert b"private provider text" not in ledger.path.read_bytes()
    with pytest.raises(LedgerError, match="attempt_not_admissible"):
        claim(ledger)


def test_other_live_attempts_can_settle_after_pause(ledger):
    ledger.begin_case("greeting")
    first, second = claim(ledger), claim(ledger)
    ledger.settle(first, state="unknown", metadata={"error_code": "network_error"})
    received(ledger, second)
    ledger.finish_case("greeting")
    assert ledger.summary()["paused"] and ledger.summary()["pending"] == []
    assert ledger.summary()["attempts"] == 2

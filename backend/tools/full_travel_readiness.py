"""Offline readiness checks for the full travel candidate's current HTTP seams.

This is not an execution entry point, a sender, a quality score, or permission
to cut over a service. Real contracts/projection functions are exercised with
synthetic receipts; no database, environment file, provider or browser is read.
Frontend wait values are explicitly supplied source inspections, bound to the
actual inspected file hashes, NOT measurements of a running browser/proxy.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import stat
from typing import Any, get_args

from pydantic import TypeAdapter, ValidationError

VERSION = "full-travel-readiness-v1"
# Explicit target; registration alone still does not prove HTTP admission.
PROPOSED_RUNTIME = "echomind_travel_full_v2"
FRONTEND_SOURCE = "frontend/src/lib/assistant.ts"
PROXY_SOURCE = "frontend/src/lib/server/backend-routing.ts"
_SOURCES = frozenset({FRONTEND_SOURCE, PROXY_SOURCE})
_ID = "10000000-0000-4000-8000-000000000001"
_OP = "10000000-0000-4000-8000-000000000002"


class ProbeError(ValueError):
    """Only finite safe codes; never echo CLI values or file contents."""


@dataclass(frozen=True)
class WaitInspection:
    path: str
    sha256: str
    milliseconds: int
    method: str = "source-inspected-not-browser-tested"

    def validate(self) -> None:
        if (self.path not in _SOURCES or type(self.sha256) is not str
                or re.fullmatch(r"[0-9a-f]{64}", self.sha256) is None
                or type(self.milliseconds) is not int or not 1 <= self.milliseconds <= 300_000
                or self.method != "source-inspected-not-browser-tested"):
            raise ProbeError("wait_inspection_invalid")

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        return {"path": self.path, "sha256": self.sha256, "milliseconds": self.milliseconds,
                "method": self.method}


def inspect_wait_source(root: Path, relative: str, milliseconds: int) -> WaitInspection:
    """Hash the fixed source; the caller must review the supplied numeric value.

    We intentionally do not infer runtime behavior from grep/regex matches.
    The number is a transparent source-review input, not executed TypeScript.
    """
    if not isinstance(root, Path) or relative not in _SOURCES:
        raise ProbeError("wait_source_invalid")
    root = root.absolute()
    path = root / relative
    try:
        for current in (root, *[root.joinpath(*Path(relative).parts[:index])
                                for index in range(1, len(Path(relative).parts) + 1)]):
            if current.is_symlink():
                raise ProbeError("wait_source_invalid")
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 1 <= info.st_size <= 100_000:
            raise ProbeError("wait_source_invalid")
        content = path.read_bytes()
        if len(content) != info.st_size:
            raise ProbeError("wait_source_changed")
    except OSError:
        raise ProbeError("wait_source_unavailable") from None
    result = WaitInspection(relative, hashlib.sha256(content).hexdigest(), milliseconds)
    result.validate()
    return result


def configuration_probe() -> dict[str, Any]:
    from core.config import Settings

    # Inspect/execute the actual field validator without constructing Settings:
    # BaseSettings construction could otherwise discover a real .env/secret.
    annotation = Settings.model_fields["assistant_runtime"].annotation
    allowed = list(get_args(annotation))
    try:
        TypeAdapter(annotation).validate_python(PROPOSED_RUNTIME, strict=True)
        accepted = True
    except ValidationError:
        accepted = False
    return {"id": "http_runtime_registration", "status": "registered-admission-unverified" if accepted else "blocked",
            "method": "executed-current-settings-field-contract",
            "proposed_runtime": PROPOSED_RUNTIME, "registered_values": allowed,
            "proposed_runtime_is_existing_configuration": accepted,
            "contract_accepts_proposed_runtime": accepted,
            "reason": "registration-alone-does-not-prove-admission" if accepted else "proposed_full_runtime_not_registered",
            "admission_check": "not-executed; configuration validation alone does not execute the admitted candidate",
            "limitation": "Rejecting the proposed name alone does not prove which candidate the HTTP path executes.",
            "http_execution_verified": False}


def _receipt(actions: list[tuple[str, dict[str, Any]]]) -> tuple[dict, dict]:
    from core.schemas.chat import Conversation, Intent
    from core.schemas.dialogue import validate_action

    conversation = Conversation(id=_ID, title="离线契约探针", revision=2, requirements=Intent(),
        confirmed_revision=None, sources=[], issues=[], messages=[],
        last_turn={"operation_id": _OP, "state": "succeeded", "error_code": None},
        created_at="2026-10-24T00:00:00Z", updated_at="2026-10-24T00:00:00Z").model_dump(mode="json")
    rows = [{"name": group, "data": {"message": "只读提议，尚未执行。",
              "dialogue_action": validate_action(action["name"], action["arguments"])}}
            for group, action in actions]
    task = {"stale": False, "conversation_revision": 2, "state": "waiting", "operation_id": _OP,
            "checkpoint": {"result": {"surface": "dialogue-v2", "tool_results": rows}}}
    return conversation, task


def browser_projection_probe(*, surface: str = "legacy") -> dict[str, Any]:
    from core.llm_utils import RuntimeFailure
    from core.schemas.dialogue import DialogueAction
    from tooling.travel.dialogue import project_action

    schedule = ("travel_schedule", {"name": "read_schedule", "arguments": {"date": None, "day": 2}})
    budget = ("travel_budget", {"name": "read_budget", "arguments": {}})
    # The same real projector accepts each individual strictly validated action.
    individual = []
    for item in (schedule, budget):
        conversation, task = _receipt([item])
        value = DialogueAction.model_validate(project_action(conversation, task))
        individual.append(value.action is not None)
    conversation, task = _receipt([schedule, budget])
    code = None
    try:
        DialogueAction.model_validate(project_action(conversation, task))
        accepted = True
    except RuntimeFailure as error:
        code, accepted = error.code, False
    except ValidationError:
        code, accepted = "dialogue_action_contract_rejected", False
    legacy = {"individual_read_projections_valid": individual, "pair_projection_valid": accepted,
              "safe_error_code": code, "scope": "legacy-compatibility-not-v3-target"}
    if surface == "dialogue-v3":
        from core.schemas.dialogue import DialogueActions
        from tooling.travel.dialogue import project_actions
        task["checkpoint"]["result"].update(surface="dialogue-v3", workspace_version=_ID)
        value = DialogueActions.model_validate(project_actions(conversation, task))
        return {"id": "parallel_read_browser_projection", "status": "ready",
                "method": "executed-v3-projector-and-response-contract", "legacy_compatibility": legacy,
                "pair_projection_valid": len(value.actions) == 2, "browser_execution_verified": False,
                "reason": "v3-contract-only-browser-execution-unverified"}
    if surface != "legacy":
        raise ProbeError("surface_invalid")
    return {"id": "parallel_read_browser_projection", "status": "ready" if accepted else "blocked",
            "method": "executed-current-projector-and-response-contract",
            "individual_read_projections_valid": individual, "pair_projection_valid": accepted,
            "safe_error_code": code, "browser_execution_verified": False,
            "reason": "browser-execution-still-unverified" if accepted else "two_read_proposals_not_representable"}


async def _budget_projection(*, surface: str = "legacy") -> list[dict[str, Any]]:
    from api.assistant import TaskView, _trace_rows
    from echomind_core.travel_budget import TravelBudgetTier, TravelTaskBudget

    results = []
    for tier in TravelBudgetTier:
        budget = TravelTaskBudget(tier)
        # These are actual in-memory budget RESERVATIONS, never HTTP sends or
        # successful model/tool executions. Embedding reserves zero output.
        for _ in range(budget.model_limit):
            await budget.reserve_embedding()
        for _ in range(budget.tool_limit):
            await budget.reserve_tool()
        models, tools, _ = budget.counts
        rows = _trace_rows({"model_calls": models, "tool_calls": tools, "events": []},
                           error_code="offline_reservation_probe")
        displayed = {"model_calls": sum(row.get("tool") == "model" for row in rows),
                     "tool_calls": sum(row.get("tool") not in (None, "model") for row in rows)}
        try:
            TaskView.model_validate({"id": _OP, "state": "failed", "model_calls": models,
                "tool_calls": tools, "stop_reason": "offline_reservation_probe", "steps": []})
            accepts = True
        except ValidationError:
            accepts = False
        faithful = (models, tools) == (displayed["model_calls"], displayed["tool_calls"])
        legacy = {"displayed_from_current_trace": displayed, "display_rows": len(rows),
                  "reservation_display_matches": faithful, "task_view_accepts_full_counts": accepts,
                  "scope": "legacy-compatibility-not-v3-target"}
        if surface == "dialogue-v3":
            from api.assistant import TaskViewV2, _full_trace_rows
            receipt = {"schema_version": "travel-execution-receipt-v1", "budget": budget.summary(),
                "reservations": {"models": models, "tools": tools, "output_tokens": 0},
                "dispatch": {"status": "unknown", "sent": None, "received": None, "failed": None,
                             "cancelled": None, "uncertain": None}, "trace_complete": False}
            full_rows = _full_trace_rows({"execution_receipt": receipt})
            view = TaskViewV2.model_validate({"id": _OP, "state": "failed", "model_calls": models,
                "tool_calls": tools, "stop_reason": "offline_reservation_probe", "steps": [],
                "execution_receipt": receipt})
            results.append({"tier": tier.value, "reservation_counts": {"model_calls": models, "tool_calls": tools},
                "projected_reservation_counts": {"model_calls": view.model_calls, "tool_calls": view.tool_calls},
                "trace_reservation_rows": len(full_rows), "dispatch_status": view.execution_receipt.dispatch.status,
                "legacy_compatibility": legacy, "status": "ready"})
            continue
        if surface != "legacy":
            raise ProbeError("surface_invalid")
        results.append({"tier": tier.value, "reservation_counts": {"model_calls": models, "tool_calls": tools},
            "displayed_from_current_trace": displayed, "display_rows": len(rows),
            "reservation_display_matches": faithful, "task_view_accepts_full_counts": accepts,
            "status": "ready" if faithful and accepts else "blocked"})
    return results


def reservation_projection_probe(*, surface: str = "legacy") -> dict[str, Any]:
    tiers = asyncio.run(_budget_projection(surface=surface))
    return {"id": "full_tier_reservation_display", "status": "ready" if all(
                row["status"] == "ready" for row in tiers) else "blocked",
            "method": "executed-original-reservations-current-trace-and-task-view",
            "trace_case": "aggregate-reservations-without-per-step-events-not-a-live-turn",
            "count_semantics": "conservative_reservations_not_actual_dispatches", "tiers": tiers,
            "reason": "v3-contract-only-dispatch-observation-unverified" if surface == "dialogue-v3"
                else "reservation_display_or_contract_mismatch"}


def wait_probe(frontend: WaitInspection, proxy: WaitInspection) -> dict[str, Any]:
    frontend.validate()
    proxy.validate()
    if frontend.path != FRONTEND_SOURCE or proxy.path != PROXY_SOURCE:
        raise ProbeError("wait_inspection_role_mismatch")
    target = 90_000
    covered = frontend.milliseconds > target and proxy.milliseconds > frontend.milliseconds
    return {"id": "full_tier_wait_capacity", "status": "ready" if covered else "blocked",
            "method": "explicit-source-inspection-not-runtime-measurement", "task_deadline_ms": target,
            "frontend": frontend.as_dict(), "proxy": proxy.as_dict(),
            "strict_wait_order_covers_task": covered, "browser_or_proxy_runtime_verified": False,
            "reason": "source-capacity-only" if covered else "source_waits_do_not_cover_90_seconds"}


def probe(frontend: WaitInspection, proxy: WaitInspection, *, surface: str = "dialogue-v3") -> dict[str, Any]:
    checks = [configuration_probe(), browser_projection_probe(surface=surface), reservation_projection_probe(surface=surface),
              wait_probe(frontend, proxy)]
    ready = all(row["status"] == "ready" for row in checks)
    return {"schema_version": VERSION, "status": "contracts-ready-not-product-acceptance" if ready else "blocked",
            "scope": "offline-current-contracts-and-explicit-source-inspection",
            "target_surface": surface,
            "real_model_calls": 0, "business_external_calls": 0, "database_operations": 0,
            "browser_operations": 0, "checks": checks,
            "limitation": "Rejection reproduced is a blocked seam, not successful product acceptance. No cutover performed."}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend-wait-ms", type=int, required=True,
                        help="Explicitly reviewed source value, not a live browser measurement")
    parser.add_argument("--proxy-wait-ms", type=int, required=True,
                        help="Explicitly reviewed source value, not a live proxy measurement")
    parser.add_argument("--surface", choices=("legacy", "dialogue-v3"), default="dialogue-v3")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    try:
        result = probe(inspect_wait_source(root, FRONTEND_SOURCE, args.frontend_wait_ms),
                       inspect_wait_source(root, PROXY_SOURCE, args.proxy_wait_ms), surface=args.surface)
    except ProbeError as error:
        print(json.dumps({"schema_version": VERSION, "status": "blocked", "code": str(error)}))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 2 if result["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Thin V3_CORE-only recovery adapter for the unstarted Goal 4 tail."""
from __future__ import annotations

import argparse
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Sequence

from codepacex.capability_v3 import CapabilityV3Flag
from evals.evaluation_v2 import capability_v3_pilot as pilot, control_canary, full_replay

FREEZE_NAME = "v3-core-tail-completion-freeze.json"
TAIL_TASK_IDS = full_replay.GOAL4_ORDER[4:]
TAIL_HARD_CAP_CNY = "225.935092"


@contextmanager
def _configured() -> Any:
    previous = (pilot.PILOT_TASK_IDS, pilot.TREATMENTS, pilot.EXPERIMENT_NAME,
                pilot.FREEZE_NAME, pilot.validate_freeze)
    pilot.PILOT_TASK_IDS = TAIL_TASK_IDS
    pilot.TREATMENTS = (CapabilityV3Flag.V3_CORE,)
    pilot.EXPERIMENT_NAME = "capability-v3-core-goal4-tail-completion"
    pilot.FREEZE_NAME = FREEZE_NAME
    pilot.validate_freeze = validate_freeze
    try:
        yield
    finally:
        pilot.PILOT_TASK_IDS, pilot.TREATMENTS, pilot.EXPERIMENT_NAME, pilot.FREEZE_NAME, pilot.validate_freeze = previous


def freeze_payload(root: Path, *, bound_main_commit: str | None = None, run_id: str) -> dict[str, Any]:
    with _configured():
        payload = pilot.freeze_payload(root, bound_main_commit=bound_main_commit, run_id=run_id,
                                      approved_total_hard_cap_cny=TAIL_HARD_CAP_CNY)
    payload["execution_order"] = "strictly_serial_v3_core_tail_completion_only"
    payload["recovery_contract"] = {
        "predecessor_actions_run": "30503096853",
        "predecessor_artifact_id": "8744897594",
        "retries_only": list(TAIL_TASK_IDS[:1]),
        "previously_scorable_task_runs_excluded": list(full_replay.GOAL4_ORDER[:4]),
        "completion_kind": "two-run-infrastructure-recovery-completion",
    }
    return payload


def write_freeze(root: Path, output: Path, *, run_id: str) -> dict[str, str]:
    payload = freeze_payload(root, run_id=run_id)
    output.mkdir(parents=True, exist_ok=False)
    pilot._write_json(output / FREEZE_NAME, payload)
    (output / "pricing-snapshot.json").write_bytes((root / full_replay.PRICING_PATH).read_bytes())
    return {"freeze_sha256": pilot._sha256(output / FREEZE_NAME),
            "pricing_snapshot_sha256": payload["budget_contract"]["pricing_snapshot_sha256"],
            "runtime_hash": payload["runtime_hash"], "task_list_sha256": payload["task_list_sha256"],
            "allocation_hash": payload["allocation_binding"]["allocation_hash"]}


def validate_freeze(root: Path, freeze: Path) -> dict[str, Any]:
    actual = pilot._read_json(freeze / FREEZE_NAME)
    binding = actual.get("allocation_binding")
    expected = freeze_payload(root, bound_main_commit=str(actual.get("bound_main_commit", "")),
                              run_id=str(binding.get("internal_run_id", ""))) if isinstance(binding, dict) else None
    if actual != expected:
        raise ValueError("V3_CORE tail Freeze differs from its canonical contract")
    if (freeze / "pricing-snapshot.json").read_bytes() != (root / full_replay.PRICING_PATH).read_bytes():
        raise ValueError("V3_CORE tail pricing snapshot differs from the frozen repository file")
    return {"valid": True, "freeze_sha256": pilot._sha256(freeze / FREEZE_NAME), **pilot.write_freeze_identities(actual)}


def preflight(root: Path, freeze: Path, artifact_root: Path) -> dict[str, Any]:
    with _configured(): return pilot.run_preflight(root, freeze, artifact_root)


def rehearse_tail(root: Path, freeze: Path, artifact_root: Path, *, run_id: str) -> dict[str, Any]:
    """Zero-provider paid-path rehearsal with an initial transport terminal."""
    identities = validate_freeze(root, freeze)
    ordinal = 0
    def executor(_root: Path, _frozen: dict[str, Any], _metadata: dict[str, Any], _gate: Any,
                 task_root: Path, _execution_run_id: str, task: dict[str, Any], **_kwargs: Any) -> control_canary.PaidTaskResult:
        nonlocal ordinal
        ordinal += 1
        task_dir = task_root / "tasks" / task["instance_id"]
        task_dir.mkdir(parents=True, exist_ok=True)
        if ordinal == 1:
            result = control_canary.PaidTaskResult(task["instance_id"], "failed", "not_exported", "not_run", "not_run", "error", "transport_failed", terminal_status="infrastructure_error", failure_classification="simulated_network_error", resolved=False, live_executor_invoked=True, agent_dispatch_started=True, provider_client_initialized=True, settlement_count=0)
        else:
            result = control_canary.PaidTaskResult(task["instance_id"], "completed_without_candidate", "not_exported", "executed", "not_run", "completed", "completed", terminal_status="unresolved", failure_classification="simulated_tail_terminal", resolved=False, live_executor_invoked=True, agent_dispatch_started=True, provider_client_initialized=True, model_response_observed=True, settlement_count=0)
        pilot._write_json(task_dir / "task-result.json", {**result.__dict__, "provider_transport": "deterministic_zero_provider"})
        return result
    with _configured():
        summary = pilot.run_paid_pilot(root, freeze, artifact_root, expected_freeze_sha256=identities["freeze_sha256"], expected_allocation_hash=identities["allocation_hash"], approved_total_hard_cap_cny=TAIL_HARD_CAP_CNY, authorization_acknowledgement="zero-provider-tail-readiness", run_id=run_id, executor=executor)
    summary.update({"provider_secret_read": False, "provider_requests": 0, "usage": 0, "charge_cny": "0", "active_reservation": None, "task_scoped_infrastructure_terminal": True})
    pilot._write_json(artifact_root / "tail-rehearsal-summary.json", summary)
    return summary


def run_paid(root: Path, freeze: Path, artifact_root: Path, *, expected_freeze_sha256: str, expected_allocation_hash: str, acknowledgement: str, run_id: str) -> dict[str, Any]:
    with _configured():
        return pilot.run_paid_pilot(root, freeze, artifact_root, expected_freeze_sha256=expected_freeze_sha256,
                                    expected_allocation_hash=expected_allocation_hash, approved_total_hard_cap_cny=TAIL_HARD_CAP_CNY,
                                    authorization_acknowledgement=acknowledgement, run_id=run_id)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V3_CORE Goal 4 tail completion")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("freeze", "validate", "preflight", "rehearse-tail", "paid"):
        item = sub.add_parser(name); item.add_argument("--root", type=Path, required=True)
        item.add_argument("--freeze", type=Path, required=name != "freeze")
        if name == "freeze": item.add_argument("--output", type=Path, required=True)
        if name in {"preflight", "rehearse-tail", "paid"}: item.add_argument("--artifact-root", type=Path, required=True)
        if name in {"freeze", "rehearse-tail", "paid"}: item.add_argument("--run-id", required=True)
    paid = sub.choices["paid"]
    paid.add_argument("--expected-freeze-sha256", required=True); paid.add_argument("--expected-allocation-hash", required=True)
    paid.add_argument("--authorization-acknowledgement", required=True); paid.add_argument("--confirm-paid-execution", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "freeze": result = write_freeze(args.root, args.output, run_id=args.run_id)
    elif args.command == "validate": result = validate_freeze(args.root, args.freeze)
    elif args.command == "preflight": result = preflight(args.root, args.freeze, args.artifact_root)
    elif args.command == "rehearse-tail": result = rehearse_tail(args.root, args.freeze, args.artifact_root, run_id=args.run_id)
    else:
        if not args.confirm_paid_execution: raise ValueError("paid execution requires explicit confirmation")
        result = run_paid(args.root, args.freeze, args.artifact_root, expected_freeze_sha256=args.expected_freeze_sha256, expected_allocation_hash=args.expected_allocation_hash, acknowledgement=args.authorization_acknowledgement, run_id=args.run_id)
    print(json.dumps(result, sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())

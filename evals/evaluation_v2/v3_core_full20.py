"""Thin V3_CORE-only Goal 4 full-20 release wiring.

This deliberately reuses the controlled-Pilot executor, allocations, ledger,
and Artifact path.  It changes only the fixed task set and treatment cardinality.
"""
from __future__ import annotations

import argparse
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Sequence

from codepacex.capability_v3 import CapabilityV3Flag
from evals.evaluation_v2 import capability_v3_pilot as pilot, full_replay

FREEZE_NAME = "v3-core-full-20-freeze.json"


@contextmanager
def _configured() -> Any:
    """Scope the Pilot globals so importing this wrapper cannot alter Pilot callers."""
    previous = (
        pilot.PILOT_TASK_IDS,
        pilot.TREATMENTS,
        pilot.EXPERIMENT_NAME,
        pilot.FREEZE_NAME,
        pilot.validate_freeze,
    )
    pilot.PILOT_TASK_IDS = full_replay.GOAL4_ORDER
    pilot.TREATMENTS = (CapabilityV3Flag.V3_CORE,)
    pilot.EXPERIMENT_NAME = "capability-v3-core-goal4-full-20"
    pilot.FREEZE_NAME = FREEZE_NAME
    pilot.validate_freeze = validate_freeze
    try:
        yield
    finally:
        (
            pilot.PILOT_TASK_IDS,
            pilot.TREATMENTS,
            pilot.EXPERIMENT_NAME,
            pilot.FREEZE_NAME,
            pilot.validate_freeze,
        ) = previous


def freeze_payload(root: Path, *, bound_main_commit: str | None = None, run_id: str) -> dict[str, Any]:
    """Build the V3-only contract while retaining the proven V3 executor path."""
    with _configured():
        payload = pilot.freeze_payload(
            root,
            bound_main_commit=bound_main_commit,
            run_id=run_id,
            approved_total_hard_cap_cny="250.000000",
        )
    payload["execution_order"] = "strictly_serial_v3_core_only"
    payload["fairness_contract"]["only_treatment_difference"] = (
        "not_applicable_v3_core_only_longitudinal_comparison"
    )
    payload["fairness_contract"]["comparison"] = (
        "historical_goal4_reference_vs_current_v3_core_only"
    )
    return payload


def write_freeze(root: Path, output: Path, *, run_id: str) -> dict[str, str]:
    payload = freeze_payload(root, run_id=run_id)
    output.mkdir(parents=True, exist_ok=False)
    pilot._write_json(output / FREEZE_NAME, payload)
    pricing = root / full_replay.PRICING_PATH
    (output / "pricing-snapshot.json").write_bytes(pricing.read_bytes())
    return {
        "freeze_sha256": pilot._sha256(output / FREEZE_NAME),
        "pricing_snapshot_sha256": payload["budget_contract"]["pricing_snapshot_sha256"],
        "runtime_hash": payload["runtime_hash"],
        "task_list_sha256": payload["task_list_sha256"],
        "allocation_hash": payload["allocation_binding"]["allocation_hash"],
    }


def validate_freeze(root: Path, freeze: Path) -> dict[str, Any]:
    actual = pilot._read_json(freeze / FREEZE_NAME)
    binding = actual.get("allocation_binding")
    if not isinstance(binding, dict):
        raise ValueError("V3_CORE full-20 Freeze is missing its allocation binding")
    expected = freeze_payload(
        root,
        bound_main_commit=str(actual.get("bound_main_commit", "")),
        run_id=str(binding.get("internal_run_id", "")),
    )
    if actual != expected:
        raise ValueError("V3_CORE full-20 Freeze differs from its canonical contract")
    pricing = freeze / "pricing-snapshot.json"
    if not pricing.is_file() or pricing.read_bytes() != (root / full_replay.PRICING_PATH).read_bytes():
        raise ValueError("V3_CORE full-20 pricing snapshot differs from the frozen repository file")
    return {
        "valid": True,
        "freeze_sha256": pilot._sha256(freeze / FREEZE_NAME),
        **pilot.write_freeze_identities(actual),
    }


def preflight(root: Path, freeze: Path, artifact_root: Path) -> dict[str, Any]:
    with _configured():
        return pilot.run_preflight(root, freeze, artifact_root)


def rehearse(root: Path, freeze: Path, preflight_summary: Path, artifact_root: Path) -> dict[str, Any]:
    with _configured():
        return pilot.rehearse(root, freeze, preflight_summary, artifact_root)


def rehearse_entry(root: Path, freeze: Path, preflight_summary: Path, artifact_root: Path) -> dict[str, Any]:
    with _configured():
        return pilot.rehearse_execution_entry(root, freeze, preflight_summary, artifact_root)


def run_paid(root: Path, freeze: Path, artifact_root: Path, *, expected_freeze_sha256: str,
             expected_allocation_hash: str, acknowledgement: str, run_id: str) -> dict[str, Any]:
    with _configured():
        return pilot.run_paid_pilot(root, freeze, artifact_root,
            expected_freeze_sha256=expected_freeze_sha256,
            expected_allocation_hash=expected_allocation_hash,
            approved_total_hard_cap_cny="250.000000",
            authorization_acknowledgement=acknowledgement, run_id=run_id)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V3_CORE-only Goal 4 full-20 contract")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("freeze", "validate", "preflight", "rehearse", "entry", "paid"):
        command = sub.add_parser(name); command.add_argument("--root", type=Path, required=True)
        command.add_argument("--freeze", type=Path, required=name != "freeze")
        command.add_argument("--output", type=Path) if name == "freeze" else None
        command.add_argument("--artifact-root", type=Path) if name in {"preflight", "rehearse", "entry", "paid"} else None
        command.add_argument("--preflight-summary", type=Path) if name in {"rehearse", "entry"} else None
        command.add_argument("--run-id") if name in {"freeze", "paid"} else None
    paid = sub.choices["paid"]; paid.add_argument("--expected-freeze-sha256"); paid.add_argument("--expected-allocation-hash"); paid.add_argument("--authorization-acknowledgement"); paid.add_argument("--confirm-paid-execution", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "freeze": result = write_freeze(args.root, args.output, run_id=args.run_id)
    elif args.command == "validate": result = validate_freeze(args.root, args.freeze)
    elif args.command == "preflight": result = preflight(args.root, args.freeze, args.artifact_root)
    elif args.command == "rehearse": result = rehearse(args.root, args.freeze, args.preflight_summary, args.artifact_root)
    elif args.command == "entry": result = rehearse_entry(args.root, args.freeze, args.preflight_summary, args.artifact_root)
    else:
        if not args.confirm_paid_execution: raise ValueError("paid execution requires explicit confirmation")
        result = run_paid(args.root, args.freeze, args.artifact_root, expected_freeze_sha256=args.expected_freeze_sha256, expected_allocation_hash=args.expected_allocation_hash, acknowledgement=args.authorization_acknowledgement, run_id=args.run_id)
    print(json.dumps(result, sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())

"""Thin V3_CORE-only Goal 4 full-20 release wiring.

This deliberately reuses the controlled-Pilot executor, allocations, ledger,
and Artifact path.  It changes only the fixed task set and treatment cardinality.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from codepacex.capability_v3 import CapabilityV3Flag
from evals.evaluation_v2 import capability_v3_pilot as pilot, full_replay

FREEZE_NAME = "v3-core-full-20-freeze.json"


def _configure() -> None:
    pilot.PILOT_TASK_IDS = full_replay.GOAL4_ORDER
    pilot.TREATMENTS = (CapabilityV3Flag.V3_CORE,)
    pilot.EXPERIMENT_NAME = "capability-v3-core-goal4-full-20"
    pilot.FREEZE_NAME = FREEZE_NAME


def write_freeze(root: Path, output: Path, *, run_id: str) -> dict[str, str]:
    _configure()
    result = pilot.write_freeze(root, output, run_id=run_id, approved_total_hard_cap_cny="250.000000")
    return result


def validate_freeze(root: Path, freeze: Path) -> dict[str, Any]:
    _configure(); return pilot.validate_freeze(root, freeze)


def preflight(root: Path, freeze: Path, artifact_root: Path) -> dict[str, Any]:
    _configure(); return pilot.run_preflight(root, freeze, artifact_root)


def rehearse(root: Path, freeze: Path, preflight_summary: Path, artifact_root: Path) -> dict[str, Any]:
    _configure(); return pilot.rehearse(root, freeze, preflight_summary, artifact_root)


def rehearse_entry(root: Path, freeze: Path, preflight_summary: Path, artifact_root: Path) -> dict[str, Any]:
    _configure(); return pilot.rehearse_execution_entry(root, freeze, preflight_summary, artifact_root)


def run_paid(root: Path, freeze: Path, artifact_root: Path, *, expected_freeze_sha256: str,
             expected_allocation_hash: str, acknowledgement: str, run_id: str) -> dict[str, Any]:
    _configure()
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

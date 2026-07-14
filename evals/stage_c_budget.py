"""Derive non-transferable Stage C budget limits from retained Pilot usage."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from evals.costing import load_pricing, pricing_snapshot_hash
from evals.goal2_studies import load_studies
from evals.mcp_study import load_study, top_level_trial_count
from evals.paid_gate import (
    BudgetLedger,
    StageCBudgetAllocation,
    _money,
    allocation_hash,
    authorization_hash,
    ledger_fingerprint,
    load_authorization,
    stage_c_category,
)

SAFETY_RESERVE_RATIO = Decimal("0.15")
FORECAST_MULTIPLIER = Decimal("2")
DISABLED_CATEGORIES = {"swe", "long_session"}
NON_STAGE_C_PREFIXES = {"pilot"}


def formal_trial_counts(*, studies_path: Path, mcp_study_path: Path) -> dict[str, int]:
    studies = load_studies(studies_path)
    mcp_study, mcp_tasks = load_study(mcp_study_path)
    return {
        "swe": 0,
        "mcp": top_level_trial_count(mcp_study, mcp_tasks),
        "retention": len(studies.retention.session_seeds) * len(studies.retention.profiles),
        "permission": (
            len(studies.permission.tasks) * studies.permission.repetitions
            * len(studies.permission.strategies)
        ),
        "multi_agent": (
            len(studies.multi_agent.tasks) * studies.multi_agent.repetitions
            * len(studies.multi_agent.modes)
        ),
        "long_session": 0,
    }


def derive_allocation(
    *, ledger: BudgetLedger, authorization_path: Path, pricing_path: Path,
    studies_path: Path, mcp_study_path: Path,
) -> StageCBudgetAllocation:
    if ledger.active_reservation is not None:
        raise ValueError("cannot allocate Stage C budget with an active reservation")
    authorization = load_authorization(authorization_path)
    pricing = load_pricing(pricing_path)
    if authorization.pricing_snapshot_hash != pricing_snapshot_hash(pricing):
        raise ValueError("authorization and frozen pricing snapshot differ")
    if ledger.authorization_hash != authorization_hash(authorization):
        raise ValueError("ledger must be rebound to the allocation authorization")
    request_total = _money(sum(item.actual_cny for item in ledger.request_charges))
    settlement_total = _money(sum(item.actual_cny for item in ledger.settlements))
    if ledger.spent_cny != request_total or request_total != settlement_total:
        raise ValueError("cannot allocate from an internally inconsistent ledger")
    counts = formal_trial_counts(studies_path=studies_path, mcp_study_path=mcp_study_path)
    costs: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    trials: dict[str, set[str]] = defaultdict(set)
    for charge in ledger.request_charges:
        prefix = charge.trial_id.split("/", 1)[0]
        if prefix in NON_STAGE_C_PREFIXES:
            continue
        category = stage_c_category(charge.trial_id)
        costs[category] += charge.actual_cny
        trials[category].add(charge.trial_id)
    limits: dict[str, Decimal] = {}
    for category, formal_trials in counts.items():
        if category in DISABLED_CATEGORIES:
            limits[category] = Decimal("0")
            continue
        observed_trials = len(trials[category])
        if observed_trials == 0:
            raise ValueError(f"cannot forecast {category} without retained Pilot usage")
        limits[category] = _money(
            costs[category] / Decimal(observed_trials)
            * Decimal(formal_trials) * FORECAST_MULTIPLIER
        )
    safety_reserve = _money(authorization.authorized_total_cny * SAFETY_RESERVE_RATIO)
    spendable_total = authorization.authorized_total_cny - safety_reserve
    return StageCBudgetAllocation(
        experiment_commit=authorization.experiment_commit,
        pricing_snapshot_hash=pricing_snapshot_hash(pricing),
        baseline_ledger_sha256=ledger_fingerprint(ledger),
        baseline_authorization_hash=ledger.authorization_hash,
        baseline_spent_cny=ledger.spent_cny,
        baseline_request_charge_count=len(ledger.request_charges),
        baseline_settlement_count=len(ledger.settlements),
        baseline_rebind_count=len(ledger.authorization_rebinds),
        safety_reserve_cny=safety_reserve,
        spendable_total_cny=spendable_total,
        category_limits_cny=limits,
    )


def write_allocation(path: Path, allocation: StageCBudgetAllocation) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(allocation.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Derive Goal 2 Stage C category budgets")
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--pricing", type=Path, required=True)
    parser.add_argument("--studies", type=Path, default=Path("evals/goal2/studies.yaml"))
    parser.add_argument("--mcp-study", type=Path, default=Path("evals/goal2/mcp_study.yaml"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        ledger = BudgetLedger.model_validate_json(args.ledger.read_text(encoding="utf-8"))
        allocation = derive_allocation(
            ledger=ledger, authorization_path=args.authorization, pricing_path=args.pricing,
            studies_path=args.studies, mcp_study_path=args.mcp_study,
        )
        write_allocation(args.output, allocation)
        print(json.dumps({
            "allocation": str(args.output), "allocation_hash": allocation_hash(allocation),
            "category_limits_cny": {
                key: str(value) for key, value in allocation.category_limits_cny.items()
            },
            "safety_reserve_cny": str(allocation.safety_reserve_cny),
        }, sort_keys=True))
        return 0
    except (OSError, ValueError) as exc:
        print(f"Stage C budget error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""P3-A paired-Pilot freeze and zero-provider readiness.

This module intentionally has no paid execution command.  It derives its
identity inputs from the committed Evaluation V2 freeze and only writes
pre-registered P3-A evidence.  P3-B needs a separate explicit authorization.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from evals.benchmark import canonical_hash
from evals.costing import load_pricing, pricing_snapshot_hash
from evals.evaluation_v2 import full_replay
from evals.paid_gate import worst_case_reservation


SCHEMA_VERSION = 1
EXPERIMENT_NAME = "p3a-strict-paired-pilot"
BOUND_MAIN_COMMIT = "9e076874894ccf155d990fa8a176b2191e258652"
ARTIFACT_DIRECTORY = Path("evals/evaluation_v2/p3a_paired_pilot")
FREEZE_NAME = "p3a-paired-pilot-freeze.json"
MANIFEST_NAME = "8-run-manifest.json"
TREATMENT_ORDER_NAME = "treatment-order-manifest.json"
BUDGET_NAME = "budget-proposal.json"
PARENT_NAME = "parent-authorization-draft.json"
CHILDREN_NAME = "child-allocation-drafts.json"
SCHEMA_NAME = "paired-artifact-schema.json"
READINESS_NAME = "zero-provider-readiness.json"
REQUEST_CEILING = 40
RETRY = 0
FALLBACK = False
SAFETY_RESERVE_CNY = Decimal("0.000001")

# The order is pre-registered, deliberately alternates the first treatment,
# and is the only source of task order for this module.
P3A_TASK_ORDER = (
    ("beetbox__beets-5457", "V2_CONTROL", "V3_CORE"),
    ("deepset-ai__haystack-8489", "V3_CORE", "V2_CONTROL"),
    ("dynaconf__dynaconf-1249", "V2_CONTROL", "V3_CORE"),
    ("delgan__loguru-1297", "V3_CORE", "V2_CONTROL"),
)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tasks(root: Path) -> dict[str, dict[str, Any]]:
    return {item["instance_id"]: item for item in full_replay.load_tasks(root)}


def task_runs(root: Path) -> list[dict[str, Any]]:
    """Materialize the exact eight task-run identities in registered order."""
    tasks = _tasks(root)
    expected_ids = {item[0] for item in P3A_TASK_ORDER}
    if not expected_ids.issubset(tasks):
        raise ValueError("P3-A task is absent from the formal Goal 4 payload")
    runs: list[dict[str, Any]] = []
    for pair_index, (instance_id, first, second) in enumerate(P3A_TASK_ORDER, start=1):
        task = tasks[instance_id]
        for treatment in (first, second):
            ordinal = len(runs) + 1
            runs.append({
                "ordinal": ordinal,
                "pair_index": pair_index,
                "task_run_id": f"p3a-{ordinal:02d}-{instance_id}-{treatment}",
                "instance_id": instance_id,
                "repo": task["repo"],
                "base_commit": task["base_commit"],
                "problem_statement_sha256": hashlib.sha256(
                    task["problem_statement"].encode("utf-8")
                ).hexdigest(),
                "treatment": treatment,
                "expected_artifact_path": f"runs/{ordinal:02d}-{treatment}/tasks/{instance_id}",
            })
    if len(runs) != 8 or len({item["task_run_id"] for item in runs}) != 8:
        raise AssertionError("P3-A requires exactly eight unique task-run identities")
    return runs


def _formal_identities(root: Path) -> dict[str, Any]:
    """Project all model, Prompt, Provider, evaluator and Pricing fields."""
    formal = full_replay.freeze_payload(root)
    pricing = load_pricing(root / full_replay.PRICING_PATH)
    return {
        "model": {"model_id": formal["provider_contract"]["model_id"]},
        "prompt": {
            "construction": full_replay.control_canary.payload_contract(root)["prompt_construction"],
            "system_instruction_sha256": formal["runtime_contract"]["system_instruction_sha256"],
        },
        "provider": dict(formal["provider_contract"]),
        "official_evaluator": dict(formal["official_evaluator"]),
        "tools_and_permissions": dict(formal["runtime_contract"]),
        "pricing": {
            "path": str(full_replay.PRICING_PATH),
            "snapshot_sha256": pricing_snapshot_hash(pricing),
        },
    }


def budget_proposal(root: Path) -> dict[str, Any]:
    """Use frozen Pricing plus selected Goal 4 cost evidence; authorize nothing."""
    pricing = load_pricing(root / full_replay.PRICING_PATH)
    one_request = worst_case_reservation(
        pricing,
        maximum_requests=1,
        maximum_input_tokens_per_request=full_replay.MAX_INPUT_TOKENS,
        maximum_output_tokens_per_request=full_replay.MAX_OUTPUT_TOKENS,
    )
    per_run = one_request * REQUEST_CEILING
    hard_cap = per_run * 8
    selected = {item["instance_id"]: item for item in full_replay._baseline_rows(root)}
    historical = [
        Decimal(str(selected[instance_id]["goal4_selected_terminal_cost_cny"]))
        for instance_id, _first, _second in P3A_TASK_ORDER
    ]
    expected = sum(historical, Decimal("0")) * 2
    # Conservative is the larger historical paired envelope or 50% of the
    # theoretical ceiling.  The hard cap always remains the frozen worst case.
    conservative = max(expected * Decimal("1.5"), hard_cap * Decimal("0.5"))
    return {
        "schema_version": SCHEMA_VERSION,
        "currency": "CNY",
        "pricing_snapshot_path": str(full_replay.PRICING_PATH),
        "pricing_snapshot_sha256": pricing_snapshot_hash(pricing),
        "historical_source": "Goal 4 selected terminal cost rows committed in full_replay payloads",
        "historical_selected_cost_cny_by_task": {
            instance_id: str(cost)
            for (instance_id, _first, _second), cost in zip(P3A_TASK_ORDER, historical)
        },
        "one_request_theoretical_exposure_cny": str(one_request),
        "per_run_theoretical_exposure_cny": str(per_run),
        "expected_proposal_cny": str(expected),
        "conservative_proposal_cny": str(conservative),
        "hard_cap_proposal_cny": str(hard_cap),
        "safety_reserve_cny": str(SAFETY_RESERVE_CNY),
        "request_ceiling_per_run": REQUEST_CEILING,
        "derivation": "expected=2*sum(selected Goal 4 costs for the four tasks); conservative=max(1.5*expected, 50%*hard-cap); hard-cap=8*40*frozen per-request worst case",
        "authorization": "proposal_only_requires_new_explicit_P3_B_paid_authorization",
    }


def _allocation_drafts(runs: Sequence[Mapping[str, Any]], budget: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    parent_id = "p3a-paired-pilot-parent-authorization-draft"
    parent = {
        "status": "draft_not_authorized",
        "authorization_id": parent_id,
        "bound_main_commit": BOUND_MAIN_COMMIT,
        "hard_cap_proposal_cny": budget["hard_cap_proposal_cny"],
        "safety_reserve_cny": budget["safety_reserve_cny"],
        "requires_new_explicit_paid_authorization": True,
    }
    children = []
    for run in runs:
        core = {
            "parent_authorization_id": parent_id,
            "task_run_id": run["task_run_id"],
            "instance_id": run["instance_id"],
            "treatment": run["treatment"],
            "expected_artifact_path": run["expected_artifact_path"],
            "theoretical_ceiling_cny": budget["per_run_theoretical_exposure_cny"],
        }
        children.append({
            **core,
            "status": "draft_not_authorized",
            "child_allocation_id": f"{parent_id}-run-{run['ordinal']:02d}",
            "child_allocation_sha256": canonical_hash(core),
        })
    return parent, children


def paired_artifact_schema() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "comparison_key": ["pair_index", "instance_id", "repo", "base_commit", "problem_statement_sha256"],
        "required_treatments": ["V2_CONTROL", "V3_CORE"],
        "required_result_fields": [
            "task_run_id", "pair_index", "instance_id", "treatment", "provider_requests",
            "usage", "charge_cny", "provider_secret_read", "artifact_path", "terminal_status",
        ],
        "merge_rule": "exactly one V2_CONTROL and one V3_CORE record per identical comparison key; reject all other joins",
        "v2_contract": "V2_CONTROL has no V3 Advice or V3 activation Artifact requirement",
        "v3_contract": "V3_CORE records Advice/activation expectation and raw V3 Artifact requirement",
    }


def merge_paired_results(results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Fail closed unless every paired result has exactly one record per treatment."""
    grouped: dict[tuple[Any, ...], dict[str, Mapping[str, Any]]] = {}
    for result in results:
        key = tuple(result[name] for name in paired_artifact_schema()["comparison_key"])
        treatment = str(result["treatment"])
        if treatment not in {"V2_CONTROL", "V3_CORE"} or treatment in grouped.setdefault(key, {}):
            raise ValueError("paired result has an invalid or duplicate treatment identity")
        grouped[key][treatment] = result
    merged = []
    for key, pair in grouped.items():
        if set(pair) != {"V2_CONTROL", "V3_CORE"}:
            raise ValueError("paired result is missing its strict counterpart")
        merged.append({"comparison_key": list(key), "V2_CONTROL": pair["V2_CONTROL"], "V3_CORE": pair["V3_CORE"]})
    return merged


def freeze_payload(root: Path) -> dict[str, Any]:
    runs = task_runs(root)
    identities = _formal_identities(root)
    budget = budget_proposal(root)
    parent, children = _allocation_drafts(runs, budget)
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_name": EXPERIMENT_NAME,
        "status": "frozen_zero_provider_readiness_only",
        "bound_main_commit": BOUND_MAIN_COMMIT,
        "source_formal_freeze": {
            "path": str(full_replay.COMMITTED_FREEZE),
            "sha256": _sha256(root / full_replay.COMMITTED_FREEZE),
        },
        "task_runs": runs,
        "task_runs_sha256": canonical_hash(runs),
        "treatment_order": [
            {"pair_index": index, "instance_id": task, "order": [first, second]}
            for index, (task, first, second) in enumerate(P3A_TASK_ORDER, start=1)
        ],
        "execution_contract": {
            "strict_serial": True,
            "request_ceiling_per_run": REQUEST_CEILING,
            "retry": RETRY,
            "fallback": FALLBACK,
            "only_treatment_difference": "treatment",
            "paid_execution": False,
            "automatic_retry_rerun_or_continuation": False,
        },
        "frozen_identities": identities,
        "budget_proposal": budget,
        "parent_authorization_draft": parent,
        "child_allocation_drafts": children,
        "paired_artifact_schema": paired_artifact_schema(),
        "p3_b": "blocked_pending_new_explicit_paid_authorization",
    }


def validate_freeze(root: Path, frozen: Mapping[str, Any]) -> None:
    expected = freeze_payload(root)
    if dict(frozen) != expected:
        raise ValueError("P3-A freeze differs from its canonical formal configuration projection")
    runs = frozen["task_runs"]
    if len(runs) != 8 or len({item["task_run_id"] for item in runs}) != 8:
        raise ValueError("P3-A freeze lacks eight unique task runs")
    if len(frozen["child_allocation_drafts"]) != 8:
        raise ValueError("P3-A freeze lacks eight child allocation drafts")


def readiness_payload(root: Path, frozen: Mapping[str, Any]) -> dict[str, Any]:
    validate_freeze(root, frozen)
    run_records = []
    for run in frozen["task_runs"]:
        is_v3 = run["treatment"] == "V3_CORE"
        run_records.append({
            "task_run_id": run["task_run_id"],
            "pair_index": run["pair_index"],
            "instance_id": run["instance_id"],
            "repo": run["repo"],
            "base_commit": run["base_commit"],
            "problem_statement_sha256": run["problem_statement_sha256"],
            "treatment": run["treatment"],
            "artifact_path": run["expected_artifact_path"],
            "terminal_status": "zero_provider_readiness_only",
            "provider_requests": 0,
            "usage": 0,
            "charge_cny": "0",
            "provider_secret_read": False,
            "paid_execution": False,
            "v3_advice_expected": is_v3,
            "v3_activation_artifact_required": is_v3,
        })
    # Exercise the strict merge wiring on the zero-provider records themselves.
    merged = merge_paired_results(run_records)
    return {
        "schema_version": SCHEMA_VERSION,
        "freeze_sha256": canonical_hash(frozen),
        "status": "passed_zero_provider_readiness",
        "paid_jobs": "skipped",
        "provider_requests": 0,
        "usage": 0,
        "charge_cny": "0",
        "provider_secret_read": False,
        "task_run_count": len(run_records),
        "unique_task_run_count": len({item["task_run_id"] for item in run_records}),
        "paired_result_merge_count": len(merged),
        "run_records": run_records,
        "p3_b": "blocked_pending_new_explicit_paid_authorization",
    }


def write_artifacts(root: Path, output: Path) -> dict[str, str]:
    """Write deterministic P3-A evidence without reading a Secret or dispatching."""
    if output.exists():
        raise ValueError("refusing to overwrite P3-A readiness Artifact")
    frozen = freeze_payload(root)
    readiness = readiness_payload(root, frozen)
    output.mkdir(parents=True)
    _write_json(output / FREEZE_NAME, frozen)
    _write_json(output / MANIFEST_NAME, {"task_runs": frozen["task_runs"]})
    _write_json(output / TREATMENT_ORDER_NAME, {"treatment_order": frozen["treatment_order"]})
    _write_json(output / BUDGET_NAME, frozen["budget_proposal"])
    _write_json(output / PARENT_NAME, frozen["parent_authorization_draft"])
    _write_json(output / CHILDREN_NAME, {"child_allocation_drafts": frozen["child_allocation_drafts"]})
    _write_json(output / SCHEMA_NAME, frozen["paired_artifact_schema"])
    _write_json(output / READINESS_NAME, readiness)
    return {"freeze_sha256": _sha256(output / FREEZE_NAME), "readiness_sha256": _sha256(output / READINESS_NAME)}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P3-A zero-provider paired-Pilot readiness")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(write_artifacts(args.root.resolve(), args.output.resolve()), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

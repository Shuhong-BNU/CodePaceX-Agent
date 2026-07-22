"""Freeze-only contracts for the Stage C paired Goal 4 rerun.

This module deliberately has no Provider client, inference, or evaluator import.
It compiles auditable, Agent-safe Stage C inputs and validates only deterministic
fixtures. A later, separately authorized workflow must bind an immutable commit
before it can add any paid execution capability.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

from codepacex.experiments import ExperimentProfile
from evals.benchmark import canonical_hash
from evals.costing import load_pricing, pricing_snapshot_hash
from evals.paid_gate import worst_case_reservation


SOURCE_GOAL4_MATRIX_SHA256 = "9ff16e850b92a6eb0bd1338cb85253a605fdfb0e0aa77180488382eca353972a"
GOAL4_ARTIFACT_ID = "8496125148"
GOAL4_ARCHIVE_SHA256 = "8b9309a9ee03b068bf96e69afd50ecc2c18e4a70046dc1ae99359310dc70c6c8"
GOAL4_REPORT_SHA256 = "a404d82ec17c93471b842a4139e6d3f6350c672e8edf5744b57e16820e1c1a38"
GOAL4_FREEZE_COMMIT = "75a1eca465913e1c5be81e58eba89bc4d1cd8853"
STAGE_B_MERGE_COMMIT = "a6b401082e220665bc29d681ebdc9fca1c08ac82"
GOAL4_FINAL_RUN = "29830820618"
EVALUATOR_COMMIT = "ad79b850f15e33992e96f03f6e97f05ddf9aa0be"
PRICING_PATH = Path("evals/goal2/pricing_bailian_qwen37_max_2026-07-13.json")
MAX_REQUESTS = 40
MAX_INPUT_TOKENS = 128_000
MAX_OUTPUT_TOKENS = 8_192
MAX_REASONING_TOKENS = 6_144
PHASE_1_CAP = Decimal("80")
CUMULATIVE_CAP = Decimal("250")

PHASE_1_IDS = (
    "aws-cloudformation__cfn-lint-3749",
    "aws-cloudformation__cfn-lint-3764",
    "beetbox__beets-5457",
    "beetbox__beets-5495",
    "deepset-ai__haystack-8489",
    "beancount__beancount-931",
)
PHASE_2_IDS = (
    "beeware__briefcase-2075",
    "beeware__briefcase-2085",
    "bridgecrewio__checkov-6893",
    "bridgecrewio__checkov-6895",
    "conan-io__conan-17092",
    "conan-io__conan-17102",
    "cyclotruc__gitingest-115",
    "cyclotruc__gitingest-134",
    "deepset-ai__haystack-8525",
    "delgan__loguru-1297",
    "delgan__loguru-1306",
    "dynaconf__dynaconf-1225",
    "dynaconf__dynaconf-1249",
    "instructlab__instructlab-2540",
)
ALL_IDS = PHASE_1_IDS + PHASE_2_IDS
SCORABLE = frozenset({"resolved", "unresolved"})
TERMINAL = SCORABLE | {"budget_blocked", "infrastructure_error", "not_run"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stage_c_profile() -> ExperimentProfile:
    """The only treatment difference from Goal 4 is explicit Stage B validation."""
    return ExperimentProfile(
        tool_loading="deferred",
        compression_profile="recovery_v1",
        permission_strategy="session_allow",
        agent_mode="single",
        validation_mode="stage_b",
    )


def parse_goal4_evidence_index(path: Path) -> list[dict[str, Any]]:
    """Read the published selected-terminal table without importing hidden traces."""
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [item.strip() for item in line.strip().strip("|").split("|")]
        if len(cells) != 6 or cells[0] in {"Instance", "---"}:
            continue
        instance_id, batch, bucket, status, requests, cost = cells
        if status not in SCORABLE:
            continue
        rows.append({
            "instance_id": instance_id,
            "goal4_batch": batch,
            "size_bucket": bucket,
            "goal4_status": status,
            "goal4_requests": int(requests),
            "goal4_selected_terminal_cost_cny": cost,
        })
    if tuple(item["instance_id"] for item in rows) != ALL_IDS:
        raise ValueError("Goal 4 Evidence Index no longer matches the Stage C 20-task order")
    if len(rows) != 20 or len({item["instance_id"] for item in rows}) != 20:
        raise ValueError("Goal 4 Evidence Index must contain exactly 20 unique scorable rows")
    return rows


def _require_goal4_identity(root: Path) -> None:
    report = (root / "evals/GOAL4_FINAL_REPORT.md").read_text(encoding="utf-8")
    claims = json.loads((root / "evals/claims.goal4.json").read_text(encoding="utf-8"))
    required = (
        GOAL4_FINAL_RUN, GOAL4_ARTIFACT_ID, GOAL4_ARCHIVE_SHA256,
        GOAL4_REPORT_SHA256, GOAL4_FREEZE_COMMIT, SOURCE_GOAL4_MATRIX_SHA256,
        "GOAL4_ACCEPTED", "4 / 16",
    )
    if any(value not in report for value in required):
        raise ValueError("Goal 4 published identity is incomplete")
    if claims.get("status") != "verified" or claims.get("matrix_sha256") != SOURCE_GOAL4_MATRIX_SHA256:
        raise ValueError("Goal 4 Claims are not the accepted immutable baseline")


def matrix_payload(root: Path) -> dict[str, Any]:
    _require_goal4_identity(root)
    baseline = parse_goal4_evidence_index(root / "evals/GOAL4_EVIDENCE_INDEX.md")
    return {
        "schema_version": 1,
        "stage": "C",
        "source_goal4_matrix_sha256": SOURCE_GOAL4_MATRIX_SHA256,
        "source_goal4_freeze_commit": GOAL4_FREEZE_COMMIT,
        "task_order_source": "GOAL4_EVIDENCE_INDEX.md selected-terminal order",
        "tasks": [
            {
                "ordinal": index,
                "instance_id": row["instance_id"],
                "phase": "phase_1" if index <= len(PHASE_1_IDS) else "phase_2",
            }
            for index, row in enumerate(baseline, 1)
        ],
    }


def baseline_payload(root: Path) -> dict[str, Any]:
    _require_goal4_identity(root)
    rows = parse_goal4_evidence_index(root / "evals/GOAL4_EVIDENCE_INDEX.md")
    payload = {
        "schema_version": 1,
        "source": {
            "goal4_final_run": GOAL4_FINAL_RUN,
            "artifact_id": GOAL4_ARTIFACT_ID,
            "artifact_archive_sha256": GOAL4_ARCHIVE_SHA256,
            "artifact_final_report_sha256": GOAL4_REPORT_SHA256,
            "goal4_freeze_commit": GOAL4_FREEZE_COMMIT,
            "source_goal4_matrix_sha256": SOURCE_GOAL4_MATRIX_SHA256,
            "claims_valid": True,
            "claims_verified": True,
            "evidence_index_sha256": _sha256(root / "evals/GOAL4_EVIDENCE_INDEX.md"),
        },
        "rows": rows,
        "limitations": [
            "The 20 tasks were reused from Goal 4 after diagnostic failure analysis.",
            "Goal 4 is the historical control, not a concurrent A/B arm.",
            "Provider and time drift can confound descriptive paired attribution.",
        ],
    }
    return {**payload, "baseline_snapshot_sha256": canonical_hash(payload)}


def budget_contract(root: Path) -> dict[str, Any]:
    pricing = load_pricing(root / PRICING_PATH)
    per_request = worst_case_reservation(
        pricing, maximum_requests=1,
        maximum_input_tokens_per_request=MAX_INPUT_TOKENS,
        maximum_output_tokens_per_request=MAX_OUTPUT_TOKENS,
    )
    full_path = per_request * MAX_REQUESTS
    return {
        "currency": "CNY",
        "pricing_snapshot_path": str(PRICING_PATH),
        "pricing_snapshot_hash": pricing_snapshot_hash(pricing),
        "maximum_requests_per_instance": MAX_REQUESTS,
        "maximum_input_tokens_per_request": MAX_INPUT_TOKENS,
        "maximum_output_tokens_per_request": MAX_OUTPUT_TOKENS,
        "maximum_reasoning_tokens_per_request": MAX_REASONING_TOKENS,
        "per_request_maximum_reservation_cny": str(per_request),
        "phase_1_authorization_cap_cny": str(PHASE_1_CAP),
        "cumulative_authorization_cap_cny": str(CUMULATIVE_CAP),
        "phase_2_remaining_formula": "250 - phase_1_combined_conservative_consumption",
        "theoretical_full_path_maximum_per_instance_cny": str(full_path),
        "theoretical_full_path_maximum_phase_1_cny": str(full_path * len(PHASE_1_IDS)),
        "theoretical_full_path_maximum_all_tasks_cny": str(full_path * len(ALL_IDS)),
        "reservation_granularity": "one_provider_request",
        "admission_rule": "phase_conservative_consumption + next_request_maximum <= authorization_cap",
        "completion_guarantee": False,
    }


def freeze_payload(root: Path) -> dict[str, Any]:
    matrix = matrix_payload(root)
    baseline = baseline_payload(root)
    profile = stage_c_profile()
    budget = budget_contract(root)
    return {
        "schema_version": 1,
        "status": "frozen_pending_separate_phase_authorization",
        "experiment_kind": "stage-c-goal4-paired-rerun",
        "stage_b_merge_commit": STAGE_B_MERGE_COMMIT,
        "approved_evaluated_commit": None,
        "workflow_commit": None,
        "source_goal4_baseline": baseline["source"],
        "stage_c_matrix_sha256": canonical_hash(matrix),
        "baseline_snapshot_sha256": baseline["baseline_snapshot_sha256"],
        "experiment_profile": profile.canonical_payload(),
        "experiment_profile_hash": profile.profile_hash(),
        "runtime_contract_hash": profile.runtime_contract_hash(),
        "provider": "bailian-qwen37-max",
        "model_id": "qwen3.7-max-2026-06-08",
        "protocol": "openai-compat",
        "official_evaluator_commit": EVALUATOR_COMMIT,
        "fallback_enabled": False,
        "automatic_retry": 0,
        "strict_serial": True,
        "one_formal_candidate_per_instance": True,
        "budget": budget,
        "phase_1_instance_ids": list(PHASE_1_IDS),
        "phase_2_instance_ids": list(PHASE_2_IDS),
        "authorization_requirements": {
            "phase_1": "separate user authorization required",
            "phase_2": "separate user authorization plus verified Phase 1 Artifact required",
            "workflow_checkout": "must be a manually approved immutable 40-character commit",
        },
    }


def agent_safe_task_payload(task: Mapping[str, Any]) -> dict[str, str]:
    """Only the selected instance identity reaches a future Agent payload boundary."""
    instance_id = task.get("instance_id")
    if not isinstance(instance_id, str) or instance_id not in ALL_IDS:
        raise ValueError("unknown Stage C task")
    return {"instance_id": instance_id}


def validate_matrix(matrix: Mapping[str, Any]) -> None:
    tasks = matrix.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 20:
        raise ValueError("Stage C must pre-register exactly 20 tasks")
    ids = tuple(item.get("instance_id") for item in tasks if isinstance(item, Mapping))
    if ids != ALL_IDS or len(set(ids)) != 20:
        raise ValueError("Stage C task set/order differs from Goal 4")
    phases = [item.get("phase") for item in tasks if isinstance(item, Mapping)]
    if phases[:6] != ["phase_1"] * 6 or phases[6:] != ["phase_2"] * 14:
        raise ValueError("Stage C phase partition differs from its registered 6/14 split")
    if matrix.get("source_goal4_matrix_sha256") != SOURCE_GOAL4_MATRIX_SHA256:
        raise ValueError("Stage C matrix is not bound to Goal 4")


def admit_task(
    *, phase: Literal["phase_1", "phase_2"], completed_terminal_ids: Iterable[str],
    phase_conservative_consumption: Decimal, active_reservation: bool,
    authorization_cap: Decimal, next_request_maximum: Decimal,
) -> tuple[bool, str]:
    """Deterministic admission gate used before a task's first transport request."""
    done = set(completed_terminal_ids)
    required_previous = PHASE_1_IDS if phase == "phase_2" else ()
    if any(instance_id not in done for instance_id in required_previous):
        return False, "phase_1_terminal_evidence_incomplete"
    if active_reservation:
        return False, "active_reservation_present"
    if phase_conservative_consumption + next_request_maximum > authorization_cap:
        return False, "budget_blocked_before_transport"
    return True, "admitted"


def compile_paired_claim(
    *, matrix: Mapping[str, Any], baseline: Mapping[str, Any],
    stage_results: Mapping[str, str],
) -> dict[str, Any]:
    """Compile only evidence that exists; `not_run` never becomes unresolved."""
    validate_matrix(matrix)
    baseline_rows = baseline.get("rows")
    if not isinstance(baseline_rows, list):
        raise ValueError("Stage C baseline snapshot is invalid")
    by_baseline = {str(row.get("instance_id")): row for row in baseline_rows if isinstance(row, Mapping)}
    if set(by_baseline) != set(ALL_IDS) or not set(stage_results).issubset(set(ALL_IDS)):
        raise ValueError("paired claim task identity mismatch")
    normalized = {instance_id: stage_results.get(instance_id, "not_run") for instance_id in ALL_IDS}
    if any(value not in TERMINAL for value in normalized.values()):
        raise ValueError("Stage C result has an invalid terminal status")
    scorable_ids = [item for item, value in normalized.items() if value in SCORABLE]
    pairs = {
        "resolved_to_resolved": 0,
        "resolved_to_unresolved": 0,
        "unresolved_to_resolved": 0,
        "unresolved_to_unresolved": 0,
    }
    for instance_id in scorable_ids:
        source = str(by_baseline[instance_id]["goal4_status"])
        key = f"{source}_to_{normalized[instance_id]}"
        pairs[key] += 1
    phase_1_complete = all(normalized[item] in SCORABLE for item in PHASE_1_IDS)
    full_complete = len(scorable_ids) == len(ALL_IDS)
    return {
        "schema_version": 1,
        "claim_kind": "full_20_task_paired_result" if full_complete else (
            "phase_1_smoke_pilot" if phase_1_complete else "partial_stage_c"
        ),
        "full_claim": full_complete,
        "partial": not full_complete,
        "scorable_denominator": len(scorable_ids),
        "registered_denominator": len(ALL_IDS),
        "not_run": [item for item, value in normalized.items() if value == "not_run"],
        "budget_blocked": [item for item, value in normalized.items() if value == "budget_blocked"],
        "stage_c_resolved": sum(value == "resolved" for value in normalized.values()),
        "stage_c_unresolved": sum(value == "unresolved" for value in normalized.values()),
        "net_resolved_delta_vs_goal4": pairs["unresolved_to_resolved"] - pairs["resolved_to_unresolved"],
        "paired_transitions": pairs,
        "limitations": list(baseline.get("limitations", [])),
    }


def validate_terminal_evidence(record: Mapping[str, Any]) -> None:
    """Verify a task is safe to precede the next serial task.

    This is intentionally schema-level: it never invokes an evaluator, reads a
    patch, or inspects Provider payload content.
    """
    status = record.get("status")
    if status not in TERMINAL:
        raise ValueError("Stage C terminal status is invalid")
    if record.get("active_reservation") is not None:
        raise ValueError("terminal evidence has an active reservation")
    if status == "not_run":
        if record.get("provider_requests", 0) != 0:
            raise ValueError("not_run cannot contain Provider requests")
        return
    if status == "budget_blocked":
        if record.get("transport_started") is not False or record.get("provider_requests", 0) != 0:
            raise ValueError("budget block must occur before transport")
        if record.get("budget_blocked") is not True:
            raise ValueError("budget block lacks its durable terminal marker")
        return
    required = (
        "prediction_reference", "stdout_reference", "trace_reference",
        "validation_events_reference", "validation_summary_reference",
        "usage_reference", "charge_reference", "settlement_reference",
        "artifact_reference", "evaluator_report_reference",
    )
    if any(not isinstance(record.get(key), str) or not record[key] for key in required):
        raise ValueError("scorable terminal evidence is incomplete")
    if not isinstance(record.get("provider_requests"), int) or not 0 <= record["provider_requests"] <= MAX_REQUESTS:
        raise ValueError("terminal evidence has an invalid Provider request count")
    if record.get("secret_scan_passed") is not True:
        raise ValueError("terminal evidence lacks a clean secret scan")


def report_metric_schema() -> dict[str, None]:
    """Named metrics prevent future reports from inventing a composite score."""
    return {
        "f2p": None,
        "p2p": None,
        "provider_requests": None,
        "input_tokens": None,
        "completion_tokens": None,
        "reasoning_tokens": None,
        "verified_cost_cny": None,
        "uncertain_exposure_cny": None,
        "combined_conservative_cost_cny": None,
        "reproduced_before_edit": None,
        "reproduction_exception_code": None,
        "contract_inventory_revision_count": None,
        "target_test_after_last_edit": None,
        "regression_baseline_present": None,
        "regression_comparison_status": None,
        "new_regression_count": None,
        "checkpoint_20_acknowledged": None,
        "checkpoint_30_acknowledged": None,
        "checkpoint_36_decision": None,
        "completion_blocked_count": None,
        "invalid_completion_attempts": None,
        "final_validation_decision": None,
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        raise ValueError(f"refusing to overwrite frozen output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_freeze_bundle(root: Path, output_dir: Path) -> dict[str, Any]:
    root, output_dir = root.resolve(), output_dir.resolve()
    matrix = matrix_payload(root)
    baseline = baseline_payload(root)
    freeze = freeze_payload(root)
    validate_matrix(matrix)
    _write_json(output_dir / "stage_c_matrix.json", {**matrix, "stage_c_matrix_sha256": canonical_hash(matrix)})
    _write_json(output_dir / "stage_c_baseline.json", baseline)
    _write_json(output_dir / "stage_c_freeze.json", freeze)
    _write_json(output_dir / "stage_c_pricing_reference.json", freeze["budget"])
    _write_json(output_dir / "phase_1_authorization.template.json", {
        "schema_version": 1, "authorization_required": True, "paid_execution": False,
        "phase": "phase_1", "authorization_cap_cny": str(PHASE_1_CAP),
        "freeze_sha256": canonical_hash(freeze), "authorization_hash": None,
    })
    _write_json(output_dir / "phase_2_authorization.template.json", {
        "schema_version": 1, "authorization_required": True, "paid_execution": False,
        "phase": "phase_2", "authorization_cap_formula": "250 - phase_1_combined_conservative_consumption",
        "freeze_sha256": canonical_hash(freeze), "authorization_hash": None,
        "required_phase_1_artifact": None,
    })
    return freeze


def validate_frozen_bundle(root: Path, output_dir: Path) -> dict[str, Any]:
    """Reject drift in committed Freeze material before any workflow action."""
    matrix = json.loads((output_dir / "stage_c_matrix.json").read_text(encoding="utf-8"))
    baseline = json.loads((output_dir / "stage_c_baseline.json").read_text(encoding="utf-8"))
    freeze = json.loads((output_dir / "stage_c_freeze.json").read_text(encoding="utf-8"))
    pricing = json.loads((output_dir / "stage_c_pricing_reference.json").read_text(encoding="utf-8"))
    expected_matrix = matrix_payload(root)
    expected_baseline = baseline_payload(root)
    expected_freeze = freeze_payload(root)
    validate_matrix(matrix)
    if matrix != {**expected_matrix, "stage_c_matrix_sha256": canonical_hash(expected_matrix)}:
        raise ValueError("Stage C committed matrix differs from generated baseline")
    if baseline != expected_baseline:
        raise ValueError("Stage C committed baseline differs from Goal 4 evidence")
    if freeze != expected_freeze or pricing != expected_freeze["budget"]:
        raise ValueError("Stage C Freeze contract drift")
    return {
        "valid": True,
        "provider_requests": 0,
        "paid_execution": False,
        "formal_stage_c_trial": False,
    }


def validate_immutable_commit(value: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("paid execution requires a manually approved immutable commit")


def validate_phase_1_artifact(manifest: Mapping[str, Any]) -> None:
    """Validate the continuation prerequisites without reading raw task traces."""
    required_hashes = ("artifact_archive_sha256", "report_sha256", "ledger_sha256")
    if not isinstance(manifest.get("artifact_id"), str) or not manifest["artifact_id"].isdigit():
        raise ValueError("Phase 1 Artifact ID is missing")
    if any(not isinstance(manifest.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", manifest[key]) for key in required_hashes):
        raise ValueError("Phase 1 immutable Artifact hashes are missing")
    if manifest.get("active_reservation") is not None:
        raise ValueError("Phase 1 ledger has an active reservation")
    if tuple(manifest.get("phase_1_instance_ids", ())) != PHASE_1_IDS:
        raise ValueError("Phase 1 Artifact task set differs from the frozen prefix")
    if tuple(manifest.get("phase_2_instance_ids", ())) != PHASE_2_IDS:
        raise ValueError("Phase 2 continuation task set differs from the frozen suffix")
    statuses = manifest.get("terminal_statuses")
    if not isinstance(statuses, Mapping) or set(statuses) != set(PHASE_1_IDS):
        raise ValueError("Phase 1 terminal statuses are incomplete")
    if any(value not in SCORABLE for value in statuses.values()):
        raise ValueError("Phase 2 requires six scorable Phase 1 outcomes")


def reject_paid_execution(*, freeze_commit: str, authorization_identity: str) -> None:
    """Make a Freeze workflow fail closed even when an operator flips its input."""
    validate_immutable_commit(freeze_commit)
    if not authorization_identity.strip():
        raise ValueError("separate phase authorization identity is required")
    raise RuntimeError("paid Stage C execution is deliberately unavailable in the Freeze workflow")


def zero_provider_dry_run(root: Path, output_dir: Path, *, phase: Literal["phase_1", "phase_2"]) -> dict[str, Any]:
    freeze = freeze_payload(root.resolve())
    instance_ids = PHASE_1_IDS if phase == "phase_1" else PHASE_2_IDS
    payload = {
        "schema_version": 1,
        "phase": phase,
        "provider_requests": 0,
        "paid_execution": False,
        "formal_stage_c_trial": False,
        "freeze_sha256": canonical_hash(freeze),
        "task_statuses": {item: "not_run" for item in instance_ids},
        "next_request_maximum_cny": freeze["budget"]["per_request_maximum_reservation_cny"],
    }
    _write_json(output_dir.resolve() / f"{phase}-dry-run.json", payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Zero-provider Stage C Freeze tools")
    parser.add_argument("command", choices=["freeze", "validate", "dry-run", "compile-claims", "validate-phase-1-artifact", "reject-paid"])
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=["phase_1", "phase_2"], default="phase_1")
    parser.add_argument("--phase-1-artifact", type=Path)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--freeze-commit", default="")
    parser.add_argument("--authorization-identity", default="")
    args = parser.parse_args(argv)
    if args.command == "freeze":
        payload = write_freeze_bundle(args.root, args.output_dir)
    elif args.command == "validate":
        payload = validate_frozen_bundle(args.root, args.output_dir)
    elif args.command == "validate-phase-1-artifact":
        if args.phase_1_artifact is None:
            raise ValueError("--phase-1-artifact is required")
        validate_phase_1_artifact(json.loads(args.phase_1_artifact.read_text(encoding="utf-8")))
        payload = {"valid": True, "provider_requests": 0, "paid_execution": False, "formal_stage_c_trial": False}
    elif args.command == "compile-claims":
        if args.results is None:
            raise ValueError("--results is required")
        bundle = args.output_dir
        matrix = json.loads((bundle / "stage_c_matrix.json").read_text(encoding="utf-8"))
        baseline = json.loads((bundle / "stage_c_baseline.json").read_text(encoding="utf-8"))
        results = json.loads(args.results.read_text(encoding="utf-8"))
        if not isinstance(results, dict):
            raise ValueError("Stage C claim results must be an object")
        payload = compile_paired_claim(matrix=matrix, baseline=baseline, stage_results=results)
    elif args.command == "reject-paid":
        reject_paid_execution(
            freeze_commit=args.freeze_commit,
            authorization_identity=args.authorization_identity,
        )
        raise AssertionError("unreachable")
    else:
        payload = zero_provider_dry_run(args.root, args.output_dir, phase=args.phase)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

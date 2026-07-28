"""Frozen six-task Capability V3 controlled-Pilot contract.

This is a deliberately thin adapter over :mod:`full_replay`: it reuses the
published safe Goal 4 payload projection, environment plans, live executor and
ProviderRequestBudget gate.  It adds no Provider transport to readiness paths.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from codepacex.capability_v3 import CapabilityV3Config, CapabilityV3Controller, CapabilityV3Flag
from evals.benchmark import canonical_hash, current_git_commit
from evals.costing import load_pricing, pricing_snapshot_hash
from evals.evaluation_v2 import control_canary, full_replay
from evals.paid_gate import (
    BudgetAuthorization, BudgetLedger, PaidRunGate, StageCBudgetAllocation,
    TaskRunBudgetAllocation, TaskRunBudgetIdentity, allocation_hash, authorization_hash,
    ledger_fingerprint, task_run_allocation_hash, worst_case_reservation,
)


SCHEMA_VERSION = 1
EXPERIMENT_NAME = "capability-v3-controlled-pilot"
PILOT_TASK_IDS = (
    "aws-cloudformation__cfn-lint-3749",
    "dynaconf__dynaconf-1249",
    "deepset-ai__haystack-8489",
    "delgan__loguru-1306",
    "conan-io__conan-17102",
    "bridgecrewio__checkov-6895",
)
TREATMENTS = (CapabilityV3Flag.V2_CONTROL, CapabilityV3Flag.V3_CORE)
FREEZE_NAME = "capability-v3-controlled-pilot-freeze.json"
COMMIT = re.compile(r"^[0-9a-f]{40}$")
RUN_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,80}$")
ALLOCATION_SAFETY_RESERVE_CNY = Decimal("0.000001")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def task_runs(root: Path) -> list[dict[str, Any]]:
    """Return the fixed adjacent V2 then V3 order from safe Goal 4 payloads."""
    safe_tasks = {item["instance_id"]: item for item in full_replay.load_tasks(root)}
    if not set(PILOT_TASK_IDS).issubset(safe_tasks):
        raise ValueError("controlled Pilot task is absent from the safe Goal 4 payload")
    runs: list[dict[str, Any]] = []
    for pair_index, instance_id in enumerate(PILOT_TASK_IDS, start=1):
        for flag in TREATMENTS:
            ordinal = len(runs) + 1
            task = safe_tasks[instance_id]
            runs.append({
                "ordinal": ordinal,
                "pair_index": pair_index,
                "task_run_id": f"{ordinal:02d}-{instance_id}-{flag.value}",
                "instance_id": instance_id,
                "repo": task["repo"],
                "base_commit": task["base_commit"],
                "agent_visible_payload_sha256": canonical_hash(task),
                "capability_v3_flag": flag.value,
                "expected_artifact_path": f"runs/{ordinal:02d}-{flag.value}/tasks/{instance_id}",
            })
    if len(runs) != 12:
        raise AssertionError("controlled Pilot must materialize exactly twelve task-runs")
    return runs


def _common_runtime(root: Path) -> dict[str, Any]:
    runtime = dict(full_replay.runtime_contract(root))
    runtime.pop("capability_v3_feature_flag", None)
    runtime["capability_v3_pilot_source_sha256"] = _sha256(root / "evals/evaluation_v2/capability_v3_pilot.py")
    runtime["capability_v3_artifact_fidelity_contract"] = (
        "V3_CORE raw controller Artifact is retained at the frozen task-root path and fail-closed before Candidate export-v1"
    )
    return runtime


def budget_contract(root: Path) -> dict[str, Any]:
    pricing = load_pricing(root / full_replay.PRICING_PATH)
    one = worst_case_reservation(
        pricing, maximum_requests=1,
        maximum_input_tokens_per_request=full_replay.MAX_INPUT_TOKENS,
        maximum_output_tokens_per_request=full_replay.MAX_OUTPUT_TOKENS,
    )
    per_run = one * full_replay.MAX_REQUESTS_PER_TASK
    total = per_run * len(PILOT_TASK_IDS) * len(TREATMENTS)
    return {
        "currency": "CNY",
        "pricing_snapshot_path": str(full_replay.PRICING_PATH),
        "pricing_snapshot_sha256": pricing_snapshot_hash(pricing),
        "per_request_theoretical_exposure_cny": str(one),
        "per_run_theoretical_exposure_cny": str(per_run),
        "total_theoretical_exposure_cny": str(total),
        "provider_request_ceiling_per_run": full_replay.MAX_REQUESTS_PER_TASK,
        "maximum_input_tokens_per_request": full_replay.MAX_INPUT_TOKENS,
        "maximum_output_tokens_per_request": full_replay.MAX_OUTPUT_TOKENS,
        "maximum_reasoning_tokens_per_request": full_replay.MAX_REASONING_TOKENS,
        "rolling_reservation": "one_provider_request",
        "reservation_policy": "reserve before every request; reject a request that exceeds a run ceiling or the total authorization; settle/cancel before the next strictly serial run",
        "stop_condition": "stop immediately on an infrastructure, accounting, active-reservation, or Provider-usage-contract failure; capability outcomes remain recorded but do not authorize retry or fallback",
    }


def _safe_run_id(run_id: str) -> str:
    if not RUN_ID.fullmatch(run_id) or Path(run_id).name != run_id:
        raise ValueError("controlled Pilot requires a fresh safe Run ID")
    return run_id


def _task_run_identity(value: Mapping[str, Any]) -> TaskRunBudgetIdentity:
    return TaskRunBudgetIdentity.model_validate({key: value[key] for key in (
        "task_run_id", "task_run_allocation_id", "task_run_allocation_hash",
        "instance_id", "treatment", "expected_artifact_path",
    )})


def _authorization(frozen: Mapping[str, Any], pricing_hash: str, commit: str) -> BudgetAuthorization:
    total = Decimal(str(frozen["budget_contract"]["total_theoretical_exposure_cny"]))
    return BudgetAuthorization(
        authorized_total_cny=total, stage_limits_cny={"A": total, "B": total, "C": total},
        pricing_snapshot_hash=pricing_hash, experiment_commit=commit,
        authorized_at="single-capability-v3-controlled-pilot", authorized_by="user",
    )


def _allocation_binding(frozen: Mapping[str, Any], *, run_id: str) -> tuple[BudgetAuthorization, BudgetLedger, StageCBudgetAllocation, dict[str, Any]]:
    run_id = _safe_run_id(run_id)
    total = Decimal(str(frozen["budget_contract"]["total_theoretical_exposure_cny"]))
    pricing_hash = str(frozen["budget_contract"]["pricing_snapshot_sha256"])
    authorization = _authorization(frozen, pricing_hash, str(frozen["bound_main_commit"]))
    ledger = BudgetLedger(
        authorization_hash=authorization_hash(authorization), updated_at="controlled-pilot-allocation-prepared",
    )
    per_run = Decimal(str(frozen["budget_contract"]["per_run_theoretical_exposure_cny"]))
    parent_allocation_id = f"capability-v3-{run_id}-stage-c"
    task_run_allocations: list[TaskRunBudgetAllocation] = []
    for run in frozen["task_runs"]:
        canonical = {
            "task_run_id": run["task_run_id"],
            "task_run_allocation_id": f"{parent_allocation_id}-task-run-{run['ordinal']:02d}",
            "instance_id": run["instance_id"],
            "treatment": run["capability_v3_flag"],
            "expected_artifact_path": run["expected_artifact_path"],
            "execution_run_id": f"{run_id}-{run['ordinal']:02d}-{run['capability_v3_flag']}",
            "theoretical_ceiling_cny": str(per_run),
        }
        task_run_allocations.append(TaskRunBudgetAllocation.model_validate({
            **canonical, "task_run_allocation_hash": task_run_allocation_hash(canonical),
        }))
    allocation = StageCBudgetAllocation(
        allocation_id=parent_allocation_id,
        experiment_commit=str(frozen["bound_main_commit"]), pricing_snapshot_hash=pricing_hash,
        baseline_ledger_sha256=ledger_fingerprint(ledger),
        baseline_authorization_hash=authorization_hash(authorization),
        baseline_spent_cny=Decimal("0"), baseline_request_charge_count=0,
        baseline_settlement_count=0, baseline_budget_block_count=0, baseline_rebind_count=0,
        safety_reserve_cny=ALLOCATION_SAFETY_RESERVE_CNY,
        spendable_total_cny=total - ALLOCATION_SAFETY_RESERVE_CNY,
        category_limits_cny={
            "swe": total - ALLOCATION_SAFETY_RESERVE_CNY, "mcp": Decimal("0"),
            "retention": Decimal("0"), "permission": Decimal("0"),
            "multi_agent": Decimal("0"), "long_session": Decimal("0"),
        },
        task_run_allocations=task_run_allocations,
    )
    binding = {
        "allocation_id": allocation.allocation_id,
        "allocation_hash": allocation_hash(allocation),
        "internal_run_id": run_id,
        "bound_main_commit": frozen["bound_main_commit"],
        "pricing_snapshot_sha256": pricing_hash,
        "runtime_hash": frozen["runtime_hash"],
        "task_list_sha256": frozen["task_list_sha256"],
        "task_runs_sha256": frozen["task_runs_sha256"],
        "authorized_total_cny": str(total),
        "spendable_total_cny": str(allocation.spendable_total_cny),
        "safety_reserve_cny": str(allocation.safety_reserve_cny),
        "allocation_path": "controlled-pilot-stage-c-allocation.json",
        "ledger_binding": "allocation_hash is durable before the first reservation",
        "task_run_allocations": [item.model_dump(mode="json") for item in task_run_allocations],
        "task_run_binding": (
            "each task-run identity is an explicit parent-allocation ceiling binding; "
            "it is not a separate ledger or pre-funded pool"
        ),
    }
    return authorization, ledger, allocation, binding


def freeze_payload(root: Path, *, bound_main_commit: str | None = None, run_id: str) -> dict[str, Any]:
    head = current_git_commit(root)
    bound = bound_main_commit or head
    if not COMMIT.fullmatch(bound):
        raise ValueError("bound main commit must be a full commit SHA")
    if bound != head:
        raise ValueError("controlled Pilot Freeze must be generated from its bound main commit")
    runs = task_runs(root)
    common = _common_runtime(root)
    task_list = [{key: item[key] for key in (
        "instance_id", "repo", "base_commit", "agent_visible_payload_sha256",
    )} for item in runs[::2]]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "experiment_name": EXPERIMENT_NAME,
        "status": "frozen_pending_single_paid_authorization",
        "bound_main_commit": bound,
        "goal4_source": {
            "freeze_commit": full_replay.GOAL4_FREEZE_COMMIT,
            "accepted_result": "4 resolved / 16 unresolved; 20/20 scorable",
            "accepted_baseline_modified": False,
        },
        "task_list": task_list,
        "task_list_sha256": canonical_hash(task_list),
        "task_runs": runs,
        "task_runs_sha256": canonical_hash(runs),
        "treatments": [item.value for item in TREATMENTS],
        "execution_order": "strictly_serial_adjacent_pairs_v2_then_v3",
        "fairness_contract": {
            "fixed": [
                "bound_main_commit", "task_snapshot", "official_evaluator", "model",
                "provider_protocol", "system_prompt", "tool_schemas", "permission_strategy",
                "request_ceiling", "timeout", "dependency_environment", "pricing_snapshot",
                "retry=0", "fallback=false",
            ],
            "only_treatment_difference": "capability_v3_flag",
        },
        "runtime_contract": common,
        "runtime_hash": canonical_hash(common),
        "provider_contract": {
            "provider": "bailian-qwen37-max",
            "protocol": "openai-compat",
            "base_url": control_canary.PROVIDER_BASE_URL,
            "provider_secret_name": "BAILIAN_API_KEY",
            "model_id": "qwen3.7-max-2026-06-08",
            "fallback_enabled": False,
            "retry": 0,
            "strict_serial": True,
        },
        "budget_contract": budget_contract(root),
        "paid_execution_default": False,
        "provider_secret_presence_only": True,
        "gold_hidden_access_forbidden": True,
    }
    _authorization, _ledger, _allocation, binding = _allocation_binding(payload, run_id=run_id)
    payload["allocation_binding"] = binding
    return payload


def write_freeze(root: Path, output: Path, *, run_id: str) -> dict[str, str]:
    payload = freeze_payload(root, run_id=run_id)
    output.mkdir(parents=True, exist_ok=False)
    _write_json(output / FREEZE_NAME, payload)
    pricing = root / full_replay.PRICING_PATH
    (output / "pricing-snapshot.json").write_bytes(pricing.read_bytes())
    return {
        "freeze_sha256": _sha256(output / FREEZE_NAME),
        "pricing_snapshot_sha256": payload["budget_contract"]["pricing_snapshot_sha256"],
        "runtime_hash": payload["runtime_hash"],
        "task_list_sha256": payload["task_list_sha256"],
        "allocation_hash": payload["allocation_binding"]["allocation_hash"],
    }


def validate_freeze(root: Path, freeze: Path) -> dict[str, Any]:
    actual = _read_json(freeze / FREEZE_NAME)
    binding = actual.get("allocation_binding")
    if not isinstance(binding, dict):
        raise ValueError("controlled Pilot Freeze is missing its allocation binding")
    expected = freeze_payload(
        root, bound_main_commit=str(actual.get("bound_main_commit", "")),
        run_id=str(binding.get("internal_run_id", "")),
    )
    if actual != expected:
        raise ValueError("controlled Pilot Freeze differs from its canonical contract")
    pricing = freeze / "pricing-snapshot.json"
    if not pricing.is_file() or pricing.read_bytes() != (root / full_replay.PRICING_PATH).read_bytes():
        raise ValueError("controlled Pilot pricing snapshot differs from the frozen repository file")
    return {"valid": True, "freeze_sha256": _sha256(freeze / FREEZE_NAME), **write_freeze_identities(actual)}


def write_freeze_identities(payload: Mapping[str, Any]) -> dict[str, str]:
    return {
        "pricing_snapshot_sha256": str(payload["budget_contract"]["pricing_snapshot_sha256"]),
        "runtime_hash": str(payload["runtime_hash"]),
        "task_list_sha256": str(payload["task_list_sha256"]),
        "allocation_hash": str(payload["allocation_binding"]["allocation_hash"]),
    }


def run_preflight(root: Path, freeze: Path, artifact_root: Path) -> dict[str, Any]:
    validate_freeze(root, freeze)
    if artifact_root.exists():
        raise ValueError("refusing to overwrite controlled Pilot preflight Artifact")
    frozen = _read_json(freeze / FREEZE_NAME)
    tasks = {item["instance_id"]: item for item in full_replay.load_tasks(root)}
    plans = full_replay._task_environment_contract(root)
    artifact_root.mkdir(parents=True)
    results = [
        full_replay.preflight_task(tasks[instance_id], plans[instance_id], work_root=artifact_root / "tasks")
        for instance_id in PILOT_TASK_IDS
    ]
    summary = {
        "schema_version": SCHEMA_VERSION, "paid_execution": False, "provider_requests": 0,
        "usage": 0, "charge_cny": "0", "provider_secret_read": False,
        "freeze_sha256": _sha256(freeze / FREEZE_NAME),
        "task_list_sha256": frozen["task_list_sha256"], "tasks": results,
        "ready_count": sum(item["environment_status"] == "ready" for item in results),
        "passed": len(results) == len(PILOT_TASK_IDS) and all(item["environment_status"] == "ready" for item in results),
    }
    _write_json(artifact_root / "preflight-summary.json", summary)
    return summary


def _fresh_gate(root: Path, freeze: Path, artifact_root: Path, acknowledgement: str, *, run_id: str) -> PaidRunGate:
    if not acknowledgement:
        raise ValueError("controlled Pilot requires a non-empty authorization acknowledgement")
    frozen = _read_json(freeze / FREEZE_NAME)
    pricing = load_pricing(freeze / "pricing-snapshot.json")
    authorization, ledger, allocation, binding = _allocation_binding(frozen, run_id=run_id)
    if authorization.pricing_snapshot_hash != pricing_snapshot_hash(pricing) or authorization.experiment_commit != current_git_commit(root):
        raise ValueError("controlled Pilot allocation binding does not match its live freeze identity")
    if binding != frozen["allocation_binding"]:
        raise ValueError("controlled Pilot allocation hash or Run ID does not match the freeze")
    authorization_path, ledger_path = artifact_root / "authorization.json", artifact_root / "ledger.json"
    allocation_path = artifact_root / binding["allocation_path"]
    _write_json(authorization_path, authorization.model_dump(mode="json"))
    _write_json(artifact_root / "authorization-acknowledgement.json", {"acknowledgement": acknowledgement})
    ledger = ledger.model_copy(update={"allocation_hash": allocation_hash(allocation), "updated_at": "controlled-pilot-entry"})
    _write_json(ledger_path, ledger.model_dump(mode="json"))
    _write_json(allocation_path, allocation.model_dump(mode="json"))
    _write_json(artifact_root / "controlled-pilot-execution-contract.json", {
        "freeze_sha256": _sha256(freeze / FREEZE_NAME), "allocation_binding": binding,
        "allocation_hash": allocation_hash(allocation), "ledger_allocation_hash": ledger.allocation_hash,
        "task_run_allocations": binding["task_run_allocations"],
        "paid_execution": False, "provider_requests": 0, "provider_secret_read": False,
    })
    return PaidRunGate(root=root, authorization_path=authorization_path, ledger_path=ledger_path,
        allocation_path=allocation_path, pricing_path=freeze / "pricing-snapshot.json", pricing=pricing, stage="C")


def _write_rehearsal_capability_v3_artifact(location: Path, run: Mapping[str, Any]) -> dict[str, Any]:
    """Create deterministic raw V3 evidence at the same nested Artifact path.

    This is an Artifact collection rehearsal, not an Agent or Provider run. The
    streaming Agent lifecycle is separately covered by its zero-provider test.
    """
    root = location / "capability-v3"
    controller = CapabilityV3Controller(
        CapabilityV3Config.from_flag(CapabilityV3Flag.V3_CORE),
        task_id=str(run["instance_id"]), base_commit=str(run["base_commit"]), state_dir=root,
    )
    controller.begin_run(
        task_id=str(run["instance_id"]), base_commit=str(run["base_commit"]),
        feature_flag=CapabilityV3Flag.V3_CORE.value,
    )
    candidate = root / "candidate-rehearsal.patch"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(
        "diff --git a/.codepacex-rehearsal b/.codepacex-rehearsal\n"
        "--- a/.codepacex-rehearsal\n+++ b/.codepacex-rehearsal\n"
        "+zero-provider-artifact-rehearsal\n",
        encoding="utf-8",
    )
    controller.observe_diff(
        diff_text=candidate.read_text(encoding="utf-8"), changed_files=(".codepacex-rehearsal",),
        patch_path=candidate,
    )
    controller.observe_test_result(passed=True, test_evidence_id="zero-provider-artifact-rehearsal")
    selected = controller.finalize("zero_provider_artifact_rehearsal")
    if selected is None:
        raise AssertionError("zero-provider V3 rehearsal did not retain its Candidate")
    (root / "final.patch").write_bytes(candidate.read_bytes())
    controller.write_artifact(root)
    fidelity = control_canary._validate_capability_v3_artifact(
        task_root=location, instance_id=str(run["instance_id"]), treatment=CapabilityV3Flag.V3_CORE,
    )
    if not fidelity["valid"]:
        raise AssertionError(f"zero-provider V3 rehearsal Artifact invalid: {fidelity['errors']}")
    fidelity["candidate_sha256"] = _sha256(candidate)
    fidelity["candidate_matches_final_patch"] = fidelity["candidate_sha256"] == fidelity["final_patch_sha256"]
    if not fidelity["candidate_matches_final_patch"]:
        raise AssertionError("zero-provider V3 rehearsal Candidate differs from final.patch")
    return fidelity


def rehearse(root: Path, freeze: Path, preflight_summary: Path, artifact_root: Path) -> dict[str, Any]:
    """Materialize all twelve Artifact paths and flag hand-offs without transport."""
    identities = validate_freeze(root, freeze)
    preflight = _read_json(preflight_summary)
    if not preflight.get("passed") or preflight.get("ready_count") != len(PILOT_TASK_IDS):
        raise ValueError("controlled Pilot preflight is not ready")
    if artifact_root.exists():
        raise ValueError("refusing to overwrite controlled Pilot rehearsal Artifact")
    frozen = _read_json(freeze / FREEZE_NAME)
    artifact_root.mkdir(parents=True)
    allocation_by_run = {
        item["task_run_id"]: item
        for item in frozen["allocation_binding"]["task_run_allocations"]
    }
    run_artifacts = []
    for run in frozen["task_runs"]:
        location = artifact_root / run["expected_artifact_path"]
        location.mkdir(parents=True)
        payload = {"runtime_contract": {**frozen["runtime_contract"], "capability_v3_feature_flag": run["capability_v3_flag"]}, "provider_contract": {**frozen["provider_contract"], "provider_secret_name": "BAILIAN_API_KEY"}}
        pilot = control_canary._paid_pilot_config(payload)
        if pilot.feature_flags.get("capability_v3_flag") != run["capability_v3_flag"]:
            raise AssertionError("Capability V3 flag was not passed into the Agent Pilot config")
        artifact = {
            "task_run_id": run["task_run_id"], "instance_id": run["instance_id"],
            "capability_v3_flag": run["capability_v3_flag"], "agent_feature_flags": pilot.feature_flags,
            "paid_execution": False, "provider_requests": 0, "usage": 0, "charge_cny": "0",
            "provider_secret_read": False, "candidate_sha256": None,
            "status": "zero_provider_rehearsal_only",
            "task_run_allocation": allocation_by_run[run["task_run_id"]],
        }
        if run["capability_v3_flag"] == CapabilityV3Flag.V3_CORE.value:
            artifact["capability_v3_treatment_fidelity"] = _write_rehearsal_capability_v3_artifact(location, run)
        _write_json(location / "task-run-contract.json", artifact)
        run_artifacts.append({"task_run_id": run["task_run_id"], "artifact": str(location.relative_to(artifact_root))})
    v3_coverage = [
        json.loads((artifact_root / run["expected_artifact_path"] / "task-run-contract.json").read_text(encoding="utf-8"))["capability_v3_treatment_fidelity"]
        for run in frozen["task_runs"] if run["capability_v3_flag"] == CapabilityV3Flag.V3_CORE.value
    ]
    if len(v3_coverage) != len(PILOT_TASK_IDS) or not all(item["valid"] for item in v3_coverage):
        raise AssertionError("zero-provider rehearsal lacks complete V3 Artifact coverage")
    summary = {
        "schema_version": SCHEMA_VERSION, "paid_execution": False, "provider_requests": 0,
        "usage": 0, "charge_cny": "0", "provider_secret_read": False,
        "freeze_sha256": identities["freeze_sha256"], "run_artifacts": run_artifacts,
        "flag_handoffs_verified": [item.value for item in TREATMENTS],
        "v3_evidence_coverage": v3_coverage,
        "v3_evidence_coverage_count": len(v3_coverage),
        "completed": len(run_artifacts) == 12,
    }
    _write_json(artifact_root / "controlled-pilot-rehearsal-summary.json", summary)
    return summary


def rehearse_execution_entry(root: Path, freeze: Path, preflight_summary: Path, artifact_root: Path) -> dict[str, Any]:
    """Exercise the real Stage C entry gate for all frozen runs without transport."""
    identities = validate_freeze(root, freeze)
    preflight = _read_json(preflight_summary)
    if not preflight.get("passed") or preflight.get("ready_count") != len(PILOT_TASK_IDS):
        raise ValueError("controlled Pilot preflight is not ready")
    if artifact_root.exists():
        raise ValueError("refusing to create a duplicate controlled Pilot allocation Artifact")
    frozen = _read_json(freeze / FREEZE_NAME)
    artifact_root.mkdir(parents=True)
    run_id = str(frozen["allocation_binding"]["internal_run_id"])
    gate = _fresh_gate(root, freeze, artifact_root, "zero-provider-execution-entry-rehearsal", run_id=run_id)
    handoffs = []
    allocation_by_run = {
        item["task_run_id"]: _task_run_identity(item)
        for item in frozen["allocation_binding"]["task_run_allocations"]
    }
    for run in frozen["task_runs"]:
        identity = allocation_by_run[run["task_run_id"]]
        trial_id = f"swe/v2-full-20/{run_id}-{run['ordinal']:02d}-{run['capability_v3_flag']}/{run['instance_id']}"
        reservation = gate.reserve(
            trial_id, maximum_requests=1,
            maximum_input_tokens_per_request=full_replay.MAX_INPUT_TOKENS,
            maximum_output_tokens_per_request=full_replay.MAX_OUTPUT_TOKENS,
            task_run_identity=identity,
        )
        settlement = gate.cancel(reservation, reason="provider_confirmed_not_submitted")
        handoffs.append({
            "task_run_id": run["task_run_id"], "capability_v3_flag": run["capability_v3_flag"],
            "trial_id": trial_id, "reservation_cny": str(reservation.reserved_cny),
            "settlement_cny": str(settlement.actual_cny), "status": settlement.status,
            "task_run_allocation_id": identity.task_run_allocation_id,
            "task_run_allocation_hash": identity.task_run_allocation_hash,
        })
    ledger = BudgetLedger.model_validate_json(gate.ledger_path.read_text(encoding="utf-8"))
    binding = frozen["allocation_binding"]
    if ledger.active_reservation is not None or ledger.request_charges or ledger.spent_cny != 0:
        raise RuntimeError("zero-provider controlled Pilot entry rehearsal did not close cleanly")
    result = {
        "schema_version": SCHEMA_VERSION, "paid_execution": False, "provider_requests": 0,
        "usage": 0, "charge_cny": "0", "provider_secret_read": False,
        "freeze_sha256": identities["freeze_sha256"], "allocation_binding": binding,
        "ledger_allocation_hash": ledger.allocation_hash, "active_reservation": None,
        "task_runs": handoffs, "completed": len(handoffs) == len(frozen["task_runs"]),
    }
    _write_json(artifact_root / "controlled-pilot-execution-entry-rehearsal.json", result)
    return result


def run_paid_pilot(root: Path, freeze: Path, artifact_root: Path, *, expected_freeze_sha256: str, expected_allocation_hash: str, approved_total_hard_cap_cny: str, authorization_acknowledgement: str, run_id: str) -> dict[str, Any]:
    """Future-only serial executor; no caller reaches it without explicit confirmation."""
    identities = validate_freeze(root, freeze)
    frozen = _read_json(freeze / FREEZE_NAME)
    total = Decimal(str(frozen["budget_contract"]["total_theoretical_exposure_cny"]))
    binding = frozen["allocation_binding"]
    if expected_freeze_sha256 != identities["freeze_sha256"] or Decimal(approved_total_hard_cap_cny) != total:
        raise ValueError("paid authorization does not match the controlled Pilot Freeze and hard cap")
    if expected_allocation_hash != binding["allocation_hash"]:
        raise ValueError("paid authorization does not match the controlled Pilot allocation hash")
    if run_id != binding["internal_run_id"] or not run_id or Path(run_id).name != run_id or artifact_root.exists():
        raise ValueError("controlled Pilot requires a fresh safe Run ID and Artifact root")
    artifact_root.mkdir(parents=True)
    gate = _fresh_gate(root, freeze, artifact_root, authorization_acknowledgement, run_id=run_id)
    tasks = {item["instance_id"]: item for item in full_replay.load_tasks(root)}
    metadata = full_replay._task_environment_contract(root)
    allocation_by_run = {
        item["task_run_id"]: _task_run_identity(item)
        for item in binding["task_run_allocations"]
    }
    results = []
    for run in frozen["task_runs"]:
        run_root = artifact_root / "runs" / f"{run['ordinal']:02d}-{run['capability_v3_flag']}"
        expected_location = Path(run["expected_artifact_path"])
        actual_location = Path("runs") / f"{run['ordinal']:02d}-{run['capability_v3_flag']}" / "tasks" / run["instance_id"]
        if actual_location != expected_location:
            raise ValueError("controlled Pilot Artifact path does not match the frozen task-run allocation")
        runtime = {**frozen["runtime_contract"], "capability_v3_feature_flag": run["capability_v3_flag"]}
        execution_freeze = {"runtime_contract": runtime, "provider_contract": {**frozen["provider_contract"], "provider_secret_name": "BAILIAN_API_KEY"}}
        task_run_identity = allocation_by_run[run["task_run_id"]]
        result = full_replay._full_task_executor(
            root, execution_freeze, metadata, gate, run_root,
            f"{run_id}-{run['ordinal']:02d}-{run['capability_v3_flag']}",
            tasks[run["instance_id"]], task_run_identity=task_run_identity,
        )
        results.append({
            "task_run_id": run["task_run_id"], "capability_v3_flag": run["capability_v3_flag"],
            "task_run_allocation": task_run_identity.model_dump(mode="json"), **asdict(result),
        })
        ledger = BudgetLedger.model_validate_json(gate.ledger_path.read_text(encoding="utf-8"))
        if result.terminal_status not in full_replay.CAPABILITY_TERMINALS or ledger.active_reservation is not None:
            break
    ledger = BudgetLedger.model_validate_json(gate.ledger_path.read_text(encoding="utf-8"))
    summary = {
        "schema_version": SCHEMA_VERSION, "paid_execution": True, "run_id": run_id,
        "freeze_sha256": identities["freeze_sha256"], "results": results,
        "allocation_id": binding["allocation_id"], "allocation_hash": binding["allocation_hash"],
        "task_run_allocations": binding["task_run_allocations"],
        "provider_requests": len(ledger.request_charges), "usage": sum(item.input_tokens + item.output_tokens for item in ledger.request_charges),
        "charge_cny": str(ledger.spent_cny), "ledger_closed": ledger.active_reservation is None,
        "completed": len(results) == 12 and ledger.active_reservation is None,
    }
    _write_json(artifact_root / "controlled-pilot-paid-summary.json", summary)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capability V3 controlled Pilot contracts")
    sub = parser.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("freeze"); freeze.add_argument("--root", type=Path, required=True); freeze.add_argument("--output", type=Path, required=True); freeze.add_argument("--run-id", required=True)
    validate = sub.add_parser("validate"); validate.add_argument("--root", type=Path, required=True); validate.add_argument("--freeze", type=Path, required=True)
    preflight = sub.add_parser("preflight"); preflight.add_argument("--root", type=Path, required=True); preflight.add_argument("--freeze", type=Path, required=True); preflight.add_argument("--artifact-root", type=Path, required=True)
    rehearsal = sub.add_parser("rehearse"); rehearsal.add_argument("--root", type=Path, required=True); rehearsal.add_argument("--freeze", type=Path, required=True); rehearsal.add_argument("--preflight-summary", type=Path, required=True); rehearsal.add_argument("--artifact-root", type=Path, required=True)
    entry = sub.add_parser("rehearse-execution-entry"); entry.add_argument("--root", type=Path, required=True); entry.add_argument("--freeze", type=Path, required=True); entry.add_argument("--preflight-summary", type=Path, required=True); entry.add_argument("--artifact-root", type=Path, required=True)
    paid = sub.add_parser("paid-run"); paid.add_argument("--root", type=Path, required=True); paid.add_argument("--freeze", type=Path, required=True); paid.add_argument("--artifact-root", type=Path, required=True); paid.add_argument("--expected-freeze-sha256", required=True); paid.add_argument("--expected-allocation-hash", required=True); paid.add_argument("--approved-total-hard-cap-cny", required=True); paid.add_argument("--authorization-acknowledgement", required=True); paid.add_argument("--run-id", required=True); paid.add_argument("--confirm-paid-execution", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "freeze": result = write_freeze(args.root.resolve(), args.output.resolve(), run_id=args.run_id)
    elif args.command == "validate": result = validate_freeze(args.root.resolve(), args.freeze.resolve())
    elif args.command == "preflight": result = run_preflight(args.root.resolve(), args.freeze.resolve(), args.artifact_root.resolve())
    elif args.command == "rehearse": result = rehearse(args.root.resolve(), args.freeze.resolve(), args.preflight_summary.resolve(), args.artifact_root.resolve())
    elif args.command == "rehearse-execution-entry": result = rehearse_execution_entry(args.root.resolve(), args.freeze.resolve(), args.preflight_summary.resolve(), args.artifact_root.resolve())
    else:
        if not args.confirm_paid_execution:
            raise ValueError("paid execution requires --confirm-paid-execution")
        result = run_paid_pilot(args.root.resolve(), args.freeze.resolve(), args.artifact_root.resolve(), expected_freeze_sha256=args.expected_freeze_sha256, expected_allocation_hash=args.expected_allocation_hash, approved_total_hard_cap_cny=args.approved_total_hard_cap_cny, authorization_acknowledgement=args.authorization_acknowledgement, run_id=args.run_id)
    print(json.dumps(result, sort_keys=True)); return 0


if __name__ == "__main__":
    raise SystemExit(main())

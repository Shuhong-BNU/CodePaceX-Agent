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

from codepacex.capability_v3 import CapabilityV3Flag
from evals.benchmark import canonical_hash, current_git_commit
from evals.costing import load_pricing, pricing_snapshot_hash
from evals.evaluation_v2 import control_canary, full_replay
from evals.paid_gate import BudgetAuthorization, BudgetLedger, PaidRunGate, authorization_hash, worst_case_reservation


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


def freeze_payload(root: Path, *, bound_main_commit: str | None = None) -> dict[str, Any]:
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
    return {
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


def write_freeze(root: Path, output: Path) -> dict[str, str]:
    payload = freeze_payload(root)
    output.mkdir(parents=True, exist_ok=False)
    _write_json(output / FREEZE_NAME, payload)
    pricing = root / full_replay.PRICING_PATH
    (output / "pricing-snapshot.json").write_bytes(pricing.read_bytes())
    return {
        "freeze_sha256": _sha256(output / FREEZE_NAME),
        "pricing_snapshot_sha256": payload["budget_contract"]["pricing_snapshot_sha256"],
        "runtime_hash": payload["runtime_hash"],
        "task_list_sha256": payload["task_list_sha256"],
    }


def validate_freeze(root: Path, freeze: Path) -> dict[str, Any]:
    actual = _read_json(freeze / FREEZE_NAME)
    expected = freeze_payload(root, bound_main_commit=str(actual.get("bound_main_commit", "")))
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


def _fresh_gate(root: Path, freeze: Path, artifact_root: Path, acknowledgement: str) -> PaidRunGate:
    if not acknowledgement:
        raise ValueError("controlled Pilot requires a non-empty authorization acknowledgement")
    frozen = _read_json(freeze / FREEZE_NAME)
    total = Decimal(str(frozen["budget_contract"]["total_theoretical_exposure_cny"]))
    pricing = load_pricing(freeze / "pricing-snapshot.json")
    authorization = BudgetAuthorization(
        authorized_total_cny=total, stage_limits_cny={"A": total, "B": total, "C": total},
        pricing_snapshot_hash=pricing_snapshot_hash(pricing), experiment_commit=current_git_commit(root),
        authorized_at="single-capability-v3-controlled-pilot", authorized_by="user",
    )
    authorization_path, ledger_path = artifact_root / "authorization.json", artifact_root / "ledger.json"
    _write_json(authorization_path, authorization.model_dump(mode="json"))
    _write_json(artifact_root / "authorization-acknowledgement.json", {"acknowledgement": acknowledgement})
    _write_json(ledger_path, BudgetLedger(authorization_hash=authorization_hash(authorization), updated_at="controlled-pilot-start").model_dump(mode="json"))
    return PaidRunGate(root=root, authorization_path=authorization_path, ledger_path=ledger_path, pricing_path=freeze / "pricing-snapshot.json", pricing=pricing, stage="C")


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
        }
        _write_json(location / "task-run-contract.json", artifact)
        run_artifacts.append({"task_run_id": run["task_run_id"], "artifact": str(location.relative_to(artifact_root))})
    summary = {
        "schema_version": SCHEMA_VERSION, "paid_execution": False, "provider_requests": 0,
        "usage": 0, "charge_cny": "0", "provider_secret_read": False,
        "freeze_sha256": identities["freeze_sha256"], "run_artifacts": run_artifacts,
        "flag_handoffs_verified": [item.value for item in TREATMENTS], "completed": len(run_artifacts) == 12,
    }
    _write_json(artifact_root / "controlled-pilot-rehearsal-summary.json", summary)
    return summary


def run_paid_pilot(root: Path, freeze: Path, artifact_root: Path, *, expected_freeze_sha256: str, approved_total_hard_cap_cny: str, authorization_acknowledgement: str, run_id: str) -> dict[str, Any]:
    """Future-only serial executor; no caller reaches it without explicit confirmation."""
    identities = validate_freeze(root, freeze)
    frozen = _read_json(freeze / FREEZE_NAME)
    total = Decimal(str(frozen["budget_contract"]["total_theoretical_exposure_cny"]))
    if expected_freeze_sha256 != identities["freeze_sha256"] or Decimal(approved_total_hard_cap_cny) != total:
        raise ValueError("paid authorization does not match the controlled Pilot Freeze and hard cap")
    if not run_id or Path(run_id).name != run_id or artifact_root.exists():
        raise ValueError("controlled Pilot requires a fresh safe Run ID and Artifact root")
    artifact_root.mkdir(parents=True)
    gate = _fresh_gate(root, freeze, artifact_root, authorization_acknowledgement)
    tasks = {item["instance_id"]: item for item in full_replay.load_tasks(root)}
    metadata = full_replay._task_environment_contract(root)
    results = []
    for run in frozen["task_runs"]:
        run_root = artifact_root / "runs" / f"{run['ordinal']:02d}-{run['capability_v3_flag']}"
        runtime = {**frozen["runtime_contract"], "capability_v3_feature_flag": run["capability_v3_flag"]}
        execution_freeze = {"runtime_contract": runtime, "provider_contract": {**frozen["provider_contract"], "provider_secret_name": "BAILIAN_API_KEY"}}
        result = full_replay._full_task_executor(root, execution_freeze, metadata, gate, run_root, f"{run_id}-{run['ordinal']:02d}-{run['capability_v3_flag']}", tasks[run["instance_id"]])
        results.append({"task_run_id": run["task_run_id"], "capability_v3_flag": run["capability_v3_flag"], **asdict(result)})
        ledger = BudgetLedger.model_validate_json(gate.ledger_path.read_text(encoding="utf-8"))
        if result.terminal_status not in full_replay.CAPABILITY_TERMINALS or ledger.active_reservation is not None:
            break
    ledger = BudgetLedger.model_validate_json(gate.ledger_path.read_text(encoding="utf-8"))
    summary = {
        "schema_version": SCHEMA_VERSION, "paid_execution": True, "run_id": run_id,
        "freeze_sha256": identities["freeze_sha256"], "results": results,
        "provider_requests": len(ledger.request_charges), "usage": sum(item.input_tokens + item.output_tokens for item in ledger.request_charges),
        "charge_cny": str(ledger.spent_cny), "ledger_closed": ledger.active_reservation is None,
        "completed": len(results) == 12 and ledger.active_reservation is None,
    }
    _write_json(artifact_root / "controlled-pilot-paid-summary.json", summary)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capability V3 controlled Pilot contracts")
    sub = parser.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("freeze"); freeze.add_argument("--root", type=Path, required=True); freeze.add_argument("--output", type=Path, required=True)
    validate = sub.add_parser("validate"); validate.add_argument("--root", type=Path, required=True); validate.add_argument("--freeze", type=Path, required=True)
    preflight = sub.add_parser("preflight"); preflight.add_argument("--root", type=Path, required=True); preflight.add_argument("--freeze", type=Path, required=True); preflight.add_argument("--artifact-root", type=Path, required=True)
    rehearsal = sub.add_parser("rehearse"); rehearsal.add_argument("--root", type=Path, required=True); rehearsal.add_argument("--freeze", type=Path, required=True); rehearsal.add_argument("--preflight-summary", type=Path, required=True); rehearsal.add_argument("--artifact-root", type=Path, required=True)
    paid = sub.add_parser("paid-run"); paid.add_argument("--root", type=Path, required=True); paid.add_argument("--freeze", type=Path, required=True); paid.add_argument("--artifact-root", type=Path, required=True); paid.add_argument("--expected-freeze-sha256", required=True); paid.add_argument("--approved-total-hard-cap-cny", required=True); paid.add_argument("--authorization-acknowledgement", required=True); paid.add_argument("--run-id", required=True); paid.add_argument("--confirm-paid-execution", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "freeze": result = write_freeze(args.root.resolve(), args.output.resolve())
    elif args.command == "validate": result = validate_freeze(args.root.resolve(), args.freeze.resolve())
    elif args.command == "preflight": result = run_preflight(args.root.resolve(), args.freeze.resolve(), args.artifact_root.resolve())
    elif args.command == "rehearse": result = rehearse(args.root.resolve(), args.freeze.resolve(), args.preflight_summary.resolve(), args.artifact_root.resolve())
    else:
        if not args.confirm_paid_execution:
            raise ValueError("paid execution requires --confirm-paid-execution")
        result = run_paid_pilot(args.root.resolve(), args.freeze.resolve(), args.artifact_root.resolve(), expected_freeze_sha256=args.expected_freeze_sha256, approved_total_hard_cap_cny=args.approved_total_hard_cap_cny, authorization_acknowledgement=args.authorization_acknowledgement, run_id=args.run_id)
    print(json.dumps(result, sort_keys=True)); return 0


if __name__ == "__main__":
    raise SystemExit(main())

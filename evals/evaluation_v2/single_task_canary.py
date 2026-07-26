"""Isolated, one-task Evaluation V2 paid-canary contract.

The only executable task is ``aws-cloudformation__cfn-lint-3749``.  This
module deliberately consumes the committed full-20 task/environment contracts
without changing the two-task Control Canary or the full-20 runner.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from evals.benchmark import canonical_hash, current_git_commit
from evals.costing import load_pricing, pricing_snapshot_hash
from evals.evaluation_v2 import control_canary, full_replay
from evals.paid_gate import (
    BudgetAuthorization,
    BudgetLedger,
    PaidRunGate,
    ProviderRequestBudget,
    ProviderUsageContractViolationError,
    StageCBudgetAllocation,
    allocation_hash,
    authorization_hash,
    ledger_fingerprint,
    worst_case_reservation,
)


SCHEMA_VERSION = 1
TASK_ID = "aws-cloudformation__cfn-lint-3749"
PARENT_COMMIT = "0927c92264d8ebf6e042e2ebc7165d07bbaf1eb3"
PARENT_RUNTIME_HASH = "c4fd70b03b8c8f63df5f87520441a7482cc6759f44d4aa95ad96c441c1f05cfd"
PARENT_FREEZE_SHA256 = "54e2e0b5d09c1cfea238d047baa5dd567187c913e65e5d9afa1ad6bcf27f32a0"
PARENT_PRICING_HASH = "a09eb6e6955b9fb68d3e011771c948f7a14b7bbca5316a2433cab099d0b643d3"
READINESS_RUN_ID = 30163373235
READINESS_ARTIFACT_ID = 8621081736
READINESS_ARTIFACT_DIGEST = "sha256:1256700266cfa3195589548f526bf2e46f418239d42aa0b72b6ca30fbac5b236"

PRICING_PATH = full_replay.PRICING_PATH
PARENT_FREEZE_PATH = full_replay.COMMITTED_FREEZE
COMMITTED_FREEZE = Path("evals/evaluation_v2/single_task_canary_payloads/single-task-freeze.json")
WORKFLOW_PATH = Path(".github/workflows/evaluation-v2-single-task-paid-canary.yml")
MAX_REQUESTS_PER_TASK = 40
AGENT_MAX_ITERATIONS = 50
MAX_INPUT_TOKENS = 128_000
MAX_OUTPUT_TOKENS = 8_192
MAX_REASONING_TOKENS = 6_144
SAFETY_RESERVE_CNY = Decimal("0.000001")
TERMINALS = frozenset({
    "resolved", "unresolved", "agent_no_candidate", "validation_failed",
    "request_ceiling_reached", "provider_usage_contract_violation",
    "pre_agent_blocked", "agent_dispatch_missing", "host_runtime_contaminated",
    "protocol_blocked", "provider_transport_error", "evaluator_unavailable",
    "evaluator_execution_error", "evaluator_report_selection_error", "runner_error",
    "budget_blocked", "task_environment_blocked", "preflight_wiring_blocked",
})
RUNTIME_SOURCES = (
    "evals/evaluation_v2/single_task_canary.py",
    "evals/evaluation_v2/full_replay.py",
    "evals/evaluation_v2/control_canary.py",
    "evals/paid_gate.py",
    str(WORKFLOW_PATH),
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _task(root: Path) -> dict[str, Any]:
    tasks = {item["instance_id"]: item for item in full_replay.load_tasks(root)}
    if set(tasks) != set(full_replay.GOAL4_ORDER) or TASK_ID not in tasks:
        raise ValueError("parent full-20 task identity is unavailable")
    return dict(tasks[TASK_ID])


def _environment(root: Path) -> dict[str, Any]:
    contracts = full_replay._task_environment_contract(root)
    if TASK_ID not in contracts:
        raise ValueError("parent full-20 task environment identity is unavailable")
    return dict(contracts[TASK_ID])


def _budget_contract(root: Path) -> dict[str, Any]:
    pricing = load_pricing(root / PRICING_PATH)
    one = worst_case_reservation(
        pricing, maximum_requests=1,
        maximum_input_tokens_per_request=MAX_INPUT_TOKENS,
        maximum_output_tokens_per_request=MAX_OUTPUT_TOKENS,
    )
    spendable = worst_case_reservation(
        pricing, maximum_requests=MAX_REQUESTS_PER_TASK,
        maximum_input_tokens_per_request=MAX_INPUT_TOKENS,
        maximum_output_tokens_per_request=MAX_OUTPUT_TOKENS,
    )
    if spendable != Decimal("73.236480"):
        raise ValueError("single-task theoretical exposure differs from the approved contract")
    return {
        "currency": "CNY",
        "pricing_snapshot_path": str(PRICING_PATH),
        "pricing_snapshot_hash": pricing_snapshot_hash(pricing),
        "maximum_input_tokens_per_request": MAX_INPUT_TOKENS,
        "maximum_output_tokens_per_request": MAX_OUTPUT_TOKENS,
        "maximum_reasoning_tokens_per_request": MAX_REASONING_TOKENS,
        "provider_request_ceiling_per_task": MAX_REQUESTS_PER_TASK,
        "agent_max_iterations": AGENT_MAX_ITERATIONS,
        "rolling_reservation": "one_provider_request",
        "one_request_theoretical_exposure_cny": str(one),
        "single_task_spendable_cap_cny": str(spendable),
        "authorization_hard_cap_cny": str(spendable + SAFETY_RESERVE_CNY),
        "nonspendable_safety_reserve_cny": str(SAFETY_RESERVE_CNY),
        "budget_rule": "only the frozen one-task maximum exposure is spendable; the micro reserve is nonspendable",
    }


def freeze_payload(root: Path) -> dict[str, Any]:
    root = root.resolve()
    parent = full_replay.validate_contract(root)
    if parent["freeze_sha256"] != PARENT_FREEZE_SHA256 or parent["runtime_contract_sha256"] != PARENT_RUNTIME_HASH:
        raise ValueError("parent full-20 identity differs from the approved readiness contract")
    budget = _budget_contract(root)
    if budget["pricing_snapshot_hash"] != PARENT_PRICING_HASH:
        raise ValueError("pricing identity differs from the approved parent contract")
    task, environment = _task(root), _environment(root)
    runtime = {
        "schema_version": SCHEMA_VERSION,
        "source_sha256": {name: _sha256(root / name) for name in RUNTIME_SOURCES},
        "delegated_executor": "full_replay._full_task_executor",
        "usage_contract_terminal": "provider_usage_contract_violation",
        "one_formal_trial_contract": "exactly-one-fixed-task-trial-v1",
    }
    parent_freeze = _read_json(root / PARENT_FREEZE_PATH)
    payload_manifest = full_replay.build_payload_manifest(root)
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_name": "evaluation-v2-single-task-paid-canary",
        "status": "frozen_pending_explicit_single_task_authorization",
        "parent_checkout": PARENT_COMMIT,
        "parent_readiness": {
            "runtime_contract_sha256": PARENT_RUNTIME_HASH,
            "freeze_sha256": PARENT_FREEZE_SHA256,
            "pricing_snapshot_sha256": PARENT_PRICING_HASH,
            "run_id": READINESS_RUN_ID,
            "artifact_id": READINESS_ARTIFACT_ID,
            "artifact_digest": READINESS_ARTIFACT_DIGEST,
        },
        "task": task,
        "task_environment": environment,
        "task_payload_sha256": next(
            item["agent_visible_payload_sha256"]
            for item in payload_manifest["payloads"] if item["instance_id"] == TASK_ID
        ),
        "runtime_contract": runtime,
        "runtime_contract_hash": canonical_hash(runtime),
        "provider_contract": parent_freeze["provider_contract"],
        "official_evaluator": parent_freeze["official_evaluator"],
        "budget_contract": budget,
        "terminal_status_schema": sorted(TERMINALS),
        "go_no_go": {
            "paid_execution": "future separately authorized single task only",
            "stop_after": ["provider_usage_contract_violation", "provider_transport_error", "runner_error", "active_reservation"],
            "forbidden": ["second_task", "phase_a", "full_20", "retry", "continuation", "fallback"],
        },
    }


def write_freeze(root: Path, output: Path) -> dict[str, Any]:
    output = output.resolve()
    if output.exists():
        raise ValueError("refusing to overwrite a single-task canary Freeze")
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = freeze_payload(root)
    _write_json(output, payload)
    return {"freeze_sha256": _sha256(output), "runtime_contract_hash": payload["runtime_contract_hash"]}


def validate_freeze(root: Path, freeze: Path = COMMITTED_FREEZE) -> dict[str, Any]:
    root, freeze = root.resolve(), freeze.resolve()
    expected = freeze_payload(root)
    actual = _read_json(freeze)
    if actual != expected:
        raise ValueError("single-task canary Freeze differs from the current contract")
    return {"valid": True, "freeze_sha256": _sha256(freeze), "runtime_contract_hash": actual["runtime_contract_hash"], "pricing_snapshot_hash": actual["budget_contract"]["pricing_snapshot_hash"]}


def _fresh_gate(root: Path, artifact_root: Path, acknowledgement: str) -> PaidRunGate:
    if not acknowledgement:
        raise ValueError("single-task canary requires an authorization acknowledgement")
    frozen = _read_json(root / COMMITTED_FREEZE)
    budget = frozen["budget_contract"]
    pricing = load_pricing(root / PRICING_PATH)
    authorization = BudgetAuthorization(
        authorized_total_cny=Decimal(budget["authorization_hard_cap_cny"]),
        stage_limits_cny={"A": Decimal(budget["authorization_hard_cap_cny"]), "B": Decimal(budget["authorization_hard_cap_cny"]), "C": Decimal(budget["authorization_hard_cap_cny"])},
        pricing_snapshot_hash=pricing_snapshot_hash(pricing), experiment_commit=current_git_commit(root),
        authorized_at="single-task-canary", authorized_by="user",
    )
    paths = {"authorization": artifact_root / "authorization.json", "ledger": artifact_root / "ledger.json", "allocation": artifact_root / "single-task-allocation.json"}
    _write_json(paths["authorization"], authorization.model_dump(mode="json"))
    _write_json(artifact_root / "authorization-acknowledgement.json", {"acknowledgement": acknowledgement})
    ledger = BudgetLedger(authorization_hash=authorization_hash(authorization), updated_at="single-task-canary-start")
    _write_json(paths["ledger"], ledger.model_dump(mode="json"))
    spendable = Decimal(budget["single_task_spendable_cap_cny"])
    allocation = StageCBudgetAllocation(
        experiment_commit=current_git_commit(root), pricing_snapshot_hash=pricing_snapshot_hash(pricing),
        baseline_ledger_sha256=ledger_fingerprint(ledger), baseline_authorization_hash=authorization_hash(authorization),
        baseline_spent_cny=Decimal("0"), baseline_request_charge_count=0, baseline_settlement_count=0,
        baseline_budget_block_count=0, baseline_rebind_count=0, safety_reserve_cny=SAFETY_RESERVE_CNY,
        spendable_total_cny=spendable,
        category_limits_cny={"swe": spendable, "mcp": Decimal("0"), "retention": Decimal("0"), "permission": Decimal("0"), "multi_agent": Decimal("0"), "long_session": Decimal("0")},
    )
    _write_json(paths["allocation"], allocation.model_dump(mode="json"))
    return PaidRunGate(
        root=root, authorization_path=paths["authorization"], ledger_path=paths["ledger"],
        allocation_path=paths["allocation"], pricing_path=root / PRICING_PATH,
        pricing=pricing, stage="C",
    )


def run_preflight(root: Path, artifact_root: Path) -> dict[str, Any]:
    validate_freeze(root)
    if artifact_root.exists():
        raise ValueError("refusing to overwrite single-task preflight evidence")
    artifact_root.mkdir(parents=True)
    task, environment = _task(root), _environment(root)
    result = full_replay.preflight_task(task, environment, work_root=artifact_root / "tasks")
    summary = {"schema_version": SCHEMA_VERSION, "paid_execution": False, "provider_requests": 0, "usage": 0, "charge_cny": "0", "provider_secret_read": False, "task": TASK_ID, "ready_count": int(result["environment_status"] == "ready"), "tasks": [result], "passed": result["environment_status"] == "ready"}
    _write_json(artifact_root / "preflight-summary.json", summary)
    return summary


def _fake_result(root: Path, artifact_root: Path, run_id: str, scenario: str) -> tuple[control_canary.PaidTaskResult, BudgetLedger]:
    gate = _fresh_gate(root, artifact_root, f"zero-provider-{scenario}")
    trial_id = f"swe/v2-single-task/{run_id}/{TASK_ID}"
    budget = ProviderRequestBudget(gate, trial_id=trial_id, maximum_input_tokens_per_request=MAX_INPUT_TOKENS, maximum_output_tokens_per_request=MAX_OUTPUT_TOKENS, maximum_reasoning_tokens_per_request=MAX_REASONING_TOKENS, maximum_provider_requests_per_trial=MAX_REQUESTS_PER_TASK)
    reservation = budget.reserve_before_request()
    task_root = artifact_root / "tasks" / TASK_ID
    task_root.mkdir(parents=True, exist_ok=True)
    patch = "diff --git a/shadow.txt b/shadow.txt\n+single-task deterministic candidate\n"
    patch_path = task_root / "candidate.patch"; patch_path.write_text(patch, encoding="utf-8")
    candidate_sha = _sha256(patch_path)
    if scenario == "usage-contract-violation":
        usage = {"prompt_tokens": 1, "completion_tokens": 8197, "total_tokens": 8198, "completion_tokens_details": {"reasoning_tokens": 6144, "text_tokens": 8197}}
        try:
            budget.settle_after_usage(reservation, usage)
        except ProviderUsageContractViolationError as exc:
            violation = exc.violation.model_dump(mode="json")
        else:
            raise AssertionError("expected fake usage contract violation")
        result = control_canary.PaidTaskResult(TASK_ID, "failed", "exported_nonempty", "not_run", "not_run", "error", "completed", terminal_status="provider_usage_contract_violation", provider_requests=1, charge_cny=str(gate.trial_accounting(trial_id)["actual_cny"]), candidate_sha256=candidate_sha, workspace_diff_sha256=candidate_sha, candidate_diff_identity=True, live_executor_invoked=True, agent_dispatch_started=True, provider_client_initialized=True, model_response_observed=True, settlement_count=1, trial_id=trial_id, failure_classification="provider_usage_contract_violation", provider_usage_contract_violation=violation)
    else:
        settlement = budget.settle_after_usage(reservation, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "completion_tokens_details": {"reasoning_tokens": 0, "text_tokens": 1}})
        result = control_canary.PaidTaskResult(TASK_ID, "completed_with_candidate", "exported_nonempty", "executed", "completed", "completed", "completed", terminal_status="unresolved", provider_requests=1, charge_cny=str(settlement.actual_cny), candidate_sha256=candidate_sha, workspace_diff_sha256=candidate_sha, candidate_diff_identity=True, live_executor_invoked=True, agent_dispatch_started=True, provider_client_initialized=True, model_response_observed=True, settlement_count=1, trial_id=trial_id)
    _write_json(task_root / "task-result.json", asdict(result))
    return result, BudgetLedger.model_validate_json(gate.ledger_path.read_text(encoding="utf-8"))


def _loopback_agent_provider_dispatch(
    root: Path, artifact_root: Path, run_id: str,
) -> tuple[control_canary.PaidTaskResult, int]:
    """Exercise the fixed Agent path against an in-process OpenAI-compatible server."""
    frozen = _read_json(root / COMMITTED_FREEZE)
    gate = _fresh_gate(root, artifact_root, "zero-provider-loopback-agent-provider")
    task, environment = _task(root), _environment(root)
    plan = full_replay.canonical_task_environment_plan(task, environment)
    payload_path = artifact_root / "safe-payloads" / f"{TASK_ID}.json"
    _write_json(payload_path, task)
    with full_replay._loopback_fake_provider("single_response") as (provider, base_url):
        pilot = control_canary._paid_pilot_config(frozen).model_copy(update={
            "base_url": base_url,
            "api_key_env": "EVALUATION_V2_SINGLE_TASK_LOOPBACK_KEY",
        })
        result = control_canary._live_task_executor(
            root=root,
            freeze_payload=frozen,
            task=task,
            metadata={
                "preflight_dependencies": plan["dependencies"],
                "editable_target": plan["editable_target"],
                "test_target": plan["test_target"],
                "bootstrap_environment": plan["bootstrap_environment"],
                "disk_budget": plan["disk_budget"],
            },
            gate=gate,
            artifact_root=artifact_root,
            run_id=run_id,
            payload_path=payload_path,
            trial_namespace="v2-single-task-zero-provider",
            pilot_override=pilot,
            provider_secret_override="zero-provider-loopback-only",
            child_environment_overrides={
                "NO_PROXY": "127.0.0.1,localhost",
                "no_proxy": "127.0.0.1,localhost",
            },
        )
    ledger = BudgetLedger.model_validate_json(gate.ledger_path.read_text(encoding="utf-8"))
    if ledger.active_reservation is not None:
        raise RuntimeError("single-task loopback dispatch left an active reservation")
    return result, provider.request_count


def rehearse(root: Path, preflight_summary: Path, artifact_root: Path, run_id: str) -> dict[str, Any]:
    validate_freeze(root)
    if not _read_json(preflight_summary).get("passed"):
        raise ValueError("single-task rehearsal requires a healthy preflight")
    if artifact_root.exists() or not run_id or Path(run_id).name != run_id:
        raise ValueError("single-task rehearsal requires a fresh safe artifact root and Run ID")
    artifact_root.mkdir(parents=True)
    normal_root, violation_root = artifact_root / "normal", artifact_root / "usage-contract-violation"
    normal, normal_ledger = _fake_result(root, normal_root, f"{run_id}-normal", "normal")
    violation, violation_ledger = _fake_result(root, violation_root, f"{run_id}-violation", "usage-contract-violation")
    dispatch, loopback_requests = _loopback_agent_provider_dispatch(
        root, artifact_root / "agent-provider-dispatch", f"{run_id}-loopback",
    )
    if normal_ledger.active_reservation is not None or violation_ledger.active_reservation is not None:
        raise RuntimeError("zero-provider rehearsal left an active reservation")
    if not dispatch.agent_dispatch_started or loopback_requests < 1:
        raise RuntimeError("single-task Agent-to-loopback Provider coverage is incomplete")
    result = {"schema_version": SCHEMA_VERSION, "run_id": run_id, "paid_execution": False, "formal_trial_count": 0, "task": TASK_ID, "provider_transport": "loopback_fake_openai_compatible", "external_provider_transport": False, "external_provider_requests": 0, "provider_requests": 0, "usage": 0, "charge_cny": "0", "provider_secret_read": False, "agent_dispatch_count": 1, "provider_task_coverage": "1/1", "loopback_simulated_provider_requests": loopback_requests, "agent_provider_dispatch": asdict(dispatch), "normal": asdict(normal), "usage_contract_violation": asdict(violation), "ledger_closed": True, "active_reservation": None, "completed": True}
    _write_json(artifact_root / "single-task-rehearsal-summary.json", result)
    _write_json(artifact_root / "artifact-index-record.json", {"schema_version": SCHEMA_VERSION, "kind": "evaluation-v2-single-task-zero-provider-rehearsal", "task": TASK_ID, "parent_readiness": freeze_payload(root)["parent_readiness"], "summary": "single-task-rehearsal-summary.json"})
    return result


def run_paid(root: Path, artifact_root: Path, expected_freeze_sha256: str, approved_hard_cap_cny: str, authorization_acknowledgement: str, run_id: str) -> dict[str, Any]:
    identities = validate_freeze(root)
    frozen = _read_json(root / COMMITTED_FREEZE)
    budget = frozen["budget_contract"]
    if expected_freeze_sha256 != identities["freeze_sha256"]:
        raise ValueError("expected single-task Freeze SHA does not match")
    if Decimal(approved_hard_cap_cny) != Decimal(budget["authorization_hard_cap_cny"]):
        raise ValueError("approved hard cap does not match the frozen single-task contract")
    if not run_id or Path(run_id).name != run_id or artifact_root.exists():
        raise ValueError("single-task paid execution requires a fresh safe Run ID and artifact root")
    artifact_root.mkdir(parents=True)
    gate = _fresh_gate(root, artifact_root, authorization_acknowledgement)
    task, environment = _task(root), _environment(root)
    plan = full_replay.canonical_task_environment_plan(task, environment)
    payload_path = artifact_root / "safe-payloads" / f"{TASK_ID}.json"
    _write_json(payload_path, task)
    result = control_canary._live_task_executor(root=root, freeze_payload=frozen, task=task, metadata={"preflight_dependencies": plan["dependencies"], "editable_target": plan["editable_target"], "test_target": plan["test_target"], "bootstrap_environment": plan["bootstrap_environment"], "disk_budget": plan["disk_budget"]}, gate=gate, artifact_root=artifact_root, run_id=run_id, payload_path=payload_path, trial_namespace="v2-single-task")
    ledger = BudgetLedger.model_validate_json(gate.ledger_path.read_text(encoding="utf-8"))
    summary = {"schema_version": SCHEMA_VERSION, "run_id": run_id, "task": TASK_ID, "paid_execution": True, "freeze_sha256": identities["freeze_sha256"], "runtime_contract_hash": identities["runtime_contract_hash"], "pricing_snapshot_hash": identities["pricing_snapshot_hash"], "result": asdict(result), "provider_requests": len(ledger.request_charges), "usage": sum(item.input_tokens + item.output_tokens for item in ledger.request_charges), "charge_cny": str(ledger.spent_cny), "settlements": len(ledger.settlements), "ledger_closed": ledger.active_reservation is None, "active_reservation": None if ledger.active_reservation is None else ledger.active_reservation.model_dump(mode="json"), "completed": result.terminal_status in TERMINALS and ledger.active_reservation is None}
    _write_json(artifact_root / "paid-single-task-summary.json", summary)
    return summary


def release_check(root: Path, preflight_summary: Path, rehearsal_summary: Path) -> dict[str, Any]:
    identities = validate_freeze(root)
    preflight, rehearsal = _read_json(preflight_summary), _read_json(rehearsal_summary)
    remote = __import__("subprocess").run(["git", "-C", str(root), "rev-parse", "--verify", "refs/remotes/origin/main^{commit}"], capture_output=True, text=True, check=False).stdout.strip()
    blockers = []
    if not remote or current_git_commit(root) != remote: blockers.append("head_is_not_origin_main")
    if not preflight.get("passed") or preflight.get("ready_count") != 1: blockers.append("single_task_preflight_not_ready")
    if rehearsal.get("provider_task_coverage") != "1/1" or not rehearsal.get("ledger_closed") or rehearsal.get("active_reservation") is not None: blockers.append("single_task_rehearsal_not_closed")
    if any((rehearsal.get("external_provider_requests"), rehearsal.get("usage"), Decimal(str(rehearsal.get("charge_cny", "0"))))): blockers.append("zero_provider_accounting_changed")
    return {"status": "READY_FOR_SINGLE_TASK_PAID_CANARY_AUTHORIZATION" if not blockers else blockers[0], "blockers": blockers, **identities, "task": TASK_ID, "provider_requests": 0, "usage": 0, "charge_cny": "0", "provider_secret_read": False, "workflow_inputs": {"paid_execution": "true", "expected_freeze_sha256": identities["freeze_sha256"], "approved_hard_cap_cny": _read_json(root / COMMITTED_FREEZE)["budget_contract"]["authorization_hard_cap_cny"], "authorization_acknowledgement": "REPLACE_WITH_EXPLICIT_SINGLE_TASK_AUTHORIZATION", "run_id": "REPLACE_WITH_FRESH_RUN_ID"}}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluation V2 isolated single-task paid canary")
    sub = parser.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("freeze"); freeze.add_argument("--root", type=Path, required=True); freeze.add_argument("--output", type=Path, required=True)
    validate = sub.add_parser("validate"); validate.add_argument("--root", type=Path, required=True); validate.add_argument("--freeze", type=Path, default=COMMITTED_FREEZE)
    preflight = sub.add_parser("preflight"); preflight.add_argument("--root", type=Path, required=True); preflight.add_argument("--artifact-root", type=Path, required=True)
    rehearsal = sub.add_parser("rehearse"); rehearsal.add_argument("--root", type=Path, required=True); rehearsal.add_argument("--preflight-summary", type=Path, required=True); rehearsal.add_argument("--artifact-root", type=Path, required=True); rehearsal.add_argument("--run-id", required=True)
    release = sub.add_parser("release-check"); release.add_argument("--root", type=Path, required=True); release.add_argument("--preflight-summary", type=Path, required=True); release.add_argument("--rehearsal-summary", type=Path, required=True); release.add_argument("--output", type=Path)
    paid = sub.add_parser("paid-run"); paid.add_argument("--root", type=Path, required=True); paid.add_argument("--artifact-root", type=Path, required=True); paid.add_argument("--expected-freeze-sha256", required=True); paid.add_argument("--approved-hard-cap-cny", required=True); paid.add_argument("--authorization-acknowledgement", required=True); paid.add_argument("--run-id", required=True); paid.add_argument("--confirm-paid-execution", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "freeze": result = write_freeze(args.root, args.output)
    elif args.command == "validate": result = validate_freeze(args.root, args.freeze)
    elif args.command == "preflight": result = run_preflight(args.root, args.artifact_root)
    elif args.command == "rehearse": result = rehearse(args.root, args.preflight_summary, args.artifact_root, args.run_id)
    elif args.command == "release-check":
        result = release_check(args.root, args.preflight_summary, args.rehearsal_summary)
        if args.output: _write_json(args.output, result)
    else:
        if not args.confirm_paid_execution: raise ValueError("paid execution requires --confirm-paid-execution")
        result = run_paid(args.root, args.artifact_root, args.expected_freeze_sha256, args.approved_hard_cap_cny, args.authorization_acknowledgement, args.run_id)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

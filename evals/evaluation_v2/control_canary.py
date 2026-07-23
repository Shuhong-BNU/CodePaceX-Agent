"""Freeze and rehearse the Evaluation V2 two-task Control Canary.

This module intentionally has no Provider transport.  Its paid-runner function
accepts an injected future executor so its stop/ledger contract is testable now
without making a request or reading a secret.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import venv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Sequence

from codepacex.prompts import build_system_prompt
from codepacex.tools import create_default_registry
from evals.benchmark import canonical_hash, current_git_commit
from evals.costing import load_pricing, pricing_snapshot_hash
from evals.goal3_swe import _goal3_materialize_instance
from evals.paid_gate import (
    BudgetAuthorization,
    BudgetLedger,
    PaidRunGate,
    authorization_hash,
    worst_case_reservation,
)


SCHEMA_VERSION = 1
OFFICIAL_EVALUATOR_COMMIT = "ad79b850f15e33992e96f03f6e97f05ddf9aa0be"
PRICING_PATH = Path("evals/goal2/pricing_bailian_qwen37_max_2026-07-13.json")
MAX_REQUESTS_PER_TASK = 40
MAX_INPUT_TOKENS = 128_000
MAX_OUTPUT_TOKENS = 8_192
MAX_REASONING_TOKENS = 6_144
RECOMMENDED_HARD_CAP_CNY = Decimal("15.000000")
TASKS: tuple[dict[str, str], ...] = (
    {
        "instance_id": "beetbox__beets-5495",
        "repo": "beetbox/beets",
        "base_commit": "fa10dcf11add0afd3b4b22af29f8d504e7ef8a0a",
        "test_command": "test/test_importer.py::ImportTest::test_set_fields",
        "historical_goal4_cost_cny": "1.010196",
    },
    {
        "instance_id": "beancount__beancount-931",
        "repo": "beancount/beancount",
        "base_commit": "a0e6f445fbf0d101602a4b6d886d6320971587b6",
        "test_command": "beancount/plugins/leafonly_test.py::TestLeafOnly::test_leaf_only3",
        "historical_goal4_cost_cny": "3.297720",
    },
)
RUNTIME_SOURCES = (
    "codepacex/agent.py",
    "codepacex/client.py",
    "codepacex/permissions/checker.py",
    "codepacex/tools/edit_file.py",
    "codepacex/tools/run_test.py",
    "evals/evaluation_v2/golden_path.py",
    "evals/evaluation_v2/control_canary.py",
    "evals/paid_gate.py",
)
def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _runtime_contract(root: Path) -> dict[str, Any]:
    sources = {name: _sha256(root / name) for name in RUNTIME_SOURCES}
    tools = create_default_registry().get_all_schemas("openai-compat")
    return {
        "schema_version": SCHEMA_VERSION,
        "runtime_source_sha256": sources,
        "agent_entrypoint": "Agent.run_to_completion",
        "system_instruction_sha256": hashlib.sha256(build_system_prompt().encode()).hexdigest(),
        "tool_schemas_sha256": canonical_hash(tools),
        "permission_profile": "session_allow_workspace_boundary",
        "candidate_export_contract": "git-diff-binary-sha256-bound-v1",
    }


def budget_contract(root: Path) -> dict[str, Any]:
    pricing = load_pricing(root / PRICING_PATH)
    one_request = worst_case_reservation(
        pricing, maximum_requests=1, maximum_input_tokens_per_request=MAX_INPUT_TOKENS,
        maximum_output_tokens_per_request=MAX_OUTPUT_TOKENS,
    )
    per_task = worst_case_reservation(
        pricing, maximum_requests=MAX_REQUESTS_PER_TASK,
        maximum_input_tokens_per_request=MAX_INPUT_TOKENS,
        maximum_output_tokens_per_request=MAX_OUTPUT_TOKENS,
    )
    total = per_task * len(TASKS)
    historical = sum((Decimal(task["historical_goal4_cost_cny"]) for task in TASKS), Decimal("0"))
    return {
        "pricing_snapshot_hash": pricing_snapshot_hash(pricing),
        "pricing_snapshot_path": str(PRICING_PATH),
        "maximum_input_tokens_per_request": MAX_INPUT_TOKENS,
        "maximum_output_tokens_per_request": MAX_OUTPUT_TOKENS,
        "maximum_reasoning_tokens_per_request": MAX_REASONING_TOKENS,
        "maximum_provider_requests_per_task": MAX_REQUESTS_PER_TASK,
        "rolling_reservation": "one_provider_request",
        "ledger_budget_stage": "C",
        "ledger_stage_note": "the generic gate enum value is used only inside this fresh V2 ledger; it is not a Stage C experiment or ledger",
        "one_request_theoretical_maximum_cny": str(one_request),
        "per_task_theoretical_maximum_cny": str(per_task),
        "two_task_theoretical_maximum_cny": str(total),
        "historical_goal4_control_cost_cny": str(historical),
        "recommended_hard_cap_cny": str(RECOMMENDED_HARD_CAP_CNY),
        "recommendation_basis": "rounded 3x historical resolved-control cost, still constrained by the per-request CNY exposure",
        "pretransport_block": "reject reservation when remaining hard cap is below one_request_theoretical_maximum_cny",
    }


def freeze_payload(root: Path) -> dict[str, Any]:
    runtime = _runtime_contract(root)
    budget = budget_contract(root)
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_name": "evaluation-v2-control-canary",
        "status": "frozen_pending_single_budget_authorization",
        "evaluated_codepacex_commit": current_git_commit(root),
        "base_lane": {"run_id": "29981654331", "artifact_id": "8553450494", "commit": "97fa5ad"},
        "tasks": list(TASKS),
        "runtime_contract": runtime,
        "runtime_contract_hash": canonical_hash(runtime),
        "provider_contract": {
            "provider": "bailian-qwen37-max",
            "protocol": "openai-compat",
            "model_id": "qwen3.7-max-2026-06-08",
            "provider_secret_name": "BAILIAN_API_KEY",
            "fallback_enabled": False,
            "retry": 0,
            "strict_serial": True,
        },
        "official_evaluator": {
            "repository": "https://github.com/microsoft/SWE-bench-Live",
            "commit": OFFICIAL_EVALUATOR_COMMIT,
            "architecture": "native-x86_64",
            "namespace": "starryzhang",
            "report_selection": "detailed_then_summary_fail_closed-v1",
        },
        "workspace_materialization": "git-clone-filter-blob-none-detached-base-v1",
        "dependency_bootstrap": "isolated-venv-pip-install-editable-plus-pytest-v1",
        "gold_patch_forbidden": True,
        "fresh_authorization_and_ledger_required": True,
        "terminal_status_schema": ["resolved", "unresolved", "agent_no_candidate", "protocol_blocked", "provider_transport_error", "evaluator_unavailable", "evaluator_execution_error", "evaluator_report_selection_error", "runner_error", "budget_blocked"],
        "budget_contract": budget,
        "go_no_go": {
            "go": "both task environment preflights pass and zero-provider paid-path rehearsal closes its ledger",
            "stop_after_first": ["provider_transport_error", "runner_error", "evaluator_unavailable", "evaluator_execution_error", "evaluator_report_selection_error", "active_reservation", "usage_charge_settlement_mismatch", "freeze_identity_mismatch"],
            "unresolved_continues_only_when_healthy": True,
        },
    }


def write_freeze(*, root: Path, output: Path) -> dict[str, Any]:
    root, output = root.resolve(), output.resolve()
    if output.exists():
        raise ValueError("refusing to overwrite an Evaluation V2 Canary Freeze")
    output.mkdir(parents=True)
    payload = freeze_payload(root)
    _write_json(output / "control-canary-freeze.json", payload)
    shutil.copyfile(root / PRICING_PATH, output / "pricing-snapshot.json")
    _write_json(output / "authorization-request.json", {
        "schema_version": SCHEMA_VERSION,
        "status": "pending_user_authorization_no_provider_transport_permitted",
        "required_hard_cap_cny": payload["budget_contract"]["recommended_hard_cap_cny"],
        "freeze_sha256": _sha256(output / "control-canary-freeze.json"),
        "pricing_snapshot_hash": payload["budget_contract"]["pricing_snapshot_hash"],
        "fresh_ledger_required": True,
    })
    return {"freeze_sha256": _sha256(output / "control-canary-freeze.json"), "runtime_contract_hash": payload["runtime_contract_hash"], **payload}


def validate_freeze(*, root: Path, freeze: Path) -> dict[str, Any]:
    root, freeze = root.resolve(), freeze.resolve()
    payload = json.loads((freeze / "control-canary-freeze.json").read_text(encoding="utf-8"))
    expected = freeze_payload(root)
    if payload != expected:
        raise ValueError("Evaluation V2 Control Canary Freeze differs from the current committed contract")
    if _sha256(freeze / "pricing-snapshot.json") != _sha256(root / PRICING_PATH):
        raise ValueError("Canary pricing snapshot differs from the frozen source")
    if [task["instance_id"] for task in payload["tasks"]] != [task["instance_id"] for task in TASKS]:
        raise ValueError("Canary task order differs from the fixed two-task contract")
    return {"valid": True, "freeze_sha256": _sha256(freeze / "control-canary-freeze.json"), "runtime_contract_hash": payload["runtime_contract_hash"]}


def _run(command: list[str], *, cwd: Path, timeout: int = 1200) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False)


def _environment_blocker(result: subprocess.CompletedProcess[str]) -> str | None:
    text = (result.stdout + "\n" + result.stderr).lower()
    if "modulenotfounderror" in text or "no module named" in text:
        return "missing_python_dependency"
    if "error collecting" in text or result.returncode == 4:
        return "pytest_collection_error"
    if "command not found" in text or result.returncode == 127:
        return "command_not_found"
    if result.returncode not in {0, 1}:
        return "test_runner_error"
    return None


def _bootstrap(workspace: Path) -> tuple[Path, list[dict[str, Any]]]:
    venv_path = workspace / ".evaluation-v2-preflight-venv"
    venv.EnvBuilder(with_pip=True, clear=True).create(venv_path)
    python = venv_path / "bin" / "python"
    install = _run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-e", ".", "pytest"], cwd=workspace)
    logs = [{"command": [str(python), "-m", "pip", "install", "--disable-pip-version-check", "-e", ".", "pytest"], "exit_code": install.returncode, "stdout": install.stdout, "stderr": install.stderr}]
    return python, logs


def preflight_task(task: dict[str, str], *, work_root: Path) -> dict[str, Any]:
    work_root.mkdir(parents=True, exist_ok=True)
    workspace = work_root / task["instance_id"]
    artifact = work_root / "evidence" / task["instance_id"]
    artifact.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {"instance_id": task["instance_id"], "repository": task["repo"], "base_commit": task["base_commit"], "task_workspace_materialized": False, "dependencies_installed": False, "test_collection_completed": False, "meaningful_test_executed": False, "environment_blocker": None, "test_command": task["test_command"]}
    phase = "workspace_materialization"
    try:
        _goal3_materialize_instance(task, workspace)
        result["task_workspace_materialized"] = True
        phase = "dependency_bootstrap"
        python, installs = _bootstrap(workspace)
        _write_json(artifact / "dependency-bootstrap.json", installs)
        if installs[-1]["exit_code"]:
            raise RuntimeError("dependency bootstrap command failed")
        result["dependencies_installed"] = True
        phase = "test_execution"
        test = _run([str(python), "-m", "pytest", task["test_command"]], cwd=workspace)
        _write_json(artifact / "test-command.json", {"command": [str(python), "-m", "pytest", task["test_command"]]})
        (artifact / "test.stdout.txt").write_text(test.stdout, encoding="utf-8")
        (artifact / "test.stderr.txt").write_text(test.stderr, encoding="utf-8")
        result["exit_code"] = test.returncode
        result["test_collection_completed"] = bool(re.search(r"collected\s+\d+\s+items?", test.stdout)) and "error collecting" not in (test.stdout + test.stderr).lower()
        result["environment_blocker"] = _environment_blocker(test)
        result["meaningful_test_executed"] = result["environment_blocker"] is None and result["test_collection_completed"]
    except subprocess.TimeoutExpired as exc:
        result["environment_blocker"] = f"{phase}_timeout"
        result["error"] = str(exc)
    except Exception as exc:
        result["environment_blocker"] = {
            "workspace_materialization": "workspace_materialization_failed",
            "dependency_bootstrap": "dependency_bootstrap_failed",
            "test_execution": "test_execution_failed",
        }[phase]
        result["error"] = str(exc)
    _write_json(artifact / "preflight-result.json", result)
    return result


def run_environment_preflight(*, freeze: Path, artifact_root: Path) -> dict[str, Any]:
    artifact_root = artifact_root.resolve()
    if artifact_root.exists():
        raise ValueError("refusing to overwrite Control Canary preflight evidence")
    artifact_root.mkdir(parents=True)
    payload = json.loads((freeze / "control-canary-freeze.json").read_text(encoding="utf-8"))
    results = [preflight_task(dict(task), work_root=artifact_root / "workspaces") for task in payload["tasks"]]
    summary = {"schema_version": SCHEMA_VERSION, "provider_requests": 0, "usage": 0, "charge_cny": "0", "settlements": 0, "active_reservation": None, "provider_secret_read": False, "tasks": results, "passed": all(item["environment_blocker"] is None and item["meaningful_test_executed"] for item in results)}
    _write_json(artifact_root / "preflight-summary.json", summary)
    if not summary["passed"]:
        raise RuntimeError("V2_CONTROL_CANARY_ENVIRONMENT_BLOCKED")
    return summary


def rehearse_paid_path(*, root: Path, freeze: Path, preflight_summary: Path, artifact_root: Path) -> dict[str, Any]:
    root, artifact_root = root.resolve(), artifact_root.resolve()
    freeze_payload = json.loads((freeze / "control-canary-freeze.json").read_text(encoding="utf-8"))
    preflight = json.loads(preflight_summary.read_text(encoding="utf-8"))
    if not preflight.get("passed"):
        raise ValueError("paid-path rehearsal requires both environment preflights")
    if artifact_root.exists():
        raise ValueError("refusing to overwrite paid-path rehearsal evidence")
    artifact_root.mkdir(parents=True)
    pricing = load_pricing(freeze / "pricing-snapshot.json")
    authorization = BudgetAuthorization(
        authorized_total_cny=RECOMMENDED_HARD_CAP_CNY,
        stage_limits_cny={"A": RECOMMENDED_HARD_CAP_CNY, "B": RECOMMENDED_HARD_CAP_CNY, "C": RECOMMENDED_HARD_CAP_CNY},
        pricing_snapshot_hash=pricing_snapshot_hash(pricing), experiment_commit=current_git_commit(root),
        authorized_at="zero-provider-rehearsal", authorized_by="user",
    )
    authorization_path = artifact_root / "rehearsal-authorization.json"
    ledger_path = artifact_root / "rehearsal-ledger.json"
    _write_json(authorization_path, authorization.model_dump(mode="json"))
    _write_json(ledger_path, BudgetLedger(authorization_hash=authorization_hash(authorization), updated_at="zero-provider-rehearsal").model_dump(mode="json"))
    gate = PaidRunGate(root=root, authorization_path=authorization_path, ledger_path=ledger_path, pricing=pricing, stage="C")
    reservations: list[dict[str, Any]] = []
    for task in freeze_payload["tasks"]:
        reservation = gate.reserve(f"swe/v2-control/rehearsal/{task['instance_id']}", maximum_requests=1, maximum_input_tokens_per_request=MAX_INPUT_TOKENS, maximum_output_tokens_per_request=MAX_OUTPUT_TOKENS)
        settlement = gate.cancel(reservation, reason="provider_confirmed_not_submitted")
        reservations.append({"instance_id": task["instance_id"], "reservation_cny": str(reservation.reserved_cny), "settlement_cny": str(settlement.actual_cny), "status": settlement.status})
    ledger = BudgetLedger.model_validate_json(ledger_path.read_text(encoding="utf-8"))
    if ledger.active_reservation is not None or ledger.request_charges or ledger.spent_cny != 0:
        raise RuntimeError("zero-provider paid-path rehearsal did not close cleanly")
    result = {"schema_version": SCHEMA_VERSION, "paid_execution": False, "provider_requests": 0, "usage": 0, "charge_cny": "0", "settlements": len(ledger.settlements), "active_reservation": None, "provider_secret_read": False, "reservations": reservations, "freeze_sha256": _sha256(freeze / "control-canary-freeze.json")}
    _write_json(artifact_root / "paid-path-rehearsal.json", result)
    return result


@dataclass
class PaidTaskResult:
    instance_id: str
    agent_status: str
    candidate_status: str
    validation_status: str
    evaluator_status: str
    runner_status: str
    provider_status: str
    active_reservation: Any = None


def execute_paid_canary(*, freeze: Path, paid_execution: bool, executor: Callable[[dict[str, str]], PaidTaskResult] | None = None) -> list[PaidTaskResult]:
    """Future serial runner: execute task two only after task one is healthy.

    No default executor is supplied in this phase, preventing accidental model
    transport even if this function is called incorrectly.
    """
    if not paid_execution:
        return []
    if executor is None:
        raise ValueError("paid execution requires a separately authorized Provider executor")
    tasks = json.loads((freeze / "control-canary-freeze.json").read_text(encoding="utf-8"))["tasks"]
    results: list[PaidTaskResult] = []
    for task in tasks:
        result = executor(dict(task))
        results.append(result)
        unhealthy = result.runner_status != "completed" or result.evaluator_status in {"evaluator_unavailable", "evaluator_execution_error", "evaluator_report_selection_error"} or result.provider_status == "provider_transport_error" or result.active_reservation is not None
        if unhealthy:
            break
    return results


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluation V2 Control Canary zero-provider controls")
    sub = parser.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("freeze"); freeze.add_argument("--root", type=Path, required=True); freeze.add_argument("--output", type=Path, required=True)
    validate = sub.add_parser("validate"); validate.add_argument("--root", type=Path, required=True); validate.add_argument("--freeze", type=Path, required=True)
    preflight = sub.add_parser("preflight"); preflight.add_argument("--freeze", type=Path, required=True); preflight.add_argument("--artifact-root", type=Path, required=True)
    rehearsal = sub.add_parser("rehearse"); rehearsal.add_argument("--root", type=Path, required=True); rehearsal.add_argument("--freeze", type=Path, required=True); rehearsal.add_argument("--preflight-summary", type=Path, required=True); rehearsal.add_argument("--artifact-root", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "freeze": result = write_freeze(root=args.root, output=args.output)
    elif args.command == "validate": result = validate_freeze(root=args.root, freeze=args.freeze)
    elif args.command == "preflight": result = run_environment_preflight(freeze=args.freeze, artifact_root=args.artifact_root)
    else: result = rehearse_paid_path(root=args.root, freeze=args.freeze, preflight_summary=args.preflight_summary, artifact_root=args.artifact_root)
    print(json.dumps(result, sort_keys=True)); return 0


if __name__ == "__main__":
    raise SystemExit(main())

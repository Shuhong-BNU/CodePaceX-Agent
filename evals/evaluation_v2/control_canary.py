"""Freeze and rehearse the Evaluation V2 two-task Control Canary.

This module intentionally has no Provider transport.  Its paid-runner function
accepts an injected future executor so its stop/ledger contract is testable now
without making a request or reading a secret.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import venv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Sequence

import yaml
from codepacex.config import load_config as load_codepacex_config
from codepacex.experiments import ExperimentProfile
from codepacex.prompts import build_static_system_instruction
from codepacex.tools import create_default_registry
from evals.benchmark import canonical_hash, current_git_commit
from evals.costing import load_pricing, pricing_snapshot_hash
from evals.goal3_swe import (
    ENVIRONMENT as SWE_ENVIRONMENT,
    _child_environment,
    _goal3_child_config,
    _goal3_extract_patch,
    _goal3_inference_prompt,
    _goal3_materialize_instance,
    collect_goal3_official_outcome,
)
from evals.paid_gate import (
    BudgetAuthorization,
    BudgetLedger,
    PaidRunGate,
    StageCBudgetAllocation,
    allocation_hash,
    authorization_hash,
    ledger_fingerprint,
    provider_request_budget_environment,
    worst_case_reservation,
)
from evals.pilot import PilotConfig
from evals.swe_bench_live import official_evaluator_report_path, run_official_evaluator


SCHEMA_VERSION = 1
OFFICIAL_EVALUATOR_COMMIT = "ad79b850f15e33992e96f03f6e97f05ddf9aa0be"
PRICING_PATH = Path("evals/goal2/pricing_bailian_qwen37_max_2026-07-13.json")
MAX_REQUESTS_PER_TASK = 40
MAX_INPUT_TOKENS = 128_000
MAX_OUTPUT_TOKENS = 8_192
MAX_REASONING_TOKENS = 6_144
RECOMMENDED_HARD_CAP_CNY = Decimal("15.000000")
PAYLOAD_DIRECTORY = Path("evals/evaluation_v2/control_canary_payloads")
PAYLOAD_MANIFEST = PAYLOAD_DIRECTORY / "manifest.json"
PAYLOAD_FIELDS = frozenset({"instance_id", "repo", "base_commit", "problem_statement"})
FORBIDDEN_PAYLOAD_KEY = re.compile(r"(?:^|_)(?:patch|gold|test_patch|solution|reference_patch|expected_patch|answer)(?:_|$)", re.I)
PROVIDER_BASE_URL = "https://llm-ipge9fy38w648m28.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
TASKS: tuple[dict[str, Any], ...] = (
    {
        "instance_id": "beetbox__beets-5495",
        "repo": "beetbox/beets",
        "base_commit": "fa10dcf11add0afd3b4b22af29f8d504e7ef8a0a",
        "test_target": "test/test_importer.py::ImportSingletonTest::test_set_fields",
        "preflight_dependencies": ["responses>=0.3.0"],
        "historical_goal4_cost_cny": "1.010196",
    },
    {
        "instance_id": "beancount__beancount-931",
        "repo": "beancount/beancount",
        "base_commit": "a0e6f445fbf0d101602a4b6d886d6320971587b6",
        "test_target": "beancount/plugins/leafonly_test.py",
        "preflight_dependencies": [],
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


def _scan_payload_value(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if FORBIDDEN_PAYLOAD_KEY.search(str(key)):
                raise ValueError(f"Control Canary payload has forbidden key: {key}")
            _scan_payload_value(item)
    elif isinstance(value, list):
        for item in value:
            _scan_payload_value(item)
    elif isinstance(value, str) and "diff --git" in value and "--- a/" in value and "+++ b/" in value:
        raise ValueError("Control Canary payload contains a unified diff")


def load_frozen_payloads(root: Path) -> list[dict[str, str]]:
    """Load only the committed four-field, Agent-visible task projection."""
    payload_root = (root / PAYLOAD_DIRECTORY).resolve()
    manifest_path = payload_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if set(manifest) != {"schema_version", "task_order", "payloads", "source"}:
        raise ValueError("Control Canary payload manifest schema changed")
    if manifest["schema_version"] != 1 or manifest["task_order"] != [task["instance_id"] for task in TASKS]:
        raise ValueError("Control Canary payload manifest task order changed")
    if not isinstance(manifest["source"], dict) or manifest["source"].get("extraction_contract") != "allowlisted-non-gold-fields-v1":
        raise ValueError("Control Canary payload source contract changed")
    payloads: list[dict[str, str]] = []
    records = manifest["payloads"]
    if not isinstance(records, list) or len(records) != len(TASKS):
        raise ValueError("Control Canary payload manifest count changed")
    for task, record in zip(TASKS, records, strict=True):
        if not isinstance(record, dict) or record.get("instance_id") != task["instance_id"]:
            raise ValueError("Control Canary payload manifest identity changed")
        name = record.get("file")
        if not isinstance(name, str) or Path(name).name != name or not name.endswith(".json"):
            raise ValueError("Control Canary payload manifest filename is unsafe")
        path = payload_root / name
        if _sha256(path) != record.get("payload_sha256"):
            raise ValueError("Control Canary payload SHA differs from its manifest")
        payload = json.loads(path.read_text(encoding="utf-8"))
        _scan_payload_value(payload)
        if set(payload) != PAYLOAD_FIELDS:
            raise ValueError("Control Canary payload must contain exactly four allowlisted fields")
        if payload["instance_id"] != task["instance_id"] or payload["repo"] != task["repo"] or payload["base_commit"] != task["base_commit"]:
            raise ValueError("Control Canary payload identity differs from the frozen task")
        if not isinstance(payload["problem_statement"], str) or not payload["problem_statement"]:
            raise ValueError("Control Canary payload has no problem statement")
        if hashlib.sha256(payload["problem_statement"].encode()).hexdigest() != record.get("problem_statement_sha256"):
            raise ValueError("Control Canary problem statement SHA differs from its manifest")
        payloads.append({key: str(payload[key]) for key in PAYLOAD_FIELDS})
    return payloads


def payload_contract(root: Path) -> dict[str, Any]:
    payloads = load_frozen_payloads(root)
    manifest = json.loads((root / PAYLOAD_MANIFEST).read_text(encoding="utf-8"))
    return {
        "manifest_sha256": _sha256(root / PAYLOAD_MANIFEST),
        "source": manifest["source"],
        "payloads": [
            {
                "instance_id": payload["instance_id"],
                "payload_sha256": _sha256(root / PAYLOAD_DIRECTORY / record["file"]),
                "problem_statement_sha256": hashlib.sha256(payload["problem_statement"].encode()).hexdigest(),
            }
            for payload, record in zip(payloads, manifest["payloads"], strict=True)
        ],
        "prompt_construction": "goal3-swe-inference-prompt-v1",
    }


def _runtime_contract(root: Path) -> dict[str, Any]:
    sources = {name: _sha256(root / name) for name in RUNTIME_SOURCES}
    tools = create_default_registry().get_all_schemas("openai-compat")
    return {
        "schema_version": SCHEMA_VERSION,
        "runtime_source_sha256": sources,
        "agent_entrypoint": "Agent.run_to_completion",
        "system_instruction_sha256": hashlib.sha256(build_static_system_instruction().encode()).hexdigest(),
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
    payloads = payload_contract(root)
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_name": "evaluation-v2-control-canary",
        "status": "frozen_pending_single_budget_authorization",
        "evaluated_codepacex_commit": current_git_commit(root),
        "base_lane": {"run_id": "29981654331", "artifact_id": "8553450494", "commit": "97fa5ad"},
        "tasks": list(TASKS),
        "payload_contract": payloads,
        "runtime_contract": runtime,
        "runtime_contract_hash": canonical_hash(runtime),
        "provider_contract": {
            "provider": "bailian-qwen37-max",
            "protocol": "openai-compat",
            "model_id": "qwen3.7-max-2026-06-08",
            "base_url": PROVIDER_BASE_URL,
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
        "dependency_bootstrap": "isolated-venv-pip-install-editable-pytest-and-frozen-preflight-dependencies-v2",
        "gold_patch_forbidden": True,
        "fresh_authorization_and_ledger_required": True,
        "terminal_status_schema": ["resolved", "unresolved", "agent_no_candidate", "protocol_blocked", "provider_transport_error", "evaluator_unavailable", "evaluator_execution_error", "evaluator_report_selection_error", "runner_error", "budget_blocked", "task_environment_blocked", "preflight_wiring_blocked"],
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
    if "not found:" in text or "not found\n" in text or "file or directory not found" in text:
        return "pytest_selector_not_found"
    if "error collecting" in text or result.returncode == 4:
        return "pytest_collection_error"
    if "command not found" in text or result.returncode == 127:
        return "command_not_found"
    if result.returncode not in {0, 1}:
        return "test_runner_error"
    return None


def _bootstrap(
    workspace: Path, preflight_dependencies: Sequence[str], *, editable_target: str = ".",
) -> tuple[Path, list[dict[str, Any]]]:
    venv_path = workspace / ".evaluation-v2-preflight-venv"
    venv.EnvBuilder(with_pip=True, clear=True).create(venv_path)
    python = venv_path / "bin" / "python"
    command = [
        str(python), "-m", "pip", "install", "--disable-pip-version-check",
        "-e", editable_target, "pytest", *preflight_dependencies,
    ]
    install = _run(command, cwd=workspace)
    logs = [{
        "command": command,
        "exit_code": install.returncode,
        "stdout": install.stdout,
        "stderr": install.stderr,
        "preflight_dependencies": list(preflight_dependencies),
    }]
    return python, logs


def _collected_test_count(result: subprocess.CompletedProcess[str]) -> int:
    output = result.stdout + "\n" + result.stderr
    matches = re.findall(r"(\d+)\s+(?:tests?|items?)\s+collected", output, re.IGNORECASE)
    return int(matches[-1]) if matches else 0


def preflight_task(task: dict[str, Any], *, work_root: Path) -> dict[str, Any]:
    work_root.mkdir(parents=True, exist_ok=True)
    workspace = work_root / task["instance_id"]
    artifact = work_root / "evidence" / task["instance_id"]
    artifact.mkdir(parents=True, exist_ok=True)
    test_target = str(task["test_target"])
    dependencies = [str(item) for item in task["preflight_dependencies"]]
    result: dict[str, Any] = {
        "instance_id": task["instance_id"],
        "repository": task["repo"],
        "base_commit": task["base_commit"],
        "test_target": test_target,
        "preflight_dependencies": dependencies,
        "task_workspace_materialized": False,
        "dependencies_installed": False,
        "test_collection_completed": False,
        "collected_test_count": 0,
        "meaningful_test_executed": False,
        "environment_blocker": None,
    }
    phase = "workspace_materialization"
    try:
        _goal3_materialize_instance(task, workspace)
        result["task_workspace_materialized"] = True
        phase = "dependency_bootstrap"
        python, installs = _bootstrap(workspace, dependencies)
        _write_json(artifact / "dependency-bootstrap.json", installs)
        if installs[-1]["exit_code"]:
            raise RuntimeError("dependency bootstrap command failed")
        result["dependencies_installed"] = True
        phase = "test_collection"
        collect_command = [str(python), "-m", "pytest", "--collect-only", "-q", test_target]
        collected = _run(collect_command, cwd=workspace)
        _write_json(artifact / "collection-command.json", {"command": collect_command})
        (artifact / "collection.stdout.txt").write_text(collected.stdout, encoding="utf-8")
        (artifact / "collection.stderr.txt").write_text(collected.stderr, encoding="utf-8")
        count = _collected_test_count(collected)
        result["collection_exit_code"] = collected.returncode
        result["collected_test_count"] = count
        blocker = _environment_blocker(collected)
        if blocker is None and count == 0:
            blocker = "pytest_collected_zero_tests"
        if blocker is not None:
            result["environment_blocker"] = blocker
        else:
            result["test_collection_completed"] = True
            phase = "test_execution"
            execute_command = [str(python), "-m", "pytest", test_target]
            test = _run(execute_command, cwd=workspace)
            _write_json(artifact / "execution-command.json", {"command": execute_command})
            (artifact / "execution.stdout.txt").write_text(test.stdout, encoding="utf-8")
            (artifact / "execution.stderr.txt").write_text(test.stderr, encoding="utf-8")
            result["execution_exit_code"] = test.returncode
            result["exit_code"] = test.returncode
            result["environment_blocker"] = _environment_blocker(test)
            result["meaningful_test_executed"] = result["environment_blocker"] is None
    except subprocess.TimeoutExpired as exc:
        result["environment_blocker"] = f"{phase}_timeout"
        result["error"] = str(exc)
    except Exception as exc:
        result["environment_blocker"] = {
            "workspace_materialization": "workspace_materialization_failed",
            "dependency_bootstrap": "dependency_bootstrap_failed",
            "test_collection": "test_collection_failed",
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


def _fresh_rehearsal_allocation(
    authorization: BudgetAuthorization, ledger: BudgetLedger, pricing_hash: str,
) -> StageCBudgetAllocation:
    safety_reserve = Decimal("0.000001")
    spendable_total = authorization.authorized_total_cny - safety_reserve
    return StageCBudgetAllocation(
        experiment_commit=authorization.experiment_commit,
        pricing_snapshot_hash=pricing_hash,
        baseline_ledger_sha256=ledger_fingerprint(ledger),
        baseline_authorization_hash=authorization_hash(authorization),
        baseline_spent_cny=ledger.spent_cny,
        baseline_request_charge_count=len(ledger.request_charges),
        baseline_settlement_count=len(ledger.settlements),
        baseline_budget_block_count=len(ledger.budget_blocks),
        baseline_rebind_count=len(ledger.authorization_rebinds),
        safety_reserve_cny=safety_reserve,
        spendable_total_cny=spendable_total,
        category_limits_cny={
            "swe": spendable_total,
            "mcp": Decimal("0"),
            "retention": Decimal("0"),
            "permission": Decimal("0"),
            "multi_agent": Decimal("0"),
            "long_session": Decimal("0"),
        },
    )


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
    allocation_path = artifact_root / "rehearsal-stage-c-allocation.json"
    _write_json(authorization_path, authorization.model_dump(mode="json"))
    initial_ledger = BudgetLedger(
        authorization_hash=authorization_hash(authorization), updated_at="zero-provider-rehearsal",
    )
    _write_json(ledger_path, initial_ledger.model_dump(mode="json"))
    allocation = _fresh_rehearsal_allocation(
        authorization, initial_ledger, pricing_snapshot_hash(pricing),
    )
    _write_json(allocation_path, allocation.model_dump(mode="json"))
    gate = PaidRunGate(
        root=root, authorization_path=authorization_path, ledger_path=ledger_path,
        pricing=pricing, stage="C", allocation_path=allocation_path,
    )
    reservations: list[dict[str, Any]] = []
    for task in freeze_payload["tasks"]:
        reservation = gate.reserve(f"swe/v2-control/rehearsal/{task['instance_id']}", maximum_requests=1, maximum_input_tokens_per_request=MAX_INPUT_TOKENS, maximum_output_tokens_per_request=MAX_OUTPUT_TOKENS)
        settlement = gate.cancel(reservation, reason="provider_confirmed_not_submitted")
        reservations.append({"instance_id": task["instance_id"], "reservation_cny": str(reservation.reserved_cny), "settlement_cny": str(settlement.actual_cny), "status": settlement.status})
    ledger = BudgetLedger.model_validate_json(ledger_path.read_text(encoding="utf-8"))
    if ledger.active_reservation is not None or ledger.request_charges or ledger.spent_cny != 0:
        raise RuntimeError("zero-provider paid-path rehearsal did not close cleanly")
    result = {
        "schema_version": SCHEMA_VERSION,
        "paid_execution": False,
        "provider_requests": 0,
        "usage": 0,
        "charge_cny": "0",
        "settlements": len(ledger.settlements),
        "active_reservation": None,
        "provider_secret_read": False,
        "reservations": reservations,
        "freeze_sha256": _sha256(freeze / "control-canary-freeze.json"),
        "allocation": {
            "path": allocation_path.name,
            "sha256": allocation_hash(allocation),
            "baseline_ledger_sha256": allocation.baseline_ledger_sha256,
            "spendable_total_cny": str(allocation.spendable_total_cny),
            "safety_reserve_cny": str(allocation.safety_reserve_cny),
            "remaining_spendable_cny": str(allocation.spendable_total_cny - ledger.spent_cny),
            "closed": ledger.active_reservation is None and not ledger.request_charges and ledger.spent_cny == 0,
        },
    }
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
    terminal_status: str = "runner_error"
    provider_requests: int = 0
    usage: dict[str, int] | None = None
    charge_cny: str = "0"
    candidate_sha256: str | None = None
    workspace_diff_sha256: str | None = None
    candidate_diff_identity: bool = False
    evaluator_report_sha256: str | None = None
    resolved: bool | None = None
    failure_classification: str | None = None
    active_reservation: Any = None


def _healthy_paid_result(result: PaidTaskResult) -> bool:
    return (
        result.runner_status == "completed"
        and result.candidate_status == "exported_nonempty"
        and result.candidate_diff_identity
        and result.evaluator_status == "completed"
        and result.provider_status != "provider_transport_error"
        and result.active_reservation is None
    )


def execute_paid_canary(*, root: Path, freeze: Path, paid_execution: bool, executor: Callable[[dict[str, str]], PaidTaskResult] | None = None) -> list[PaidTaskResult]:
    """Run the frozen tasks serially, stopping before task two on an unhealthy path."""
    if not paid_execution:
        return []
    if executor is None:
        raise ValueError("paid execution requires a configured Provider executor")
    payload = json.loads((freeze / "control-canary-freeze.json").read_text(encoding="utf-8"))
    if payload.get("payload_contract") != payload_contract(root.resolve()):
        raise ValueError("paid execution payload identity differs from Freeze")
    by_id = {item["instance_id"]: item for item in load_frozen_payloads(root.resolve())}
    results: list[PaidTaskResult] = []
    for task in payload["tasks"]:
        task_payload = by_id[str(task["instance_id"])]
        result = executor(task_payload)
        results.append(result)
        if not _healthy_paid_result(result):
            break
    return results


def _paid_pilot_config(freeze_payload: dict[str, Any]) -> PilotConfig:
    profile = ExperimentProfile(
        tool_loading="deferred", compression_profile="recovery_v1",
        permission_strategy="session_allow", agent_mode="single",
    )
    provider = freeze_payload["provider_contract"]
    return PilotConfig.model_validate({
        "schema_version": 2, "experiment_kind": "pilot", "provider": provider["provider"],
        "protocol": provider["protocol"], "base_url": provider["base_url"],
        "api_key_env": provider["provider_secret_name"], "model_id": provider["model_id"],
        "fallback_enabled": False, "retry_budget": 0, "task_ids": [], "repetitions": 1,
        "feature_flags": {}, "experiment_profile": profile.canonical_payload(),
        # PilotConfig keeps this schema-compatibility field at 50. The separate
        # Provider request bridge below remains the frozen 40-request limit.
        "max_iterations": 50,
        "model_parameters": {
            "temperature": None, "top_p": None, "max_output_tokens": None,
            "max_completion_tokens": MAX_OUTPUT_TOKENS, "enable_thinking": True,
            "thinking_budget": MAX_REASONING_TOKENS,
        },
    })


def _initialize_paid_agent_config(*, pilot: PilotConfig, home: Path) -> None:
    """Write and validate the same child configuration consumed by the Agent CLI."""
    _goal3_child_config(pilot=pilot, home=home)
    config = load_codepacex_config(home / ".codepacex" / "config.yaml")
    primary = config.providers[0]
    if (primary.name, primary.protocol, primary.base_url, primary.model) != (
        pilot.provider, pilot.protocol, pilot.base_url, pilot.model_id,
    ):
        raise ValueError("generated paid Agent configuration changed frozen identity")


def _fresh_paid_gate(*, root: Path, freeze: Path, artifact_root: Path, acknowledgement: str) -> PaidRunGate:
    if not acknowledgement:
        raise ValueError("paid execution requires an authorization acknowledgement")
    pricing = load_pricing(freeze / "pricing-snapshot.json")
    authorization = BudgetAuthorization(
        authorized_total_cny=RECOMMENDED_HARD_CAP_CNY,
        stage_limits_cny={"A": RECOMMENDED_HARD_CAP_CNY, "B": RECOMMENDED_HARD_CAP_CNY, "C": RECOMMENDED_HARD_CAP_CNY},
        pricing_snapshot_hash=pricing_snapshot_hash(pricing), experiment_commit=current_git_commit(root),
        authorized_at="single-control-canary-authorization", authorized_by="user",
    )
    authorization_path = artifact_root / "authorization.json"
    ledger_path = artifact_root / "ledger.json"
    allocation_path = artifact_root / "stage-c-compatibility-allocation.json"
    _write_json(authorization_path, authorization.model_dump(mode="json"))
    _write_json(artifact_root / "authorization-acknowledgement.json", {"acknowledgement": acknowledgement})
    ledger = BudgetLedger(authorization_hash=authorization_hash(authorization), updated_at="paid-canary-start")
    _write_json(ledger_path, ledger.model_dump(mode="json"))
    allocation = _fresh_rehearsal_allocation(authorization, ledger, pricing_snapshot_hash(pricing))
    _write_json(allocation_path, allocation.model_dump(mode="json"))
    return PaidRunGate(
        root=root, authorization_path=authorization_path, ledger_path=ledger_path,
        allocation_path=allocation_path, pricing_path=freeze / "pricing-snapshot.json",
        pricing=pricing, stage="C",
    )


def _live_task_executor(*, root: Path, freeze_payload: dict[str, Any], task: dict[str, str], metadata: dict[str, Any], gate: PaidRunGate, artifact_root: Path, run_id: str, payload_path: Path | None = None, trial_namespace: str = "v2-control") -> PaidTaskResult:
    task_root = artifact_root / "tasks" / task["instance_id"]
    workspace = task_root / "workspace"
    task_root.mkdir(parents=True)
    trial_id = f"swe/{trial_namespace}/{run_id}/{task['instance_id']}"
    prompt = _goal3_inference_prompt(task)
    source_payload = payload_path or root / PAYLOAD_DIRECTORY / f"{task['instance_id']}.json"
    evidence: dict[str, Any] = {
        "instance_id": task["instance_id"], "payload_sha256": _sha256(source_payload),
        "problem_statement_sha256": hashlib.sha256(task["problem_statement"].encode()).hexdigest(),
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "system_prompt_sha256": hashlib.sha256(build_static_system_instruction().encode()).hexdigest(),
        "pre_edit_test": None, "post_edit_test": None,
    }
    try:
        _goal3_materialize_instance(task, workspace)
        python, bootstrap = _bootstrap(
            workspace, metadata["preflight_dependencies"],
            editable_target=str(metadata.get("editable_target", ".")),
        )
        _write_json(task_root / "dependency-bootstrap.json", bootstrap)
        test_command = [str(python), "-m", "pytest", str(metadata["test_target"])]
        pre_edit = _run(test_command, cwd=workspace)
        (task_root / "pre-edit.stdout.txt").write_text(pre_edit.stdout, encoding="utf-8")
        (task_root / "pre-edit.stderr.txt").write_text(pre_edit.stderr, encoding="utf-8")
        evidence["pre_edit_test"] = {"command": test_command, "exit_code": pre_edit.returncode}
    except Exception as exc:
        evidence["failure"] = str(exc)
        _write_json(task_root / "task-result.json", evidence)
        return PaidTaskResult(task["instance_id"], "not_started", "not_exported", "not_run", "not_run", "error", "not_started", failure_classification="task_environment_blocked")
    pilot = _paid_pilot_config(freeze_payload)
    if not os.environ.get(pilot.api_key_env):
        raise ValueError("paid execution requires the configured Provider secret")
    with tempfile.TemporaryDirectory(prefix="codepacex-v2-control-home-") as home_text:
        home = Path(home_text)
        _initialize_paid_agent_config(pilot=pilot, home=home)
        profile_path = home / "profile.yaml"
        profile_path.write_text(yaml.safe_dump(pilot.experiment_profile.canonical_payload(), sort_keys=True), encoding="utf-8")
        environment = _child_environment(pilot, home_text, root=root)
        environment.update(provider_request_budget_environment(
            gate, trial_id=trial_id, maximum_input_tokens_per_request=MAX_INPUT_TOKENS,
            maximum_output_tokens_per_request=MAX_OUTPUT_TOKENS,
            maximum_reasoning_tokens_per_request=MAX_REASONING_TOKENS,
            maximum_provider_requests_per_trial=MAX_REQUESTS_PER_TASK,
        ))
        process = subprocess.run(
            [sys.executable, "-m", "codepacex", "-p", prompt, "--output-format", "stream-json", "--experiment-profile", str(profile_path), "--max-iterations", str(pilot.max_iterations)],
            cwd=workspace, env=environment, text=True, capture_output=True, timeout=1800, check=False,
        )
    (task_root / "agent.stdout.ndjson").write_text(process.stdout or "", encoding="utf-8")
    (task_root / "agent.stderr.txt").write_text(process.stderr or "", encoding="utf-8")
    accounting = gate.trial_accounting(trial_id)
    if accounting["budget_blocked"]:
        return PaidTaskResult(task["instance_id"], "not_completed", "not_exported", "not_run", "not_run", "blocked", "budget_blocked", terminal_status="budget_blocked", charge_cny=accounting["actual_cny"], failure_classification="budget_blocked", active_reservation=accounting["active_reservation"])
    if accounting["active_reservation"] is not None:
        return PaidTaskResult(task["instance_id"], "error", "not_exported", "not_run", "not_run", "error", "provider_transport_error", failure_classification="provider_transport_error", active_reservation=accounting["active_reservation"])
    patch = _goal3_extract_patch(workspace)
    patch_path = task_root / "candidate.patch"
    patch_path.write_text(patch, encoding="utf-8")
    candidate_sha = _sha256(patch_path)
    evidence.update({"agent_exit_code": process.returncode, "candidate_sha256": candidate_sha, "workspace_diff_sha256": hashlib.sha256(patch.encode()).hexdigest()})
    post_edit = _run(test_command, cwd=workspace)
    (task_root / "post-edit.stdout.txt").write_text(post_edit.stdout, encoding="utf-8")
    (task_root / "post-edit.stderr.txt").write_text(post_edit.stderr, encoding="utf-8")
    evidence["post_edit_test"] = {"command": test_command, "exit_code": post_edit.returncode}
    request_ceiling_reached = "ProviderRequestCeilingExceeded" in (process.stderr or "")
    if not patch.strip():
        evidence["failure_classification"] = (
            "request_ceiling_reached" if request_ceiling_reached else "agent_no_candidate"
        )
        _write_json(task_root / "task-result.json", evidence)
        return PaidTaskResult(
            task["instance_id"], "completed", "not_exported", "executed", "not_run",
            "completed", "completed", terminal_status=evidence["failure_classification"],
            provider_requests=accounting["request_count"], charge_cny=accounting["actual_cny"],
            candidate_sha256=candidate_sha, workspace_diff_sha256=candidate_sha,
            candidate_diff_identity=True, failure_classification=evidence["failure_classification"],
        )
    if process.returncode and not request_ceiling_reached:
        evidence["failure_classification"] = "runner_error"
        _write_json(task_root / "task-result.json", evidence)
        return PaidTaskResult(task["instance_id"], "error", "exported_nonempty", "executed", "not_run", "error", "completed", terminal_status="runner_error", provider_requests=accounting["request_count"], charge_cny=accounting["actual_cny"], candidate_sha256=candidate_sha, workspace_diff_sha256=candidate_sha, candidate_diff_identity=True, failure_classification="runner_error")
    prediction = task_root / "prediction.json"
    _write_json(prediction, [{"instance_id": task["instance_id"], "model_name_or_path": pilot.model_id, "model_patch": patch}])
    try:
        evaluator_run_id = f"{run_id}-{task['instance_id']}"
        evaluated = run_official_evaluator(dataset_name=SWE_ENVIRONMENT["dataset"], split=SWE_ENVIRONMENT["split"], predictions_path=prediction, instance_ids=[task["instance_id"]], max_workers=1, run_id=evaluator_run_id, namespace=SWE_ENVIRONMENT["evaluator_namespace"], cwd=task_root, evaluator_architecture="native")
        (task_root / "evaluator.stdout.txt").write_text((evaluated.stdout or "") + "\n" + (evaluated.stderr or ""), encoding="utf-8")
        if evaluated.returncode:
            raise RuntimeError(f"official evaluator exit {evaluated.returncode}")
        report = official_evaluator_report_path(cwd=task_root, run_id=evaluator_run_id, model_id=pilot.model_id, instance_id=task["instance_id"])
        resolved = collect_goal3_official_outcome(report, task["instance_id"])
        report_copy = task_root / "official-report.json"
        shutil.copyfile(report, report_copy)
    except RuntimeError as exc:
        status = "evaluator_unavailable" if "not installed" in str(exc).lower() else "evaluator_execution_error"
        return PaidTaskResult(task["instance_id"], "completed", "exported_nonempty", "executed", status, "error", "completed", provider_requests=accounting["request_count"], charge_cny=accounting["actual_cny"], candidate_sha256=candidate_sha, workspace_diff_sha256=candidate_sha, candidate_diff_identity=True, failure_classification=status)
    except (OSError, ValueError, subprocess.SubprocessError):
        return PaidTaskResult(task["instance_id"], "completed", "exported_nonempty", "executed", "evaluator_report_selection_error", "error", "completed", provider_requests=accounting["request_count"], charge_cny=accounting["actual_cny"], candidate_sha256=candidate_sha, workspace_diff_sha256=candidate_sha, candidate_diff_identity=True, failure_classification="evaluator_report_selection_error")
    terminal_status = "request_ceiling_reached" if request_ceiling_reached else "resolved" if resolved else "unresolved"
    evidence.update({"resolved": resolved, "official_report_sha256": _sha256(report_copy), "provider_requests": accounting["request_count"], "charge_cny": accounting["actual_cny"], "terminal_status": terminal_status})
    _write_json(task_root / "task-result.json", evidence)
    return PaidTaskResult(task["instance_id"], "completed", "exported_nonempty", "executed", "completed", "completed", "completed", terminal_status=terminal_status, provider_requests=accounting["request_count"], charge_cny=accounting["actual_cny"], candidate_sha256=candidate_sha, workspace_diff_sha256=candidate_sha, candidate_diff_identity=True, evaluator_report_sha256=_sha256(report_copy), resolved=resolved, failure_classification="request_ceiling_reached" if request_ceiling_reached else None)


def run_paid_canary(*, root: Path, freeze: Path, artifact_root: Path, expected_freeze_sha256: str, approved_hard_cap_cny: str, authorization_acknowledgement: str, run_id: str, executor: Callable[[dict[str, str]], PaidTaskResult] | None = None) -> dict[str, Any]:
    """The future paid path; every external authorization identity is explicit."""
    root, freeze, artifact_root = root.resolve(), freeze.resolve(), artifact_root.resolve()
    validate_freeze(root=root, freeze=freeze)
    freeze_sha = _sha256(freeze / "control-canary-freeze.json")
    if expected_freeze_sha256 != freeze_sha:
        raise ValueError("paid execution expected Freeze SHA does not match")
    if Decimal(approved_hard_cap_cny) != RECOMMENDED_HARD_CAP_CNY:
        raise ValueError("paid execution approved hard cap does not match the frozen CNY 15 contract")
    if not run_id or Path(run_id).name != run_id or artifact_root.exists():
        raise ValueError("paid execution requires a fresh safe Run ID and artifact root")
    artifact_root.mkdir(parents=True)
    frozen = json.loads((freeze / "control-canary-freeze.json").read_text(encoding="utf-8"))
    gate = _fresh_paid_gate(root=root, freeze=freeze, artifact_root=artifact_root, acknowledgement=authorization_acknowledgement)
    metadata = {item["instance_id"]: item for item in frozen["tasks"]}
    if executor is None:
        executor = lambda task: _live_task_executor(root=root, freeze_payload=frozen, task=task, metadata=metadata[task["instance_id"]], gate=gate, artifact_root=artifact_root, run_id=run_id)
    results = execute_paid_canary(root=root, freeze=freeze, paid_execution=True, executor=executor)
    ledger = BudgetLedger.model_validate_json(gate.ledger_path.read_text(encoding="utf-8"))
    summary = {
        "schema_version": SCHEMA_VERSION, "run_id": run_id, "freeze_sha256": freeze_sha,
        "runtime_contract_hash": frozen["runtime_contract_hash"],
        "payload_manifest_sha256": frozen["payload_contract"]["manifest_sha256"],
        "ledger_budget_stage": "C", "ledger_stage_note": "generic gate compatibility only; not a Stage C experiment or historical Stage C ledger",
        "results": [result.__dict__ for result in results], "provider_requests": len(ledger.request_charges),
        "usage": sum(charge.input_tokens + charge.output_tokens for charge in ledger.request_charges),
        "charge_cny": str(ledger.spent_cny), "active_reservation": None if ledger.active_reservation is None else ledger.active_reservation.model_dump(mode="json"),
        "ledger_closed": ledger.active_reservation is None,
        "completed": len(results) == len(TASKS) and all(_healthy_paid_result(result) for result in results),
    }
    _write_json(artifact_root / "paid-canary-summary.json", summary)
    return summary


def v2_2_gate(results: Sequence[dict[str, Any]], *, ledger_closed: bool) -> dict[str, Any]:
    """Evaluate the fixed V2.2 decision rule without selecting a V2.2 task."""
    reasons: list[str] = []
    candidates = [item.get("candidate_status") == "exported_nonempty" for item in results]
    scorable = [item.get("evaluator_status") == "completed" for item in results]
    infrastructure = [
        item for item in results if item.get("terminal_status") in {
            "provider_transport_error", "runner_error", "task_environment_blocked",
            "evaluator_unavailable", "evaluator_execution_error", "evaluator_report_selection_error",
        }
    ]
    positive = any(item.get("terminal_status") == "resolved" for item in results) or any(
        (item.get("post_edit_test") or {}).get("exit_code") == 0 for item in results
    )
    if len(results) != len(TASKS): reasons.append("not_all_control_tasks_completed")
    if not all(candidates): reasons.append("candidate_missing")
    if not all(scorable): reasons.append("official_evaluator_not_scorable")
    if infrastructure: reasons.append("infrastructure_failure")
    if not ledger_closed: reasons.append("ledger_not_closed")
    if not positive: reasons.append("no_positive_capability_signal")
    return {
        "status": "V2_2_DIAGNOSTIC_PILOT_GO" if not reasons else "V2_2_DIAGNOSTIC_PILOT_NO_GO",
        "reasons": reasons,
        "candidate_count": sum(candidates), "scorable_count": sum(scorable),
        "infrastructure_failure_count": len(infrastructure), "ledger_closed": ledger_closed,
    }


def summarize_canary_artifact(*, artifact_root: Path, output: Path | None = None) -> dict[str, Any]:
    """Compile a small machine-readable and Markdown receipt for a paid or shadow Artifact."""
    artifact_root = artifact_root.resolve()
    source = next((artifact_root / name for name in ("paid-canary-summary.json", "shadow-canary-summary.json") if (artifact_root / name).is_file()), None)
    if source is None:
        raise ValueError("Canary Artifact has no paid or shadow summary")
    source_summary = json.loads(source.read_text(encoding="utf-8"))
    results = list(source_summary.get("results", []))
    for item in results:
        task_path = artifact_root / "tasks" / str(item["instance_id"]) / "task-result.json"
        if task_path.is_file():
            item.update({"evidence_path": str(task_path.relative_to(artifact_root)), **json.loads(task_path.read_text(encoding="utf-8"))})
    ledger_path = artifact_root / "ledger.json"
    ledger = BudgetLedger.model_validate_json(ledger_path.read_text(encoding="utf-8")) if ledger_path.is_file() else None
    closed = ledger is not None and ledger.active_reservation is None
    gate = v2_2_gate(results, ledger_closed=closed)
    summary = {
        "schema_version": SCHEMA_VERSION, "source_summary": source.name,
        "tasks": results, "total_provider_requests": source_summary.get("provider_requests", 0),
        "total_usage": source_summary.get("usage", 0), "total_charge_cny": source_summary.get("charge_cny", "0"),
        "hard_cap_cny": str(RECOMMENDED_HARD_CAP_CNY),
        "hard_cap_respected": Decimal(str(source_summary.get("charge_cny", "0"))) <= RECOMMENDED_HARD_CAP_CNY,
        "active_reservation": None if ledger is None or ledger.active_reservation is None else ledger.active_reservation.model_dump(mode="json"),
        "ledger_closed": closed, "candidate_count": sum(item.get("candidate_status") == "exported_nonempty" for item in results),
        "scorable_count": sum(item.get("evaluator_status") == "completed" for item in results),
        "infrastructure_failure_count": gate["infrastructure_failure_count"], "v2_2_gate": gate,
        "go_no_go": "GO" if source_summary.get("completed") else "NO_GO",
    }
    destination = output.resolve() if output is not None else artifact_root / "canary-result-summary.json"
    _write_json(destination, summary)
    markdown = "# Evaluation V2 Control Canary Summary\n\n" + "\n".join(
        [f"- Overall: {summary['go_no_go']}", f"- V2.2 Gate: {gate['status']}",
         f"- Candidate / scorable: {summary['candidate_count']} / {summary['scorable_count']}",
         f"- Provider requests / usage / charge: {summary['total_provider_requests']} / {summary['total_usage']} / {summary['total_charge_cny']}",
         f"- Ledger closed: {summary['ledger_closed']}"]
    ) + "\n"
    (destination.with_suffix(".md")).write_text(markdown, encoding="utf-8")
    return summary


def run_shadow_canary(*, root: Path, freeze: Path, preflight_summary: Path, artifact_root: Path, run_id: str) -> dict[str, Any]:
    """Exercise the paid data path with deterministic simulated Provider usage only."""
    root, freeze, artifact_root = root.resolve(), freeze.resolve(), artifact_root.resolve()
    validate_freeze(root=root, freeze=freeze)
    if not json.loads(preflight_summary.read_text(encoding="utf-8")).get("passed"):
        raise ValueError("shadow Canary requires both task environment preflights")
    if not run_id or Path(run_id).name != run_id or artifact_root.exists():
        raise ValueError("shadow Canary requires a fresh safe Run ID and artifact root")
    artifact_root.mkdir(parents=True)
    frozen = json.loads((freeze / "control-canary-freeze.json").read_text(encoding="utf-8"))
    gate = _fresh_paid_gate(root=root, freeze=freeze, artifact_root=artifact_root, acknowledgement="zero-provider-shadow-deterministic-replay")
    pilot = _paid_pilot_config(frozen)
    with tempfile.TemporaryDirectory(prefix="codepacex-v2-control-shadow-home-") as home_text:
        _initialize_paid_agent_config(pilot=pilot, home=Path(home_text))
    outcomes = {TASKS[0]["instance_id"]: False, TASKS[1]["instance_id"]: True}

    def executor(task: dict[str, str]) -> PaidTaskResult:
        instance_id = task["instance_id"]
        task_root = artifact_root / "tasks" / instance_id
        task_root.mkdir(parents=True)
        reservation = gate.reserve(f"swe/v2-control-shadow/{run_id}/{instance_id}", maximum_requests=1, maximum_input_tokens_per_request=MAX_INPUT_TOKENS, maximum_output_tokens_per_request=MAX_OUTPUT_TOKENS)
        settlement = gate.settle(reservation, request_usages=[(100_000, 2_000)])
        patch = f"diff --git a/shadow/{instance_id}.txt b/shadow/{instance_id}.txt\n+shadow deterministic candidate\n"
        patch_path = task_root / "candidate.patch"; patch_path.write_text(patch, encoding="utf-8")
        candidate_sha = _sha256(patch_path)
        evaluator_run_id, model_id = f"{run_id}-{instance_id}", "evaluation-v2-shadow"
        report = task_root / "logs" / "run_evaluation" / evaluator_run_id / model_id / instance_id / "report.json"
        _write_json(report, {instance_id: {"patch_is_None": False, "patch_exists": True, "patch_successfully_applied": True, "resolved": outcomes[instance_id], "tests_status": {}}})
        selected = official_evaluator_report_path(cwd=task_root, run_id=evaluator_run_id, model_id=model_id, instance_id=instance_id)
        resolved = collect_goal3_official_outcome(selected, instance_id)
        evidence = {
            "instance_id": instance_id, "provider_executor": "deterministic_replay_no_transport",
            "provider_requests": 0, "simulated_provider_requests": 1,
            "pre_edit_test": {"command": ["deterministic-shadow", instance_id, "pre"], "exit_code": 1},
            "post_edit_test": {"command": ["deterministic-shadow", instance_id, "post"], "exit_code": 0},
            "candidate_sha256": candidate_sha, "workspace_diff_sha256": candidate_sha,
            "candidate_diff_identity": True, "official_report_sha256": _sha256(selected), "resolved": resolved,
            "pilot_max_iterations": pilot.max_iterations,
            "provider_request_ceiling_per_task": MAX_REQUESTS_PER_TASK,
            "settlement": settlement.model_dump(mode="json"), "environment_blocker": None,
        }
        _write_json(task_root / "task-result.json", evidence)
        return PaidTaskResult(instance_id=instance_id, agent_status="completed", candidate_status="exported_nonempty", validation_status="executed", evaluator_status="completed", runner_status="completed", provider_status="simulated_settled", terminal_status="resolved" if resolved else "unresolved", provider_requests=1, usage={"input_tokens": 100_000, "output_tokens": 2_000}, charge_cny=str(settlement.actual_cny), candidate_sha256=candidate_sha, workspace_diff_sha256=candidate_sha, candidate_diff_identity=True, evaluator_report_sha256=_sha256(selected), resolved=resolved)

    results = execute_paid_canary(root=root, freeze=freeze, paid_execution=True, executor=executor)
    ledger = BudgetLedger.model_validate_json(gate.ledger_path.read_text(encoding="utf-8"))
    if len(results) != len(TASKS) or ledger.active_reservation is not None:
        raise RuntimeError("shadow Canary did not complete the serial paid-path contract")
    simulated_usage = sum(charge.input_tokens + charge.output_tokens for charge in ledger.request_charges)
    summary = {
        "schema_version": SCHEMA_VERSION, "run_id": run_id, "paid_execution": False,
        "provider_transport": "deterministic_replay_no_transport", "provider_secret_read": False,
        "provider_requests": 0, "usage": 0, "charge_cny": "0",
        "simulated_provider_requests": len(ledger.request_charges), "simulated_usage": simulated_usage,
        "simulated_charge_cny": str(ledger.spent_cny), "freeze_sha256": _sha256(freeze / "control-canary-freeze.json"),
        "runtime_contract_hash": frozen["runtime_contract_hash"], "payload_manifest_sha256": frozen["payload_contract"]["manifest_sha256"],
        "pilot_max_iterations": pilot.max_iterations, "provider_request_ceiling_per_task": MAX_REQUESTS_PER_TASK,
        "results": [item.__dict__ for item in results], "active_reservation": None,
        "ledger_closed": True, "completed": all(_healthy_paid_result(item) for item in results),
    }
    _write_json(artifact_root / "shadow-canary-summary.json", summary)
    summarize_canary_artifact(artifact_root=artifact_root)
    return summary


def release_check(*, root: Path, freeze: Path, preflight_summary: Path, ledger_path: Path | None = None) -> dict[str, Any]:
    """Return the exact paid-ready state and dispatch inputs without dispatching."""
    root = root.resolve()
    head = current_git_commit(root)
    remote_result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "refs/remotes/origin/main^{commit}"],
        text=True, capture_output=True, check=False,
    )
    remote = remote_result.stdout.strip()
    status = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], text=True, capture_output=True, check=False).stdout
    blockers: list[str] = []
    try: validated = validate_freeze(root=root, freeze=freeze)
    except ValueError as exc: validated = {"valid": False}; blockers.append(str(exc))
    payload = json.loads((freeze / "control-canary-freeze.json").read_text(encoding="utf-8"))
    preflight = json.loads(preflight_summary.read_text(encoding="utf-8"))
    if remote_result.returncode or not re.fullmatch(r"[0-9a-f]{40}", remote):
        remote = None
        blockers.append("origin_main_ref_unavailable")
    elif head != remote:
        blockers.append("head_is_not_origin_main")
    if status: blockers.append("worktree_not_clean")
    if not preflight.get("passed"): blockers.append("environment_preflight_not_passed")
    active = None
    if ledger_path is not None and ledger_path.is_file(): active = BudgetLedger.model_validate_json(ledger_path.read_text(encoding="utf-8")).active_reservation
    if active is not None: blockers.append("active_reservation_exists")
    runtime = payload["runtime_contract"]
    return {
        "status": "READY_FOR_PAID_CANARY" if not blockers else blockers[0], "blockers": blockers,
        "head": head, "origin_main": remote, "head_is_origin_main": head == remote, "git_status": status, "freeze_valid": validated.get("valid", False),
        "system_instruction_sha256": runtime["system_instruction_sha256"], "freeze_sha256": _sha256(freeze / "control-canary-freeze.json"),
        "runtime_contract_sha256": payload["runtime_contract_hash"], "pricing_sha256": payload["budget_contract"]["pricing_snapshot_hash"],
        "payload_manifest_sha256": payload["payload_contract"]["manifest_sha256"], "tasks": [item["instance_id"] for item in payload["tasks"]],
        "preflight_passed": preflight.get("passed"), "hard_cap_cny": str(RECOMMENDED_HARD_CAP_CNY),
        "request_ceiling_per_task": MAX_REQUESTS_PER_TASK, "strict_serial": True, "fallback": False, "retry": 0,
        "workflow_inputs": {"paid_execution": "true", "expected_freeze_sha256": _sha256(freeze / "control-canary-freeze.json"), "approved_hard_cap_cny": str(RECOMMENDED_HARD_CAP_CNY), "authorization_acknowledgement": "REPLACE_WITH_USER_AUTHORIZATION_ACKNOWLEDGEMENT", "run_id": "REPLACE_WITH_FRESH_RUN_ID"},
        "paid_job_explicit_authorization_required": True, "active_reservation": None if active is None else active.model_dump(mode="json"),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluation V2 Control Canary zero-provider controls")
    sub = parser.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("freeze"); freeze.add_argument("--root", type=Path, required=True); freeze.add_argument("--output", type=Path, required=True)
    validate = sub.add_parser("validate"); validate.add_argument("--root", type=Path, required=True); validate.add_argument("--freeze", type=Path, required=True)
    preflight = sub.add_parser("preflight"); preflight.add_argument("--freeze", type=Path, required=True); preflight.add_argument("--artifact-root", type=Path, required=True)
    rehearsal = sub.add_parser("rehearse"); rehearsal.add_argument("--root", type=Path, required=True); rehearsal.add_argument("--freeze", type=Path, required=True); rehearsal.add_argument("--preflight-summary", type=Path, required=True); rehearsal.add_argument("--artifact-root", type=Path, required=True)
    shadow = sub.add_parser("shadow"); shadow.add_argument("--root", type=Path, required=True); shadow.add_argument("--freeze", type=Path, required=True); shadow.add_argument("--preflight-summary", type=Path, required=True); shadow.add_argument("--artifact-root", type=Path, required=True); shadow.add_argument("--run-id", required=True)
    summary = sub.add_parser("summary"); summary.add_argument("--artifact-root", type=Path, required=True); summary.add_argument("--output", type=Path)
    release = sub.add_parser("release-check"); release.add_argument("--root", type=Path, required=True); release.add_argument("--freeze", type=Path, required=True); release.add_argument("--preflight-summary", type=Path, required=True); release.add_argument("--ledger", type=Path); release.add_argument("--output", type=Path)
    paid = sub.add_parser("paid-run"); paid.add_argument("--root", type=Path, required=True); paid.add_argument("--freeze", type=Path, required=True); paid.add_argument("--artifact-root", type=Path, required=True); paid.add_argument("--expected-freeze-sha256", required=True); paid.add_argument("--approved-hard-cap-cny", required=True); paid.add_argument("--authorization-acknowledgement", required=True); paid.add_argument("--run-id", required=True); paid.add_argument("--confirm-paid-execution", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "freeze": result = write_freeze(root=args.root, output=args.output)
    elif args.command == "validate": result = validate_freeze(root=args.root, freeze=args.freeze)
    elif args.command == "preflight": result = run_environment_preflight(freeze=args.freeze, artifact_root=args.artifact_root)
    elif args.command == "rehearse": result = rehearse_paid_path(root=args.root, freeze=args.freeze, preflight_summary=args.preflight_summary, artifact_root=args.artifact_root)
    elif args.command == "shadow": result = run_shadow_canary(root=args.root, freeze=args.freeze, preflight_summary=args.preflight_summary, artifact_root=args.artifact_root, run_id=args.run_id)
    elif args.command == "summary": result = summarize_canary_artifact(artifact_root=args.artifact_root, output=args.output)
    elif args.command == "release-check":
        result = release_check(root=args.root, freeze=args.freeze, preflight_summary=args.preflight_summary, ledger_path=args.ledger)
        if args.output is not None: _write_json(args.output, result)
    else:
        if not args.confirm_paid_execution:
            raise ValueError("paid execution requires --confirm-paid-execution")
        result = run_paid_canary(root=args.root, freeze=args.freeze, artifact_root=args.artifact_root, expected_freeze_sha256=args.expected_freeze_sha256, approved_hard_cap_cny=args.approved_hard_cap_cny, authorization_acknowledgement=args.authorization_acknowledgement, run_id=args.run_id)
    print(json.dumps(result, sort_keys=True)); return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from evals.evaluation_v2 import full_replay
from evals.paid_gate import BudgetLedger


ROOT = Path(__file__).resolve().parents[1]


def test_goal4_full_payloads_are_exact_safe_projection_and_preserve_order() -> None:
    tasks = full_replay.load_tasks(ROOT)
    manifest = full_replay.build_payload_manifest(ROOT)
    assert [item["instance_id"] for item in tasks] == list(full_replay.GOAL4_ORDER)
    assert all(set(item) == full_replay.SAFE_FIELDS for item in tasks)
    assert [item["instance_id"] for item in manifest["payloads"]] == list(full_replay.GOAL4_ORDER)
    assert all(
        item["agent_visible_payload_sha256"] == item["goal4_execution_payload_sha256"]
        for item in manifest["payloads"]
    )
    assert manifest["source"]["extraction_contract"] == "exact-seven-agent-visible-fields-no-gold-v1"


def test_diagnostic_six_is_unresolved_taxonomy_backed_and_size_stratified() -> None:
    selection = full_replay.build_selection_manifest(ROOT)
    assert selection["phase_a_order"] == list(full_replay.PHASE_A_IDS)
    assert selection["phase_b_order"] == list(full_replay.PHASE_B_IDS)
    assert all(item["goal4_outcome"] == "unresolved" for item in selection["tasks"])
    assert sorted(item["goal4_failure_category"] for item in selection["tasks"]) == sorted([
        "incomplete_patch", "incomplete_patch", "regression_introduced",
        "root_cause_localization_failure", "cross_file_propagation_missed",
        "request_ceiling_exhausted",
    ])
    assert {item["size_bucket"] for item in selection["tasks"]} == {
        "one_file", "two_to_four_files", "five_plus_files",
    }


def test_committed_full_replay_contract_is_canonical_and_freezes_budget_semantics() -> None:
    result = full_replay.validate_contract(ROOT)
    frozen = json.loads((ROOT / full_replay.COMMITTED_FREEZE).read_text(encoding="utf-8"))
    assert result["valid"] is True
    assert frozen["logical_goal4_order"] == list(full_replay.GOAL4_ORDER)
    assert frozen["phase_a_diagnostic_ids"] == list(full_replay.PHASE_A_IDS)
    assert frozen["budget_contract"]["provider_request_ceiling_per_task"] == 40
    assert frozen["budget_contract"]["agent_max_iterations"] == 50
    assert frozen["budget_contract"]["full_20_recommended_hard_cap_cny"] == "250.000000"
    assert frozen["gold_patch_forbidden"] is True


def test_environment_normalization_covers_ci_specific_bootstrap_and_selector_requirements() -> None:
    contracts = full_replay._task_environment_contract(ROOT)
    assert contracts["aws-cloudformation__cfn-lint-3749"]["test_target"].endswith(
        "test_language_extensions.py"
    )
    assert contracts["cyclotruc__gitingest-134"]["test_target"].endswith(
        "test_parse_patterns_valid"
    )
    assert contracts["deepset-ai__haystack-8489"]["dependencies"] == [
        "ddtrace==2.15.0rc2", "opentelemetry-sdk",
    ]
    assert contracts["bridgecrewio__checkov-6893"]["dependencies"] == [
        "pytest-mock", "pytest-xdist", "parameterized",
    ]
    assert contracts["delgan__loguru-1297"]["dependencies"] == [
        "freezegun==1.5.0", "pytest-mypy-plugins==3.2.0",
    ]
    assert contracts["delgan__loguru-1306"]["dependencies"] == ["freezegun==1.5.0"]
    assert contracts["deepset-ai__haystack-8525"]["dependencies"] == []


def test_paid_and_preflight_use_identical_canonical_environment_plans() -> None:
    tasks = {item["instance_id"]: item for item in full_replay.load_tasks(ROOT)}
    contracts = full_replay._task_environment_contract(ROOT)
    plans = [full_replay.canonical_task_environment_plan(tasks[item], contracts[item]) for item in full_replay.GOAL4_ORDER]
    assert [plan["instance_id"] for plan in plans] == list(full_replay.GOAL4_ORDER)
    assert plans[0] == {
        "instance_id": "aws-cloudformation__cfn-lint-3749",
        "editable_target": ".[test]",
        "dependencies": [],
        "test_target": "test/unit/module/template/transforms/test_language_extensions.py",
    }


def test_full_paid_executor_passes_the_canonical_plan_to_the_shared_runner(tmp_path: Path) -> None:
    task = next(item for item in full_replay.load_tasks(ROOT) if item["instance_id"] == full_replay.GOAL4_ORDER[0])
    metadata = full_replay._task_environment_contract(ROOT)
    captured: dict[str, object] = {}

    def fake_executor(**kwargs):
        captured.update(kwargs)
        return full_replay.control_canary.PaidTaskResult(
            task["instance_id"], "not_started", "not_exported", "not_run", "not_run",
            "not_started", "not_started",
        )

    with patch.object(full_replay.control_canary, "_live_task_executor", fake_executor):
        full_replay._full_task_executor(
            ROOT, {}, metadata, object(), tmp_path, "run-id", task,
        )
    assert captured["metadata"] == {
        "preflight_dependencies": [],
        "editable_target": ".[test]",
        "test_target": "test/unit/module/template/transforms/test_language_extensions.py",
    }


def test_preflight_persists_collection_execution_and_artifact_evidence(
    tmp_path: Path, monkeypatch,
) -> None:
    task = full_replay.load_tasks(ROOT)[0]
    contract = {"editable_target": ".", "dependencies": [], "test_target": "tests/test_smoke.py"}

    def materialize(_task: dict[str, object], workspace: Path) -> None:
        (workspace / ".git").mkdir(parents=True)

    def bootstrap(workspace: Path, _contract: dict[str, object]):
        python = workspace / "python"
        return python, [{"command": ["pip"], "exit_code": 0, "stdout": "", "stderr": ""}]

    calls: list[list[str]] = []
    def run(command, *, cwd: Path, timeout: int = 1800):
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, "collected 1 item\n1 passed\n", "")

    monkeypatch.setattr(full_replay, "_bootstrap", bootstrap)
    monkeypatch.setattr(full_replay, "_run", run)
    monkeypatch.setattr(full_replay.shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    result = full_replay.preflight_task(task, contract, work_root=tmp_path, materializer=materialize)
    assert result["environment_status"] == "ready"
    assert result["task_workspace_materialized"] is True
    assert result["dependencies_installed"] is True
    assert result["test_collection_completed"] is True
    assert result["meaningful_test_executed"] is True
    assert result["environment_blocker"] is None
    assert any("--collect-only" in command for command in calls)
    assert (tmp_path / task["instance_id"] / "evidence" / "pre-edit.stdout.txt").is_file()


def test_phase_b_admission_continues_capability_outcomes_and_stops_infrastructure(tmp_path: Path) -> None:
    capability = [
        full_replay.control_canary.PaidTaskResult(
            instance_id=instance_id, agent_status="completed_without_candidate", candidate_status="not_exported",
            validation_status="executed", evaluator_status="not_run", runner_status="completed",
            provider_status="completed", terminal_status="agent_no_candidate", provider_requests=1,
            live_executor_invoked=True, agent_dispatch_started=True,
            provider_client_initialized=True, model_response_observed=True, settlement_count=1,
        ) for instance_id in full_replay.PHASE_A_IDS
    ]
    authorization = full_replay.BudgetAuthorization(
        authorized_total_cny=full_replay.TOTAL_HARD_CAP_CNY,
        stage_limits_cny={"A": full_replay.PHASE_A_HARD_CAP_CNY, "B": full_replay.TOTAL_HARD_CAP_CNY, "C": full_replay.TOTAL_HARD_CAP_CNY},
        pricing_snapshot_hash=full_replay.budget_contract(ROOT)["pricing_snapshot_sha256"],
        experiment_commit="a" * 40, authorized_at="test", authorized_by="user",
    )
    ledger = BudgetLedger(authorization_hash=full_replay.authorization_hash(authorization), updated_at="test")
    assert full_replay.phase_b_admission(capability, ledger, ROOT)["admitted"] is True
    capability[-1].terminal_status = "evaluator_execution_error"
    assert "phase_a_infrastructure_failure" in full_replay.phase_b_admission(capability, ledger, ROOT)["blockers"]


def test_full_shadow_exercises_real_executor_seam_and_covers_all_tasks(
    tmp_path: Path, monkeypatch,
) -> None:
    preflight = tmp_path / "preflight.json"
    preflight.write_text(json.dumps({"passed": True, "ready_count": 20}), encoding="utf-8")

    def shadow_task(root, frozen, metadata, gate, artifact_root, run_id, task, scenario):
        requests = 40 if scenario.startswith("ceiling_") else 1
        trial_id = f"swe/v2-full-20/{run_id}/{task['instance_id']}"
        for _ in range(requests):
            reservation = gate.reserve(
                trial_id, maximum_requests=1,
                maximum_input_tokens_per_request=full_replay.MAX_INPUT_TOKENS,
                maximum_output_tokens_per_request=full_replay.MAX_OUTPUT_TOKENS,
            )
            gate.settle(reservation, request_usages=[(1, 1)])
        candidate = scenario == "ceiling_with_candidate"
        terminal = "request_ceiling_reached" if scenario.startswith("ceiling_") else "agent_no_candidate"
        return full_replay.control_canary.PaidTaskResult(
            instance_id=task["instance_id"],
            agent_status="completed_with_candidate" if candidate else "completed_without_candidate",
            candidate_status="exported_nonempty" if candidate else "not_exported",
            validation_status="executed", evaluator_status="completed" if candidate else "not_run",
            runner_status="completed",
            provider_status="pre_transport_blocked" if scenario.startswith("ceiling_") else "completed",
            terminal_status=terminal, provider_requests=requests,
            candidate_sha256="a" * 64 if candidate else None,
            workspace_diff_sha256="a" * 64 if candidate else None,
            candidate_diff_identity=candidate,
            evaluator_report_sha256="b" * 64 if candidate else None,
            live_executor_invoked=True, agent_dispatch_started=True,
            provider_client_initialized=True, model_response_observed=True,
            agent_exit_code=1 if scenario.startswith("ceiling_") else 0,
            settlement_count=requests, trial_id=trial_id,
        ), requests

    monkeypatch.setattr(full_replay, "_shadow_task", shadow_task)
    with patch("evals.paid_gate._git_is_clean", return_value=True):
        result = full_replay.run_shadow(ROOT, preflight, tmp_path / "shadow", "shadow-test")
    assert result["paid_execution"] is False
    assert result["provider_requests"] == result["usage"] == 0
    assert result["charge_cny"] == "0"
    assert result["phase_a_completed"] and result["phase_b_admitted"] and result["phase_b_completed"]
    assert len(result["results"]) == 20
    ceiling = result["results"][0]
    assert ceiling["terminal_status"] == "request_ceiling_reached"
    assert ceiling["candidate_status"] == "exported_nonempty"
    assert ceiling["evaluator_status"] == "completed"
    assert result["agent_dispatch_count"] == 20
    assert result["provider_task_coverage"] == "20/20"
    assert result["historical_control_dispatch_covered"] is True
    assert all(item["simulated_provider_requests"] >= 1 for item in result["results"])
    assert result["ledger_closed"] and result["active_reservation"] is None
    ledger = BudgetLedger.model_validate_json((tmp_path / "shadow" / "ledger.json").read_text(encoding="utf-8"))
    assert ledger.active_reservation is None
    assert len(ledger.request_charges) == len(ledger.settlements) == 98


def test_dispatch_missing_is_an_infrastructure_stop() -> None:
    result = full_replay.control_canary.PaidTaskResult(
        instance_id="task", agent_status="failed", candidate_status="not_exported",
        validation_status="not_run", evaluator_status="not_run", runner_status="error",
        provider_status="not_started", terminal_status="agent_dispatch_missing",
        live_executor_invoked=True, agent_dispatch_started=True,
    )
    authorization = full_replay.BudgetAuthorization(
        authorized_total_cny=full_replay.TOTAL_HARD_CAP_CNY,
        stage_limits_cny={"A": full_replay.PHASE_A_HARD_CAP_CNY, "B": full_replay.TOTAL_HARD_CAP_CNY, "C": full_replay.TOTAL_HARD_CAP_CNY},
        pricing_snapshot_hash=full_replay.budget_contract(ROOT)["pricing_snapshot_sha256"],
        experiment_commit="a" * 40, authorized_at="test", authorized_by="user",
    )
    ledger = BudgetLedger(authorization_hash=full_replay.authorization_hash(authorization), updated_at="test")
    assert full_replay._phase_is_healthy_for_continuation(result, ledger) is False


def test_full_replay_workflow_keeps_paid_path_explicit_and_zero_provider_path_complete() -> None:
    workflow = (ROOT / full_replay.WORKFLOW_PATH).read_text(encoding="utf-8")
    assert "inputs.paid_execution == false" in workflow
    assert "BAILIAN_API_KEY: ${{ secrets.BAILIAN_API_KEY }}" in workflow
    assert "full_replay preflight" in workflow
    assert "full_replay shadow" in workflow
    assert "full_replay paid-run --confirm-paid-execution" in workflow
    assert "expected_freeze_sha256" in workflow
    assert "approved_total_hard_cap_cny" in workflow
    assert workflow.count("python-version: '3.11'") == 2

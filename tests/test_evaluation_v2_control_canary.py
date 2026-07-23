from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.evaluation_v2 import control_canary as canary


def test_control_task_order_and_budget_contract_are_fixed(tmp_path: Path) -> None:
    assert [task["instance_id"] for task in canary.TASKS] == ["beetbox__beets-5495", "beancount__beancount-931"]
    pricing = tmp_path / "evals" / "goal2"
    pricing.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / canary.PRICING_PATH
    (pricing / source.name).write_bytes(source.read_bytes())
    contract = canary.budget_contract(tmp_path)
    assert contract["one_request_theoretical_maximum_cny"] == "1.830912"
    assert contract["per_task_theoretical_maximum_cny"] == "73.236480"
    assert contract["two_task_theoretical_maximum_cny"] == "146.472960"
    assert contract["historical_goal4_control_cost_cny"] == "4.307916"
    assert contract["recommended_hard_cap_cny"] == "15.000000"


def test_freeze_validator_detects_contract_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "freeze"
    written = canary.write_freeze(root=root, output=output)
    assert canary.validate_freeze(root=root, freeze=output)["valid"] is True
    payload = json.loads((output / "control-canary-freeze.json").read_text(encoding="utf-8"))
    payload["tasks"].reverse()
    (output / "control-canary-freeze.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="Freeze differs"):
        canary.validate_freeze(root=root, freeze=output)
    assert written["freeze_sha256"]


def test_environment_blockers_are_precise() -> None:
    missing = canary.subprocess.CompletedProcess([], 4, "", "ModuleNotFoundError: No module named 'mediafile'")
    assert canary._environment_blocker(missing) == "missing_python_dependency"
    collect = canary.subprocess.CompletedProcess([], 4, "ERROR collecting", "")
    assert canary._environment_blocker(collect) == "pytest_collection_error"
    baseline_failure = canary.subprocess.CompletedProcess([], 1, "collected 1 items", "")
    assert canary._environment_blocker(baseline_failure) is None


def test_preflight_persists_test_evidence_and_accepts_a_project_baseline_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def materialize(task: dict[str, str], workspace: Path) -> None:
        workspace.mkdir(parents=True)

    def bootstrap(workspace: Path) -> tuple[Path, list[dict[str, object]]]:
        python = workspace / "python"
        return python, [{"command": [str(python), "-m", "pip", "install", "-e", ".", "pytest"], "exit_code": 0, "stdout": "installed", "stderr": ""}]

    def run(command: list[str], *, cwd: Path, timeout: int = 1200) -> canary.subprocess.CompletedProcess[str]:
        return canary.subprocess.CompletedProcess(command, 1, "collected 1 item\n1 failed", "")

    monkeypatch.setattr(canary, "_goal3_materialize_instance", materialize)
    monkeypatch.setattr(canary, "_bootstrap", bootstrap)
    monkeypatch.setattr(canary, "_run", run)
    task = dict(canary.TASKS[0])
    result = canary.preflight_task(task, work_root=tmp_path)
    evidence = tmp_path / "evidence" / task["instance_id"]
    assert result["task_workspace_materialized"] is True
    assert result["dependencies_installed"] is True
    assert result["test_collection_completed"] is True
    assert result["meaningful_test_executed"] is True
    assert result["environment_blocker"] is None
    assert json.loads((evidence / "test-command.json").read_text())["command"][-1] == task["test_command"]
    assert (evidence / "dependency-bootstrap.json").is_file()
    assert (evidence / "test.stdout.txt").read_text() == "collected 1 item\n1 failed"


def test_preflight_reports_the_phase_of_a_materialization_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def reject(task: dict[str, str], workspace: Path) -> None:
        raise ValueError("git transport error")

    monkeypatch.setattr(canary, "_goal3_materialize_instance", reject)
    result = canary.preflight_task(dict(canary.TASKS[0]), work_root=tmp_path)
    assert result["environment_blocker"] == "workspace_materialization_failed"
    assert result["error"] == "git transport error"


def test_future_paid_runner_stops_before_second_task_when_first_is_unhealthy(tmp_path: Path) -> None:
    freeze = tmp_path / "freeze"
    freeze.mkdir()
    (freeze / "control-canary-freeze.json").write_text(json.dumps({"tasks": list(canary.TASKS)}), encoding="utf-8")
    calls: list[str] = []

    def executor(task: dict[str, str]) -> canary.PaidTaskResult:
        calls.append(task["instance_id"])
        return canary.PaidTaskResult(task["instance_id"], "completed", "exported_nonempty", "executed", "evaluator_execution_error", "error", "zero_provider")

    results = canary.execute_paid_canary(freeze=freeze, paid_execution=True, executor=executor)
    assert len(results) == len(calls) == 1
    assert calls == ["beetbox__beets-5495"]


def test_paid_runner_requires_explicit_executor(tmp_path: Path) -> None:
    freeze = tmp_path / "freeze"
    freeze.mkdir()
    (freeze / "control-canary-freeze.json").write_text(json.dumps({"tasks": list(canary.TASKS)}), encoding="utf-8")
    assert canary.execute_paid_canary(freeze=freeze, paid_execution=False) == []
    with pytest.raises(ValueError, match="separately authorized"):
        canary.execute_paid_canary(freeze=freeze, paid_execution=True)

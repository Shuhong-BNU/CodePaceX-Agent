from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from evals.evaluation_v2 import control_canary as canary


def test_control_task_order_and_budget_contract_are_fixed(tmp_path: Path) -> None:
    assert [task["instance_id"] for task in canary.TASKS] == ["beetbox__beets-5495", "beancount__beancount-931"]
    assert canary.TASKS[0]["test_target"] == "test/test_importer.py::ImportSingletonTest::test_set_fields"
    assert canary.TASKS[0]["preflight_dependencies"] == ["responses>=0.3.0"]
    assert canary.TASKS[1]["test_target"] == "beancount/plugins/leafonly_test.py"
    assert canary.TASKS[1]["preflight_dependencies"] == []
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
    selector = canary.subprocess.CompletedProcess([], 4, "ERROR: not found: target", "")
    assert canary._environment_blocker(selector) == "pytest_selector_not_found"
    baseline_failure = canary.subprocess.CompletedProcess([], 1, "collected 1 items", "")
    assert canary._environment_blocker(baseline_failure) is None


def test_preflight_persists_test_evidence_and_accepts_a_project_baseline_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def materialize(task: dict[str, object], workspace: Path) -> None:
        workspace.mkdir(parents=True)

    def bootstrap(workspace: Path, dependencies: list[str]) -> tuple[Path, list[dict[str, object]]]:
        python = workspace / "python"
        assert dependencies == ["responses>=0.3.0"]
        return python, [{"command": [str(python), "-m", "pip", "install", "-e", ".", "pytest", *dependencies], "exit_code": 0, "stdout": "installed", "stderr": ""}]

    def run(command: list[str], *, cwd: Path, timeout: int = 1200) -> canary.subprocess.CompletedProcess[str]:
        if "--collect-only" in command:
            return canary.subprocess.CompletedProcess(command, 0, "test/test_importer.py::ImportSingletonTest::test_set_fields\n\n1 test collected", "")
        return canary.subprocess.CompletedProcess(command, 1, "1 failed", "")

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
    assert result["collected_test_count"] == 1
    assert result["collection_exit_code"] == 0
    assert result["execution_exit_code"] == result["exit_code"] == 1
    assert json.loads((evidence / "collection-command.json").read_text())["command"][-1] == task["test_target"]
    assert json.loads((evidence / "execution-command.json").read_text())["command"][-1] == task["test_target"]
    assert (evidence / "dependency-bootstrap.json").is_file()
    assert (evidence / "collection.stdout.txt").read_text().endswith("1 test collected")
    assert (evidence / "execution.stdout.txt").read_text() == "1 failed"


@pytest.mark.parametrize(
    ("stdout", "stderr", "returncode", "expected"),
    [
        ("", "ModuleNotFoundError: No module named 'responses'", 2, "missing_python_dependency"),
        ("no tests collected", "", 0, "pytest_collected_zero_tests"),
    ],
)
def test_preflight_rejects_missing_dependency_or_empty_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: str, stderr: str,
    returncode: int, expected: str,
) -> None:
    def materialize(task: dict[str, object], workspace: Path) -> None:
        workspace.mkdir(parents=True)

    def bootstrap(workspace: Path, dependencies: list[str]) -> tuple[Path, list[dict[str, object]]]:
        return workspace / "python", [{"command": [], "exit_code": 0, "stdout": "installed", "stderr": ""}]

    def run(command: list[str], *, cwd: Path, timeout: int = 1200) -> canary.subprocess.CompletedProcess[str]:
        assert "--collect-only" in command
        return canary.subprocess.CompletedProcess(command, returncode, stdout, stderr)

    monkeypatch.setattr(canary, "_goal3_materialize_instance", materialize)
    monkeypatch.setattr(canary, "_bootstrap", bootstrap)
    monkeypatch.setattr(canary, "_run", run)
    result = canary.preflight_task(dict(canary.TASKS[0]), work_root=tmp_path)
    assert result["test_collection_completed"] is False
    assert result["meaningful_test_executed"] is False
    assert result["environment_blocker"] == expected


def test_preflight_reports_the_phase_of_a_materialization_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def reject(task: dict[str, object], workspace: Path) -> None:
        raise ValueError("git transport error")

    monkeypatch.setattr(canary, "_goal3_materialize_instance", reject)
    result = canary.preflight_task(dict(canary.TASKS[0]), work_root=tmp_path)
    assert result["environment_blocker"] == "workspace_materialization_failed"
    assert result["error"] == "git transport error"


def test_future_paid_runner_stops_before_second_task_when_first_is_unhealthy(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    freeze = tmp_path / "freeze"
    canary.write_freeze(root=root, output=freeze)
    calls: list[str] = []

    def executor(task: dict[str, str]) -> canary.PaidTaskResult:
        calls.append(task["instance_id"])
        return canary.PaidTaskResult(task["instance_id"], "completed", "exported_nonempty", "executed", "evaluator_execution_error", "error", "zero_provider")

    results = canary.execute_paid_canary(root=root, freeze=freeze, paid_execution=True, executor=executor)
    assert len(results) == len(calls) == 1
    assert calls == ["beetbox__beets-5495"]


def test_paid_runner_requires_explicit_executor(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    freeze = tmp_path / "freeze"
    canary.write_freeze(root=root, output=freeze)
    assert canary.execute_paid_canary(root=root, freeze=freeze, paid_execution=False) == []
    with pytest.raises(ValueError, match="configured Provider executor"):
        canary.execute_paid_canary(root=root, freeze=freeze, paid_execution=True)


def test_payloads_are_exactly_allowlisted_and_bound_to_the_manifest(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    payloads = canary.load_frozen_payloads(root)
    assert [item["instance_id"] for item in payloads] == [task["instance_id"] for task in canary.TASKS]
    assert [hashlib.sha256(item["problem_statement"].encode()).hexdigest() for item in payloads] == [
        "34fc3d488e4b585ec0d850d6708b58a5d4277a82d75074a1585c148d1f46094d",
        "51268fc5326eefb57b052aeba817c2991e72e75a108ce59cdb8b4d3224d958a4",
    ]
    payload = root / canary.PAYLOAD_DIRECTORY / "beetbox__beets-5495.json"
    original = payload.read_text(encoding="utf-8")
    payload.write_text(original.replace('"repo"', '"gold_patch":"forbidden","repo"'), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA differs"):
        canary.load_frozen_payloads(root)
    payload.write_text(original, encoding="utf-8")


def test_fake_paid_path_validates_authorization_and_serial_health(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    freeze = tmp_path / "freeze"
    canary.write_freeze(root=root, output=freeze)
    freeze_sha = canary._sha256(freeze / "control-canary-freeze.json")
    monkeypatch.setattr("evals.paid_gate._git_is_clean", lambda _root: True)

    def replay(task: dict[str, str]) -> canary.PaidTaskResult:
        return canary.PaidTaskResult(
            task["instance_id"], "completed", "exported_nonempty", "executed", "completed",
            "completed", "replay", terminal_status="unresolved", candidate_sha256="a" * 64,
            workspace_diff_sha256="a" * 64, candidate_diff_identity=True,
        )

    summary = canary.run_paid_canary(
        root=root, freeze=freeze, artifact_root=tmp_path / "paid", expected_freeze_sha256=freeze_sha,
        approved_hard_cap_cny="15.000000", authorization_acknowledgement="test-only-replay",
        run_id="replay-001", executor=replay,
    )
    assert summary["completed"] is True
    assert summary["provider_requests"] == summary["usage"] == 0
    assert summary["charge_cny"] == "0"
    assert summary["active_reservation"] is None
    assert (tmp_path / "paid" / "authorization.json").is_file()
    assert (tmp_path / "paid" / "stage-c-compatibility-allocation.json").is_file()


def test_rehearsal_allocation_fails_closed_when_missing_or_not_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    pricing = canary.load_pricing(root / canary.PRICING_PATH)
    authorization = canary.BudgetAuthorization(
        authorized_total_cny=canary.RECOMMENDED_HARD_CAP_CNY,
        stage_limits_cny={"A": canary.RECOMMENDED_HARD_CAP_CNY, "B": canary.RECOMMENDED_HARD_CAP_CNY, "C": canary.RECOMMENDED_HARD_CAP_CNY},
        pricing_snapshot_hash=canary.pricing_snapshot_hash(pricing),
        experiment_commit=canary.current_git_commit(root),
        authorized_at="zero-provider-rehearsal",
        authorized_by="user",
    )
    authorization_path = tmp_path / "authorization.json"
    ledger_path = tmp_path / "ledger.json"
    allocation_path = tmp_path / "allocation.json"
    canary._write_json(authorization_path, authorization.model_dump(mode="json"))
    ledger = canary.BudgetLedger(
        authorization_hash=canary.authorization_hash(authorization), updated_at="zero-provider-rehearsal",
    )
    canary._write_json(ledger_path, ledger.model_dump(mode="json"))
    monkeypatch.setattr("evals.paid_gate._git_is_clean", lambda _root: True)
    with pytest.raises(ValueError, match="requires a budget allocation"):
        canary.PaidRunGate(root=root, authorization_path=authorization_path, ledger_path=ledger_path, pricing=pricing, stage="C")

    allocation = canary._fresh_rehearsal_allocation(
        authorization, ledger, canary.pricing_snapshot_hash(pricing),
    )
    canary._write_json(allocation_path, allocation.model_copy(update={"experiment_commit": "0" * 40}).model_dump(mode="json"))
    with pytest.raises(ValueError, match="not bound to the authorization"):
        canary.PaidRunGate(root=root, authorization_path=authorization_path, ledger_path=ledger_path, pricing=pricing, stage="C", allocation_path=allocation_path)

    canary._write_json(allocation_path, allocation.model_copy(update={"spendable_total_cny": Decimal("15")}).model_dump(mode="json"))
    with pytest.raises(ValueError, match="consumes the reserved safety margin"):
        canary.PaidRunGate(root=root, authorization_path=authorization_path, ledger_path=ledger_path, pricing=pricing, stage="C", allocation_path=allocation_path)

    canary._write_json(allocation_path, allocation.model_dump(mode="json"))
    historical = ledger.model_copy(update={"authorization_hash": "f" * 64})
    canary._write_json(ledger_path, historical.model_dump(mode="json"))
    with pytest.raises(ValueError, match="belongs to a different authorization"):
        gate = canary.PaidRunGate(root=root, authorization_path=authorization_path, ledger_path=ledger_path, pricing=pricing, stage="C", allocation_path=allocation_path)
        gate.reserve("swe/v2-control/rehearsal/historical", maximum_requests=1, maximum_input_tokens_per_request=canary.MAX_INPUT_TOKENS, maximum_output_tokens_per_request=canary.MAX_OUTPUT_TOKENS)


def test_workflow_owns_output_directories_and_redirect_parents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    workflow = (repository_root / ".github" / "workflows" / "evaluation-v2-control-canary.yml").read_text(encoding="utf-8")
    assert "pull_request:" in workflow
    assert "github.event_name == 'pull_request'" in workflow
    assert "github.event_name == 'workflow_dispatch' && inputs.paid_execution == true" in workflow
    assert 'mkdir -p "$root"' in workflow
    for child in ("freeze", "preflight", "rehearsal"):
        assert f'mkdir -p "$root/{child}"' not in workflow
    setup_index = workflow.index('mkdir -p "$root"')
    command_indexes = [workflow.index(f'> "$CANARY_ROOT/{name}"') for name in ("freeze-result.json", "preflight-result.json", "rehearsal-result.json")]
    assert setup_index < min(command_indexes)
    assert workflow.index("control_canary freeze") < workflow.index("control_canary validate") < workflow.index("control_canary preflight") < workflow.index("control_canary rehearse")
    for name in ("freeze-result.json", "freeze-validation.json", "preflight-result.json", "rehearsal-result.json"):
        assert f'"$CANARY_ROOT/{name}"' in workflow

    root = tmp_path / "workflow-root"
    root.mkdir()
    freeze = root / "freeze"
    preflight = root / "preflight"
    rehearsal = root / "rehearsal"
    assert not any(path.exists() for path in (freeze, preflight, rehearsal))

    canary.main(["freeze", "--root", str(repository_root), "--output", str(freeze)])
    canary.main(["validate", "--root", str(repository_root), "--freeze", str(freeze)])

    def materialize(task: dict[str, object], workspace: Path) -> None:
        workspace.mkdir(parents=True)

    def bootstrap(workspace: Path, dependencies: list[str]) -> tuple[Path, list[dict[str, object]]]:
        python = workspace / "fake-python"
        return python, [{"command": [str(python), "-m", "pip", "install", "-e", ".", "pytest"], "exit_code": 0, "stdout": "installed", "stderr": ""}]

    def run(command: list[str], *, cwd: Path, timeout: int = 1200) -> canary.subprocess.CompletedProcess[str]:
        if "pytest" in command:
            if "--collect-only" in command:
                return canary.subprocess.CompletedProcess(command, 0, "1 test collected", "")
            return canary.subprocess.CompletedProcess(command, 0, "1 passed", "")
        return canary.subprocess.CompletedProcess(command, 0, "installed", "")

    monkeypatch.setattr(canary, "_goal3_materialize_instance", materialize)
    monkeypatch.setattr(canary, "_bootstrap", bootstrap)
    monkeypatch.setattr(canary, "_run", run)
    monkeypatch.setattr("evals.paid_gate._git_is_clean", lambda _root: True)
    canary.main(["preflight", "--freeze", str(freeze), "--artifact-root", str(preflight)])
    canary.main(["rehearse", "--root", str(repository_root), "--freeze", str(freeze), "--preflight-summary", str(preflight / "preflight-summary.json"), "--artifact-root", str(rehearsal)])

    assert freeze.is_dir() and preflight.is_dir() and rehearsal.is_dir()
    summary = json.loads((preflight / "preflight-summary.json").read_text(encoding="utf-8"))
    rehearsal_result = json.loads((rehearsal / "paid-path-rehearsal.json").read_text(encoding="utf-8"))
    ledger = canary.BudgetLedger.model_validate_json((rehearsal / "rehearsal-ledger.json").read_text(encoding="utf-8"))
    assert summary["passed"] is True
    assert all(item["task_workspace_materialized"] and item["dependencies_installed"] and item["test_collection_completed"] and item["meaningful_test_executed"] and item["environment_blocker"] is None for item in summary["tasks"])
    assert all(item["collected_test_count"] >= 1 for item in summary["tasks"])
    assert rehearsal_result["provider_requests"] == rehearsal_result["usage"] == 0
    assert rehearsal_result["charge_cny"] == "0"
    assert rehearsal_result["active_reservation"] is None
    assert (rehearsal / rehearsal_result["allocation"]["path"]).is_file()
    assert rehearsal_result["allocation"]["closed"] is True
    assert rehearsal_result["allocation"]["remaining_spendable_cny"] == "14.999999"
    assert ledger.request_charges == []
    assert ledger.spent_cny == 0
    assert ledger.active_reservation is None
    assert [item["status"] for item in rehearsal_result["reservations"]] == ["cancelled", "cancelled"]
    assert all(Decimal(item["settlement_cny"]) == 0 for item in rehearsal_result["reservations"])

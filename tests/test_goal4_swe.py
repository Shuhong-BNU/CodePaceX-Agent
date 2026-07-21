from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

import pytest

import evals.goal4_swe as goal4
from evals.paid_gate import RequestCharge, Settlement, _write_ledger_atomic
from evals.secret_scan import scan_artifact_roots


PRICING = Path("evals/goal2/pricing_bailian_qwen37_max_2026-07-13.json")


def test_active_reservation_is_an_immediate_accounting_hard_stop() -> None:
    accounting = {
        "provider_usage_contract_violation": None,
        "budget_blocked": False,
        "active_reservation": {
            "trial_id": "swe/run/trial-timeout",
            "request_index": 10,
            "failure_type": "openai.APITimeoutError/httpx.ConnectTimeout",
        },
    }
    assert goal4._accounting_hard_stop_reason(accounting) == "active_reservation"


def _patch(count: int) -> str:
    return "\n".join(
        f"--- a/f{index}.py\n+++ b/f{index}.py\n@@ -1 +1 @@\n-old\n+new"
        for index in range(count)
    )


def _rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    index = 0
    for label, files, count in (("one", 1, 12), ("medium", 3, 12), ("large", 5, 8)):
        for number in range(count):
            index += 1
            rows.append({
                "instance_id": f"{label}-{number:02d}", "repo": f"org/repo-{index:02d}",
                "base_commit": f"{index:040x}", "problem_statement": f"fix {label} {number}",
                "patch": _patch(files), "test_patch": "test", "platform": "linux",
                "version": "1", "environment_setup_commit": f"{index:040x}",
            })
    return list(reversed(rows))


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_matrix_is_deterministic_stratified_and_excludes_goal3() -> None:
    rows = _rows()
    rows.append({
        "instance_id": "amoffat__sh-744", "repo": "excluded/repo", "platform": "linux",
        "patch": _patch(1), "base_commit": "a" * 40, "problem_statement": "excluded",
        "test_patch": "", "version": "1", "environment_setup_commit": "a" * 40,
    })
    selected = goal4.select_formal_matrix(rows)
    assert len(selected) == 20
    assert not {str(item["instance_id"]) for item in selected} & goal4.GOAL3_PILOT_IDS
    assert [sum(goal4.size_bucket(item) == bucket for item in selected) for bucket in goal4.FORMAL_SIZE_TARGETS] == [8, 8, 4]
    assert max(list({str(item["repo"]): sum(str(other["repo"]) == str(item["repo"]) for other in selected) for item in selected}.values())) <= 2
    assignments = goal4.assign_batches(selected)
    assert sum(batch == "A" for _item, batch in assignments) == 5
    assert sum(batch == "B" for _item, batch in assignments) == 15


def test_freeze_sanitizes_gold_data_and_validates_batches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, output = tmp_path / "source.jsonl", tmp_path / "goal4-freeze"
    _write_jsonl(source, _rows())
    monkeypatch.setattr(goal4, "current_git_commit", lambda _root: "c" * 40)
    monkeypatch.setattr(goal4, "_tree_hash", lambda _root: "d" * 40)
    frozen = goal4.freeze_formal_bundle(
        root=tmp_path, dataset_jsonl=source, pricing_snapshot=PRICING,
        output_dir=output, dataset_revision="a" * 40,
    )
    assert len(frozen["tasks"]) == 20
    assert {task["batch"] for task in frozen["tasks"]} == {"A", "B"}
    agent_rows = [json.loads(line) for line in (output / "formal-dataset.jsonl").read_text().splitlines()]
    assert len(agent_rows) == 20
    assert all("patch" not in row and "test_patch" not in row for row in agent_rows)
    assert goal4.load_formal_freeze(output / "formal-freeze.json")["matrix_sha256"] == frozen["matrix_sha256"]
    assert len(goal4.load_formal_instances(frozen=frozen, dataset_jsonl=output / "formal-dataset.jsonl")) == 20


def test_parent_and_child_budget_artifacts_are_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, output = tmp_path / "source.jsonl", tmp_path / "goal4-swe"
    _write_jsonl(source, _rows())
    monkeypatch.setattr(goal4, "current_git_commit", lambda _root: "c" * 40)
    monkeypatch.setattr(goal4, "_tree_hash", lambda _root: "d" * 40)
    goal4.freeze_formal_bundle(
        root=tmp_path, dataset_jsonl=source, pricing_snapshot=PRICING,
        output_dir=output, dataset_revision="a" * 40,
    )
    prepared = goal4.prepare_paid_artifacts(
        root=tmp_path, freeze_path=output / "formal-freeze.json",
        pricing_path=output / "pricing-snapshot.json", evidence_root=output,
    )
    assert prepared["parent_authorization_cny"] == "1684.439040"
    zero = goal4.zero_provider_check(
        root=tmp_path, freeze_path=output / "formal-freeze.json",
        pricing_path=output / "pricing-snapshot.json", evidence_root=output,
    )
    assert zero["provider_requests"] == 0
    parent = json.loads((output / "accounts" / "parent-ledger.json").read_text())
    assert parent["active_reservation"] is None
    assert parent["request_charge_count"] == 0


def test_recovery_rebind_updates_parent_and_child_freeze_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.jsonl"
    old_output, new_output = tmp_path / "goal4-old", tmp_path / "goal4-new"
    new_pricing = tmp_path / "pricing-new.json"
    _write_jsonl(source, _rows())
    pricing = json.loads(PRICING.read_text(encoding="utf-8"))
    pricing["retrieved_at"] = "2026-07-21T00:00:00Z"
    new_pricing.write_text(json.dumps(pricing), encoding="utf-8")
    monkeypatch.setattr(goal4, "_tree_hash", lambda _root: "d" * 40)
    monkeypatch.setattr(goal4, "current_git_commit", lambda _root: "c" * 40)
    goal4.freeze_formal_bundle(
        root=tmp_path, dataset_jsonl=source, pricing_snapshot=PRICING,
        output_dir=old_output, dataset_revision="a" * 40,
    )
    goal4.prepare_paid_artifacts(
        root=tmp_path, freeze_path=old_output / "formal-freeze.json",
        pricing_path=old_output / "pricing-snapshot.json", evidence_root=old_output,
    )
    monkeypatch.setattr(goal4, "current_git_commit", lambda _root: "e" * 40)
    new_frozen = goal4.freeze_formal_bundle(
        root=tmp_path, dataset_jsonl=source, pricing_snapshot=new_pricing,
        output_dir=new_output, dataset_revision="a" * 40,
    )
    goal4.prepare_recovery_artifacts(
        root=tmp_path, old_freeze_path=old_output / "formal-freeze.json",
        new_freeze_path=new_output / "formal-freeze.json",
        pricing_path=new_output / "pricing-snapshot.json", evidence_root=old_output,
    )
    parent = json.loads((old_output / "accounts" / "parent-authorization.json").read_text())
    assert parent["experiment_commit"] == "e" * 40
    assert parent["pricing_snapshot_hash"] == new_frozen["pricing_snapshot_hash"]
    for batch in ("A", "B"):
        paths = goal4._batch_paths(old_output, batch)
        authorization = goal4.BudgetAuthorization.model_validate_json(paths["authorization"].read_text())
        allocation = goal4.StageCBudgetAllocation.model_validate_json(paths["allocation"].read_text())
        assert parent["children"][batch]["authorization_sha256"] == goal4.authorization_hash(authorization)
        assert parent["children"][batch]["allocation_sha256"] == goal4.allocation_hash(allocation)


def test_evidence_accepts_one_explicit_conservative_settlement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, output = tmp_path / "source.jsonl", tmp_path / "goal4-evidence"
    _write_jsonl(source, _rows())
    monkeypatch.setattr(goal4, "current_git_commit", lambda _root: "c" * 40)
    monkeypatch.setattr(goal4, "_tree_hash", lambda _root: "d" * 40)
    frozen = goal4.freeze_formal_bundle(
        root=tmp_path, dataset_jsonl=source, pricing_snapshot=PRICING,
        output_dir=output, dataset_revision="a" * 40,
    )
    goal4.prepare_paid_artifacts(
        root=tmp_path, freeze_path=output / "formal-freeze.json",
        pricing_path=output / "pricing-snapshot.json", evidence_root=output,
    )
    events_dir = output / "goal4-formal-test"
    events_dir.mkdir()
    events = []
    for index, task in enumerate(frozen["tasks"], 1):
        trial_id = f"swe/goal4/goal4-formal-test/batch-{task['batch'].lower()}/1/{task['instance_id']}"
        events.append({
            "type": "trial_started", "task_id": task["instance_id"], "trial_id": trial_id,
            "timestamp": float(index) - 0.5, "retry_of": "old-infrastructure-run" if index == 1 else None,
        })
        events.append({
            "type": "trial_completed", "task_id": task["instance_id"], "trial_id": trial_id,
            "timestamp": float(index), "status": "unresolved", "provider_request_count": int(index == 1),
            "actual_cny": "0.100000" if index == 1 else "0.000000",
        })
    (events_dir / "events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8",
    )
    paths = goal4._batch_paths(output, "A")
    ledger = goal4._load_child_ledger(paths["ledger"])
    trial_id = events[0]["trial_id"]
    ledger.request_charges.append(RequestCharge(
        reservation_id="known", trial_id=trial_id, request_index=1,
        input_tokens=10, output_tokens=2, reasoning_tokens=1,
        actual_cny=goal4.Decimal("0.100000"), recorded_at="known",
    ))
    ledger.settlements.extend([
        Settlement(
            reservation_id="known", trial_id=trial_id, stage="C", requests=1,
            input_tokens=10, output_tokens=2, reasoning_tokens=1,
            actual_cny=goal4.Decimal("0.100000"), status="settled",
            settlement_method="provider_usage", usage_status="known", settled_at="known",
        ),
        Settlement(
            reservation_id="unknown", trial_id=trial_id, stage="C",
            actual_cny=goal4.Decimal("0.200000"), status="conservative_settled",
            settlement_method="conservative_reserved_amount", usage_status="unknown",
            evidence_gap="authorized unknown Provider outcome", settled_at="unknown",
        ),
    ])
    ledger.spent_cny = goal4.Decimal("0.300000")
    _write_ledger_atomic(paths["ledger"], ledger)
    summary = goal4.evidence_summary(
        root=tmp_path, freeze_path=output / "formal-freeze.json", evidence_root=output,
    )
    assert summary["accepted"] is True
    assert summary["total_requests"] == 1
    assert summary["settlements"] == 2
    assert summary["provider_usage_settlements"] == 1
    assert summary["conservative_settlements"] == 1
    assert summary["paid_trial_attempts"] == 20
    assert summary["infrastructure_retry_count"] == 1


def test_task_environment_uses_fresh_virtualenv(tmp_path: Path) -> None:
    task_venv = tmp_path / "task-venv"
    venv.EnvBuilder(with_pip=False).create(task_venv)
    environment = goal4._isolated_task_environment(dict(os.environ), task_venv)
    result = subprocess.run(
        ["python", "-c", "import os,sys; print(sys.prefix); print(os.environ['VIRTUAL_ENV'])"],
        env=environment, text=True, capture_output=True, check=True,
    )
    assert result.stdout.splitlines() == [str(task_venv), str(task_venv)]
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert Path(environment["PATH"].split(os.pathsep)[0]).parent == task_venv
    assert sys.prefix != str(task_venv)


def _write_compaction_fixture(root: Path, *, outside_secret: bool = False) -> tuple[Path, str]:
    run = root / "goal4-resume-test-batch-b"
    report = run / "logs" / "run_evaluation" / "run" / "model" / "task" / "report.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({"task": {"resolved": False}}), encoding="utf-8")
    report_sha = hashlib.sha256(report.read_bytes()).hexdigest()
    fixture = report.with_name("test_output.txt")
    fixture.write_text(
        "AWS_" + "ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPL\n"
        "https://fixture-user:" + "fixture-password@github.com/public-fixtures/private.git\n",
        encoding="utf-8",
    )
    (run / "events.jsonl").write_text(json.dumps({
        "type": "trial_completed", "task_id": "task", "trial_id": "trial/task",
        "status": "unresolved", "official_evaluator_completed": True,
        "evaluator_report_sha256": report_sha,
    }) + "\n", encoding="utf-8")
    if outside_secret:
        artifacts = run / "artifacts"
        artifacts.mkdir()
        (artifacts / "real.txt").write_text(
            "Authorization: " + "Bear" + "er real-credential-value-123", encoding="utf-8",
        )
    return report, report_sha


def test_compaction_preserves_hashed_report_and_removes_transient_fixtures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "goal4-evidence"
    report, report_sha = _write_compaction_fixture(root)
    assert len(scan_artifact_roots([root])) == 2
    monkeypatch.setattr(goal4, "current_git_commit", lambda _root: "c" * 40)
    index = goal4.compact_evaluator_transients(
        root=tmp_path, evidence_root=root, source_run_id="29795711075",
        source_artifact_name="goal4-swe-formal-evidence-29795711075",
        source_archive_sha256="a" * 64,
    )
    assert not report.parents[4].exists()
    assert index["preserved_report_count"] == 1
    preserved = root / index["reports"][0]["preserved_path"]
    assert hashlib.sha256(preserved.read_bytes()).hexdigest() == report_sha
    assert scan_artifact_roots([root]) == []


def test_compaction_does_not_hide_real_secret_outside_transient_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "goal4-evidence"
    _write_compaction_fixture(root, outside_secret=True)
    monkeypatch.setattr(goal4, "current_git_commit", lambda _root: "c" * 40)
    goal4.compact_evaluator_transients(
        root=tmp_path, evidence_root=root, source_run_id="29795711075",
        source_artifact_name="goal4-swe-formal-evidence-29795711075",
        source_archive_sha256="a" * 64,
    )
    findings = scan_artifact_roots([root])
    assert len(findings) == 1
    assert findings[0].endswith("/artifacts/real.txt:1")


def test_budget_contract_is_exact() -> None:
    assert goal4.BATCH_AUTHORIZATION["A"] == goal4.Decimal("421.109760")
    assert goal4.BATCH_AUTHORIZATION["B"] == goal4.Decimal("1263.329280")
    assert goal4.PARENT_AUTHORIZATION == goal4.Decimal("1684.439040")


def test_pilot_schema_compatibility_does_not_change_formal_request_ceiling() -> None:
    config = goal4._pilot_config({
        "provider": "bailian-qwen37-max", "protocol": "openai-compat",
        "base_url": "https://llm-ipge9fy38w648m28.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "BAILIAN_API_KEY", "model_id": "qwen3.7-max-2026-06-08",
        "model_parameters": goal4.FORMAL_MODEL_PARAMETERS,
        "experiment_profile": goal4._goal4_profile().canonical_payload(),
    })
    assert config.max_iterations == 50
    assert goal4.MAXIMUM_REQUESTS_PER_INSTANCE == 40


def test_retry_selection_is_bounded_to_registered_instance() -> None:
    rows = _rows()
    selected = goal4.select_formal_matrix(rows)
    assignments = goal4.assign_batches(selected)
    batch_a = [str(item["instance_id"]) for item, batch in assignments if batch == "A"]
    assert len(batch_a) == 5
    assert batch_a[0] in {str(item["instance_id"]) for item in selected}


def test_paid_workflow_can_resume_batch_b_without_rerunning_batch_a() -> None:
    workflow = Path(".github/workflows/goal4-swe-paid-formal.yml").read_text(encoding="utf-8")
    carry_start = workflow.index("- name: Carry verified Batch A into the new Freeze")
    carry_end = workflow.index("- name: Secret scan Batch A evidence", carry_start)
    carry_step = workflow[carry_start:carry_end]
    assert "resume_batch_b:" in workflow
    assert workflow.count("if: ${{ !inputs.recovery_mode && !inputs.resume_batch_b }}") == 2
    assert "python -m evals.goal4_swe zero-provider" in carry_step
    assert "python -m evals.goal4_swe prepare-recovery-artifacts" in carry_step
    assert 'len(batch_a["request_charges"]) != 138' in carry_step
    assert 'methods.count("conservative_reserved_amount") != 1' in carry_step
    assert "execute-batch --confirm-paid-run --batch A" not in carry_step


def test_batch_b_recovery_workflow_is_explicitly_paid_gated() -> None:
    workflow = Path(".github/workflows/goal4-swe-batch-b-recovery.yml").read_text(encoding="utf-8")
    assert "default: false" in workflow
    assert "if: ${{ github.event_name == 'workflow_dispatch' }}" in workflow
    assert "if: ${{ inputs.execute_paid && inputs.authorize_blocked_retry }}" in workflow
    assert "compact-evaluator-transients" in workflow
    assert "source_archive_sha256" in workflow
    assert "zero-provider" in workflow
    assert "bridgecrewio__checkov-6895" in workflow
    assert "--retry-of 'goal4-resume-29795408466-batch-b'" in workflow
    assert "Execute the ten never-run Batch B tasks strictly serially" in workflow
    preflight = workflow[:workflow.index("  paid-recovery:")]
    assert "execute-batch --confirm-paid-run" not in preflight
    freeze_workflow = Path(".github/workflows/goal4-swe-freeze.yml").read_text(encoding="utf-8")
    assert ".github/workflows/goal4-swe-batch-b-recovery.yml" in freeze_workflow


def test_unknown_provider_settlement_workflow_cannot_execute_paid_requests() -> None:
    workflow_path = Path(".github/workflows/goal4-swe-settlement-recovery.yml")
    workflow = workflow_path.read_text(encoding="utf-8")
    assert "close-unknown-provider-outcome" in workflow
    assert "dbc13fb389344b8fb34cf8d3f548f336" in workflow
    assert "29800120889" in workflow
    assert "5136e6bac5bf4b9c534b02a02165eb1c742ea817423fc9407eba62541d63ca66" in workflow
    assert "verified != Decimal('44.173392')" in workflow
    assert "Decimal('3.661824')" in workflow
    assert "parent['spent_cny'] != '47.835216'" in workflow
    assert "zero-provider" in workflow
    assert "execute-batch" not in workflow
    assert "BAILIAN_API_KEY" not in workflow
    assert "secrets." not in workflow
    assert "if: ${{ github.event_name == 'workflow_dispatch' }}" in workflow
    freeze_workflow = Path(".github/workflows/goal4-swe-freeze.yml").read_text(encoding="utf-8")
    assert str(workflow_path) in freeze_workflow


def test_empty_control_accepts_official_empty_patch_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.jsonl"
    instance = _rows()[0]
    instance_id = str(instance["instance_id"])
    _write_jsonl(source, [instance])
    monkeypatch.setattr(goal4, "current_git_commit", lambda _root: "c" * 40)
    monkeypatch.setattr(goal4, "require_native_preflight", lambda *, root: {
        "installed_evaluator_commit": "e" * 40,
    })

    def fake_evaluator(**kwargs: object) -> subprocess.CompletedProcess[str]:
        cwd = Path(str(kwargs["cwd"]))
        evaluator_logs = cwd / "logs"
        evaluator_logs.mkdir()
        (evaluator_logs / "upstream.txt").write_text("AWS" + "::LanguageExtensions")
        (cwd / "official-empty-summary.json").write_text(json.dumps({
            "empty_patch_ids": [instance_id],
        }), encoding="utf-8")
        return subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="No instances to run.\nInstances with empty patches: 1\n",
            stderr="",
        )

    monkeypatch.setattr(goal4, "run_official_evaluator", fake_evaluator)
    recorder = goal4.run_control(
        root=tmp_path, source_dataset_jsonl=source, instance_id=instance_id,
        control="empty", runs_dir=tmp_path / "goal4-controls", run_id="empty-control",
    )
    result = json.loads((recorder.path / "result.json").read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (recorder.path / "events.jsonl").read_text().splitlines()]
    terminal = next(event for event in events if event["type"] == "control_completed")
    assert result["status"] == "success"
    assert result["resolved"] is False
    assert result["official_evaluator_completed"] is True
    assert result["empty_patch_rejected_by_evaluator"] is True
    assert not (recorder.path / "predictions.json").exists()
    assert not (recorder.path / "logs").exists()
    assert terminal["evaluator_completed"] is True
    assert terminal["empty_patch_rejected_by_evaluator"] is True

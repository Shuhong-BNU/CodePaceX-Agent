from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from evals.evaluation_v2 import p3b_paid_executor as executor
from evals.evaluation_v2 import p3b_post_merge_rebind as p3b
from evals.evaluation_v2 import full_replay
from evals import paid_gate


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _simulate_clean_frozen_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    """PaidRunGate separately owns the clean-checkout invariant.

    Executor tests run from this deliberately modified R4 worktree, so model
    the clean frozen checkout that the production workflow creates.
    """
    monkeypatch.setattr(paid_gate, "_git_is_clean", lambda _root: True)


def _inputs() -> dict[str, str | bool]:
    freeze = ROOT / p3b.ARTIFACT_DIRECTORY / p3b.FREEZE_NAME
    payload = json.loads(freeze.read_text(encoding="utf-8"))
    return {
        "expected_main_sha": "4a5b66787f1d7ab0619b86eb3c9fb7f0f7b6cd72",
        "expected_freeze_sha256": hashlib.sha256(freeze.read_bytes()).hexdigest(),
        "expected_allocation_hash": payload["formal_stage_c_allocation"]["allocation_hash"],
        "approved_parent_cap_cny": "292.945921",
        "authorization_acknowledgement": "P3B_PAID_AUTHORIZATION:test-only",
        "dispatch_token": "p3b1-recording-token-0001",
        "run_id": "p3b1-fake-run-0001",
        "provider_secret_present": True,
    }


def _fake_run(tmp_path: Path, **overrides: str | bool) -> dict:
    values = {**_inputs(), **overrides}
    return executor.run_paid_executor(
        ROOT, tmp_path / "paid-artifact", task_executor=executor.recording_fake_task_executor,
        require_main=False, **values,  # type: ignore[arg-type]
    )


def test_recording_fake_exercises_the_paid_coordinator_for_all_eight_runs(tmp_path: Path) -> None:
    summary = _fake_run(tmp_path)
    assert summary["paid_execution"] is False
    assert summary["completed"] is True
    assert len(summary["records"]) == 8
    assert summary["paired_result_merge_count"] == 4
    assert summary["provider_requests"] == 32
    assert summary["usage"] == 6_400
    assert summary["charge_cny"] != "0"
    assert summary["ledger_closed"] is True and summary["active_reservation"] is None
    assert summary["provider_secret_read"] is False
    artifact = tmp_path / "paid-artifact"
    assert (artifact / executor.PAIRED_RESULTS_NAME).is_file()
    assert (artifact / executor.PAID_SUMMARY_NAME).is_file()
    assert all(
        (artifact / record["expected_artifact_path"] / "candidate.patch").is_file()
        for record in summary["records"]
    )


def test_production_adapter_exercises_all_runs_without_provider_transport(tmp_path: Path) -> None:
    """The coordinator reaches the real adapter and shared executor unchanged."""
    captured: list[dict] = []

    def provider_initialization_boundary(**kwargs: object) -> object:
        task = kwargs["task"]  # type: ignore[index]
        metadata = kwargs["metadata"]  # type: ignore[index]
        task_root = Path(kwargs["artifact_root"]) / "tasks" / task["instance_id"]  # type: ignore[index, arg-type]
        task_root.mkdir(parents=True, exist_ok=True)
        patch_text = "diff --git a/tracked.py b/tracked.py\n--- a/tracked.py\n+++ b/tracked.py\n@@ -1 +1 @@\n-value = 'base'\n+value = 'adapter-preflight'\n"
        (task_root / "candidate.patch").write_text(patch_text, encoding="utf-8")
        (task_root / "agent-request-record.json").write_text("{}\n", encoding="utf-8")
        (task_root / "official-report.json").write_text("{}\n", encoding="utf-8")
        treatment = kwargs["freeze_payload"]["runtime_contract"]["capability_v3_feature_flag"]  # type: ignore[index]
        if treatment == "V3_CORE":
            v3 = task_root / "capability-v3"
            v3.mkdir()
            (v3 / "summary.json").write_text("{}\n", encoding="utf-8")
            (v3 / "events.jsonl").write_text("{}\n", encoding="utf-8")
            (v3 / "final.patch").write_text(patch_text, encoding="utf-8")
        (task_root / "task-result.json").write_text("{}\n", encoding="utf-8")
        captured.append({"instance_id": task["instance_id"], "metadata": metadata})  # type: ignore[index]
        return executor.control_canary.PaidTaskResult(
            task["instance_id"], "not_started", "not_exported", "not_run", "not_run",  # type: ignore[index]
            "completed", "pre_transport_blocked", terminal_status="unresolved",
            live_executor_invoked=True,
        )

    with patch.object(executor.control_canary, "_live_task_executor", provider_initialization_boundary), patch.object(
        full_replay, "_full_task_executor", wraps=full_replay._full_task_executor,
    ) as shared_executor:
        summary = executor.run_paid_executor(
            ROOT, tmp_path / "paid-artifact", require_main=False, **_inputs(),  # type: ignore[arg-type]
        )

    assert summary["completed"] is True
    assert summary["provider_requests"] == summary["usage"] == 0
    assert summary["charge_cny"] == "0"
    assert summary["active_reservation"] is None
    assert summary["provider_secret_read"] is False
    assert len(captured) == 8
    assert {item["instance_id"] for item in captured} == {
        "beetbox__beets-5457", "deepset-ai__haystack-8489",
        "dynaconf__dynaconf-1249", "delgan__loguru-1297",
    }
    assert all(call.args[2].keys() == full_replay._task_environment_contract(ROOT).keys() for call in shared_executor.call_args_list)
    assert all(item["metadata"]["test_target"] for item in captured)


def test_production_adapter_reports_a_missing_environment_instance(tmp_path: Path) -> None:
    frozen = json.loads((ROOT / p3b.ARTIFACT_DIRECTORY / p3b.FREEZE_NAME).read_text(encoding="utf-8"))
    task = next(item for item in full_replay.load_tasks(ROOT) if item["instance_id"] == "beetbox__beets-5457")
    missing = full_replay._task_environment_contract(ROOT)
    missing.pop(task["instance_id"])

    class Gate:
        root = ROOT

    with patch.object(full_replay, "_task_environment_contract", return_value=missing), pytest.raises(
        ValueError, match="missing instance: beetbox__beets-5457",
    ):
        executor._real_task_executor(
            frozen, executor._execution_freeze(ROOT, frozen, "V2_CONTROL"), Gate(),
            tmp_path, "p3b-adapter-missing-environment", task, object(),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("field,value,match", [
    ("expected_freeze_sha256", "0" * 64, "freeze SHA"),
    ("expected_main_sha", "not-a-git-sha", "expected main SHA"),
    ("expected_allocation_hash", "0" * 64, "allocation hash"),
    ("approved_parent_cap_cny", "292.945920", "parent cap"),
    ("authorization_acknowledgement", "not-an-authorization", "acknowledgement"),
    ("authorization_acknowledgement", "P3B_PAID_AUTHORIZATION_V2:test-only-0001", "acknowledgement"),
    ("authorization_acknowledgement", "p3b_paid_authorization:test-only-0001", "acknowledgement"),
    ("authorization_acknowledgement", " P3B_PAID_AUTHORIZATION:test-only-0001", "acknowledgement"),
    ("authorization_acknowledgement", "P3B_PAID_AUTHORIZATION:test-only-0001\n", "format"),
    ("authorization_acknowledgement", "P3B_PAID_AUTHORIZATION:'quoted-value'", "format"),
    ("dispatch_token", "short", "dispatch token"),
    ("run_id", "bad/run", "run ID"),
    ("provider_secret_present", False, "Secret presence"),
])
def test_paid_inputs_fail_closed_before_workspace(
    field: str, value: str | bool, match: str,
) -> None:
    values = _inputs(); values[field] = value
    with pytest.raises(ValueError, match=match):
        executor.validate_paid_inputs(ROOT, require_main=False, **values)  # type: ignore[arg-type]


def test_missing_raw_artifact_fails_closed(tmp_path: Path) -> None:
    def missing_report(*args: object) -> object:
        result = executor.recording_fake_task_executor(*args)  # type: ignore[arg-type]
        run_root, task = args[3], args[5]
        (Path(run_root) / "tasks" / str(task["instance_id"]) / "official-report.json").unlink()
        return result

    with pytest.raises(RuntimeError, match="raw Artifact"):
        executor.run_paid_executor(
            ROOT, tmp_path / "paid-artifact", task_executor=missing_report, require_main=False,
            **_inputs(),  # type: ignore[arg-type]
        )


def test_usage_missing_is_conservatively_settled_and_stops(tmp_path: Path) -> None:
    def missing_usage(*args: object) -> object:
        frozen, _execution, gate, _run_root, execution_run_id, task, identity = args
        reservation = gate.reserve(
            f"swe/v2-full-20/{execution_run_id}/{task['instance_id']}", maximum_requests=1,
            maximum_input_tokens_per_request=128_000, maximum_output_tokens_per_request=8_192,
            task_run_identity=identity,
        )
        gate.conservatively_settle_unknown_usage(reservation, evidence_gap="recording fake missing Usage")
        result = executor.recording_fake_task_executor(*args)  # type: ignore[arg-type]
        result.terminal_status = "provider_transport_error"
        return result

    summary = executor.run_paid_executor(
        ROOT, tmp_path / "paid-artifact", task_executor=missing_usage, require_main=False,
        **_inputs(),  # type: ignore[arg-type]
    )
    assert summary["completed"] is False
    assert summary["stop_reason"] == "fail_closed:provider_transport_error"
    ledger = json.loads((tmp_path / "paid-artifact" / "ledger.json").read_text())
    assert ledger["active_reservation"] is None
    assert any(item["status"] == "conservative_settled" for item in ledger["settlements"])


def test_workflow_uses_executor_and_leaves_paid_job_skipped_for_prs() -> None:
    workflow = (ROOT / p3b.WORKFLOW_PATH).read_text(encoding="utf-8")
    workflow_payload = yaml.safe_load(workflow)
    paid_job = workflow_payload["jobs"]["p3b-paid-execution"]
    paid_steps = {step.get("name"): step for step in paid_job["steps"] if isinstance(step, dict)}
    assert "python -m evals.evaluation_v2.p3b_paid_executor" in workflow
    assert "--confirm-paid-execution" in workflow
    assert "p3b-paid-artifact-${{ github.run_id }}" in workflow
    assert "github.event_name == 'workflow_dispatch'" in workflow
    assert "exit 1" not in workflow
    assert "ref: ${{ inputs.expected_main_sha }}" in workflow
    assert "expected_main_sha" in workflow
    assert "--validate-inputs-only" in workflow
    assert "--input-bundle \"$P3B_INPUT_BUNDLE\"" in workflow
    assert "canonical_paid_input_bundle" in workflow
    assert "p3b-paid-execution-global-v1" in workflow
    assert "Artifact-local DispatchGuard cannot coordinate separate GitHub Runs" in workflow
    assert "--authorization-acknowledgement '${{ inputs." not in workflow
    assert "--dispatch-token '${{ inputs." not in workflow
    assert "--run-id '${{ inputs." not in workflow
    assert "BAILIAN_API_KEY: ${{ secrets.BAILIAN_API_KEY }}" in workflow
    assert workflow.index("BAILIAN_API_KEY: ${{ secrets.BAILIAN_API_KEY }}") > workflow.index(
        "name: Run the explicitly authorized P3-B executor"
    )
    assert set(paid_job["env"]) == {"P3B_PROVIDER_SECRET_PRESENT"}
    assert paid_steps["Write canonical paid input bundle"]["env"]["P3B_INPUT_BUNDLE"].startswith("${{ runner.temp }}")
    assert paid_steps["Validate paid inputs without Provider access"]["env"]["P3B_PREFLIGHT_ARTIFACT"].startswith("${{ runner.temp }}")
    assert paid_steps["Run the explicitly authorized P3-B executor"]["env"]["P3B_PAID_ARTIFACT"].startswith("${{ runner.temp }}")


def test_duplicate_and_second_dispatch_remain_rejected(tmp_path: Path) -> None:
    guard = p3b.DispatchGuard(tmp_path / "dispatch-guard.json")
    guard.claim(dispatch_token="p3b1-recording-token-0001", run_id="p3b1-fake-run-0001")
    with pytest.raises(ValueError, match="duplicate"):
        guard.claim(dispatch_token="p3b1-recording-token-0001", run_id="p3b1-fake-run-0001")
    with pytest.raises(ValueError, match="second"):
        guard.claim(dispatch_token="p3b1-second-token-0002", run_id="p3b1-second-run-0002")


def test_canonical_bundle_passes_the_same_validate_only_entrypoint(tmp_path: Path) -> None:
    bundle_path = tmp_path / "paid-inputs.json"
    bundle = executor.canonical_paid_input_bundle(**_inputs())  # type: ignore[arg-type]
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    loaded = executor._load_paid_input_bundle(bundle_path)

    with patch.object(executor.full_replay, "_full_task_executor", side_effect=AssertionError("no Provider")), patch.object(
        executor, "run_paid_executor", side_effect=AssertionError("no paid execution"),
    ):
        payload = executor.validate_inputs_only(
            ROOT, tmp_path / "preflight", require_main=False, **loaded,
        )

    assert payload["status"] == "passed_validate_paid_inputs"
    assert payload["required_acknowledgement_prefix"] == executor.REQUIRED_ACKNOWLEDGEMENT_PREFIX
    assert payload["provider_reached"] is False
    assert payload["provider_secret_read"] is False
    assert payload["provider_requests"] == payload["usage"] == 0
    assert payload["charge_cny"] == "0" and payload["active_reservation"] is None
    assert (tmp_path / "preflight" / executor.PAID_INPUT_PREFLIGHT_NAME).is_file()
    assert not (tmp_path / "preflight" / "workspace").exists()
    assert not (tmp_path / "preflight" / "ledger.json").exists()


def test_cli_accepts_the_canonical_workflow_bundle_in_validate_only_mode(tmp_path: Path) -> None:
    bundle_path = tmp_path / "paid-inputs.json"
    bundle_path.write_text(json.dumps(executor.canonical_paid_input_bundle(**_inputs())), encoding="utf-8")  # type: ignore[arg-type]
    artifact_root = tmp_path / "preflight"
    with patch.object(executor, "_require_main_checkout", return_value=_inputs()["expected_main_sha"]) as main_check, patch.object(
        executor, "run_paid_executor", side_effect=AssertionError("no paid execution"),
    ):
        exit_code = executor.main([
            "--root", str(ROOT), "--artifact-root", str(artifact_root),
            "--input-bundle", str(bundle_path), "--validate-inputs-only",
        ])

    assert exit_code == 0
    assert main_check.call_count == 1
    assert (artifact_root / executor.PAID_INPUT_PREFLIGHT_NAME).is_file()
    assert not (artifact_root / "ledger.json").exists()


def test_cli_bundle_loading_failure_still_writes_preflight_failure_artifact(tmp_path: Path) -> None:
    bundle_path = tmp_path / "invalid-paid-inputs.json"
    bundle_path.write_text("{not-json", encoding="utf-8")
    artifact_root = tmp_path / "preflight"

    exit_code = executor.main([
        "--root", str(ROOT), "--artifact-root", str(artifact_root),
        "--input-bundle", str(bundle_path), "--validate-inputs-only",
    ])

    assert exit_code == 2
    failure = json.loads((artifact_root / executor.PREFLIGHT_FAILURE_NAME).read_text())
    assert failure["phase"] == "validate_paid_inputs"
    assert failure["exception_classification"] == "paid_input_validation_failed"
    assert failure["input_hashes"] == {}
    assert failure["provider_reached"] is False
    assert failure["provider_secret_read"] is False


def test_validate_only_failure_writes_redacted_fail_closed_artifact(tmp_path: Path) -> None:
    values = _inputs()
    values["authorization_acknowledgement"] = "P3B_PAID_AUTHORIZATION_V2:test-only-0001"
    with pytest.raises(ValueError, match="acknowledgement"):
        executor.validate_inputs_only(ROOT, tmp_path / "preflight", require_main=False, **values)  # type: ignore[arg-type]

    failure = json.loads((tmp_path / "preflight" / executor.PREFLIGHT_FAILURE_NAME).read_text())
    assert failure["phase"] == "validate_paid_inputs"
    assert failure["exception_classification"] == "authorization_acknowledgement_protocol_prefix_or_syntax_mismatch"
    assert failure["provider_reached"] is False and failure["provider_secret_read"] is False
    assert failure["provider_requests"] == failure["usage"] == 0
    assert failure["charge_cny"] == "0" and failure["active_reservation"] is None
    serialized = json.dumps(failure)
    assert "P3B_PAID_AUTHORIZATION_V2" not in serialized
    assert values["dispatch_token"] not in serialized


def test_expected_main_sha_must_match_the_checked_out_head() -> None:
    expected = "a" * 40

    def fake_git(_root: Path, *args: str) -> str:
        return {
            ("rev-parse", "HEAD"): expected,
            ("status", "--porcelain"): "",
        }[args]

    with patch.object(executor, "_git", side_effect=fake_git), patch.object(
        executor.subprocess, "run", return_value=SimpleNamespace(returncode=0),
    ), patch.dict(os.environ, {"GITHUB_REF": "refs/heads/main", "GITHUB_SHA": expected}, clear=False):
        assert executor._require_main_checkout(ROOT, expected) == expected

    with patch.object(executor, "_git", side_effect=fake_git), patch.dict(
        os.environ, {"GITHUB_REF": "refs/heads/main", "GITHUB_SHA": expected}, clear=False,
    ), pytest.raises(ValueError, match="HEAD does not match"):
        executor._require_main_checkout(ROOT, "b" * 40)

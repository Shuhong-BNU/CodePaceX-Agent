from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from evals.evaluation_v2 import p3b_paid_executor as executor
from evals.evaluation_v2 import p3b_post_merge_rebind as p3b


ROOT = Path(__file__).resolve().parents[1]


def _inputs() -> dict[str, str | bool]:
    freeze = ROOT / p3b.ARTIFACT_DIRECTORY / p3b.FREEZE_NAME
    payload = json.loads(freeze.read_text(encoding="utf-8"))
    return {
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


@pytest.mark.parametrize("field,value,match", [
    ("expected_freeze_sha256", "0" * 64, "freeze SHA"),
    ("expected_allocation_hash", "0" * 64, "allocation hash"),
    ("approved_parent_cap_cny", "292.945920", "parent cap"),
    ("authorization_acknowledgement", "not-an-authorization", "acknowledgement"),
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
    assert "python -m evals.evaluation_v2.p3b_paid_executor" in workflow
    assert "--confirm-paid-execution" in workflow
    assert "p3b-paid-artifact-${{ github.run_id }}" in workflow
    assert "github.event_name == 'workflow_dispatch'" in workflow
    assert "exit 1" not in workflow


def test_duplicate_and_second_dispatch_remain_rejected(tmp_path: Path) -> None:
    guard = p3b.DispatchGuard(tmp_path / "dispatch-guard.json")
    guard.claim(dispatch_token="p3b1-recording-token-0001", run_id="p3b1-fake-run-0001")
    with pytest.raises(ValueError, match="duplicate"):
        guard.claim(dispatch_token="p3b1-recording-token-0001", run_id="p3b1-fake-run-0001")
    with pytest.raises(ValueError, match="second"):
        guard.claim(dispatch_token="p3b1-second-token-0002", run_id="p3b1-second-run-0002")

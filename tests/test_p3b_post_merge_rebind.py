from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from evals.evaluation_v2 import p3a_paired_pilot as p3a
from evals.evaluation_v2 import p3b_post_merge_rebind as p3b


ROOT = Path(__file__).resolve().parents[1]


def _records(frozen: dict) -> list[dict]:
    return [{
        **run, "artifact_path": run["expected_artifact_path"],
        "provider_requests": 0, "usage": 0, "charge_cny": "0",
        "provider_secret_read": False,
    } for run in frozen["task_runs"]]


def test_freeze_rebinds_merged_main_and_preserves_p3a_conditions() -> None:
    frozen = p3b.freeze_payload(ROOT)
    prior = p3a.freeze_payload(ROOT)
    assert frozen["bound_main_commit"] == p3b.BOUND_MAIN_COMMIT
    assert frozen["bound_main_commit"] != prior["bound_main_commit"]
    assert frozen["task_runs_sha256"] != prior["task_runs_sha256"]
    assert [run["instance_id"] for run in frozen["task_runs"]] == [run["instance_id"] for run in prior["task_runs"]]
    assert [run["treatment"] for run in frozen["task_runs"]] == [run["treatment"] for run in prior["task_runs"]]
    assert frozen["frozen_identities"] == {key: prior["frozen_identities"][key] for key in frozen["frozen_identities"]}
    assert frozen["execution_contract"] == {
        "strict_serial": True, "request_ceiling_per_run": 40, "retry": 0, "fallback": False,
        "automatic_retry_rerun_or_continuation": False, "only_treatment_difference": "treatment",
        "future_paid_execution_requires_new_user_authorization": True,
    }
    assert set(frozen["runtime_source_sha256"]) >= {str(p3b.WORKFLOW_PATH), "evals/evaluation_v2/p3b_post_merge_rebind.py"}


def test_formal_parent_and_children_close_exactly() -> None:
    frozen = p3b.freeze_payload(ROOT)
    parent = frozen["formal_stage_c_parent_authorization"]
    allocation = frozen["formal_stage_c_allocation"]
    children = frozen["formal_child_allocations"]
    assert parent["status"] == allocation["status"] == "formal_proposal_not_authorized"
    assert parent["experiment_commit"] == allocation["experiment_commit"] == p3b.BOUND_MAIN_COMMIT
    assert parent["authorized_total_cny"] == "292.945921"
    assert allocation["spendable_total_cny"] == "292.945920"
    assert allocation["safety_reserve_cny"] == "0.000001"
    assert len(children) == len({item["task_run_id"] for item in children}) == 8
    assert sum(float(item["theoretical_ceiling_cny"]) for item in children) == pytest.approx(292.945920)
    assert allocation["allocation_hash"] == p3b.allocation_hash(p3b.StageCBudgetAllocation.model_validate({key: value for key, value in allocation.items() if key not in {"allocation_hash", "status"}}))


def test_merge_rejects_incomplete_duplicate_and_unexpected_results() -> None:
    frozen = p3b.freeze_payload(ROOT)
    records = _records(frozen)
    assert len(p3b.merge_paired_results(frozen, records)) == 4
    with pytest.raises(ValueError, match="exactly eight"):
        p3b.merge_paired_results(frozen, records[:6])
    with pytest.raises(ValueError, match="exactly eight"):
        p3b.merge_paired_results(frozen, records[:-1])
    with pytest.raises(ValueError, match="duplicate"):
        p3b.merge_paired_results(frozen, records[:-1] + [dict(records[0])])
    unexpected = [dict(item) for item in records]; unexpected[0]["task_run_id"] = "unexpected"
    with pytest.raises(ValueError, match="unexpected"):
        p3b.merge_paired_results(frozen, unexpected)
    mismatch = [dict(item) for item in records]; mismatch[1]["pair_index"] = 4
    with pytest.raises(ValueError, match="pair key"):
        p3b.merge_paired_results(frozen, mismatch)


def test_dispatch_guard_rejects_replay_and_second_dispatch(tmp_path: Path) -> None:
    guard = p3b.DispatchGuard(tmp_path / "dispatch.json")
    guard.claim(dispatch_token="first-token-123", run_id="first-run-123")
    with pytest.raises(ValueError, match="duplicate"):
        guard.claim(dispatch_token="first-token-123", run_id="first-run-123")
    with pytest.raises(ValueError, match="second"):
        guard.claim(dispatch_token="second-token-456", run_id="second-run-456")


def test_unique_workflow_is_default_zero_provider_and_paid_fail_closed() -> None:
    workflow = (ROOT / p3b.WORKFLOW_PATH).read_text(encoding="utf-8")
    assert "name: P3-B paired Pilot" in workflow
    assert "p3b-zero-provider-readiness:" in workflow
    assert "p3b-paid-execution:" in workflow
    assert "inputs.paid_execution == true" in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "approved_parent_cap_cny == '292.945921'" in workflow
    assert "expected_freeze_sha256" in workflow
    assert "expected_allocation_hash" in workflow
    assert "dispatch_token" in workflow and "run_id" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "P3B_PROVIDER_SECRET_PRESENT: ${{ secrets.BAILIAN_API_KEY != '' }}" in workflow


def test_actual_zero_provider_rehearsal_writes_complete_closed_evidence(tmp_path: Path) -> None:
    output = tmp_path / "artifact"
    identities = p3b.write_artifacts(ROOT, output)
    frozen = json.loads((output / p3b.FREEZE_NAME).read_text())
    readiness = json.loads((output / p3b.READINESS_NAME).read_text())
    p3b.validate_freeze(ROOT, frozen)
    assert identities["freeze_sha256"] == hashlib.sha256((output / p3b.FREEZE_NAME).read_bytes()).hexdigest()
    assert readiness["freeze_sha256"] == identities["freeze_sha256"]
    assert readiness["freeze_canonical_sha256"] == identities["freeze_canonical_sha256"]
    assert readiness["freeze_sha256"] != readiness["freeze_canonical_sha256"]
    assert readiness["provider_requests"] == readiness["usage"] == 0
    assert readiness["charge_cny"] == "0" and readiness["provider_secret_read"] is False
    rehearsal = readiness["rehearsal"]
    assert rehearsal["agent_dispatch_count"] == 8
    assert rehearsal["recording_fake_transport_requests"] == 32
    assert rehearsal["ledger_settlement_count"] == 8 and rehearsal["ledger_closed"] is True
    assert rehearsal["active_reservation"] is None
    assert rehearsal["dispatch_guard_rejections"] == ["duplicate dispatch rejected", "second dispatch rejected"]
    assert readiness["paired_result_merge_count"] == 4
    for record in rehearsal["run_records"]:
        task_root = output / p3b.REHEARSAL_DIRECTORY / record["artifact_path"]
        assert all((task_root / name).is_file() for name in p3b.paired_artifact_schema()["required_raw_artifacts"])
        if record["treatment"] == "V2_CONTROL":
            assert not record["v3_advice_present"] and not record["v3_activation_schema_present"]
            assert not (task_root / "capability-v3").exists()
        else:
            assert record["v3_advice_present"] and record["v3_activation_schema_present"]
            assert record["treatment_fidelity"]["valid"] is True


def test_readiness_fails_when_raw_artifact_or_ledger_is_invalid(tmp_path: Path) -> None:
    output = tmp_path / "artifact"; p3b.write_artifacts(ROOT, output)
    frozen = json.loads((output / p3b.FREEZE_NAME).read_text())
    readiness = json.loads((output / p3b.READINESS_NAME).read_text())
    record = readiness["rehearsal"]["run_records"][0]
    (output / p3b.REHEARSAL_DIRECTORY / record["artifact_path"] / "official-report.json").unlink()
    with pytest.raises(ValueError, match="raw Artifact"):
        p3b.readiness_payload(ROOT, frozen, freeze_path=output / p3b.FREEZE_NAME, rehearsal=readiness["rehearsal"])
    (output / p3b.REHEARSAL_DIRECTORY / record["artifact_path"] / "official-report.json").write_text("{}", encoding="utf-8")
    rehearsal = dict(readiness["rehearsal"]); rehearsal["ledger_closed"] = False
    with pytest.raises(ValueError, match="complete zero-provider"):
        p3b.readiness_payload(ROOT, frozen, freeze_path=output / p3b.FREEZE_NAME, rehearsal=rehearsal)

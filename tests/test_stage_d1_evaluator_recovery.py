from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from evals import stage_d1_evaluator_recovery as recovery


def _source(tmp_path: Path) -> Path:
    root = tmp_path / "stage-d1-canary-29941188060"
    internal = root / "stage-d1-replacement-20260723-52d2b5d-29940818663"
    artifact = internal / "artifacts"
    artifact.mkdir(parents=True)
    patch = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
    prediction_name = hashlib.sha256(recovery.INSTANCE_ID.encode()).hexdigest() + "-prediction.json"
    (internal / prediction_name).write_text(json.dumps([{
        "instance_id": recovery.INSTANCE_ID, "model_name_or_path": recovery.MODEL_ID, "model_patch": patch,
    }]), encoding="utf-8")
    (internal / "manifest.json").write_text(json.dumps({"git_commit": recovery.SOURCE_COMMIT}), encoding="utf-8")
    (root / "canary-authorization.json").write_text(json.dumps({
        "approved_commit": recovery.SOURCE_COMMIT,
        "freeze_sha256": recovery.SOURCE_FREEZE_SHA256,
        "runtime_contract_hash": recovery.SOURCE_RUNTIME_CONTRACT_HASH,
        "pricing_snapshot_hash": recovery.SOURCE_PRICING_SHA256,
        "budget_stage_key": "STAGE_D1_CANARY",
        "task_ids": [recovery.INSTANCE_ID],
    }), encoding="utf-8")
    (root / "budget-authorization.json").write_text(json.dumps({
        "experiment_commit": recovery.SOURCE_COMMIT,
        "pricing_snapshot_hash": recovery.SOURCE_PRICING_SHA256,
    }), encoding="utf-8")
    (artifact / f"{hashlib.sha256(recovery.INSTANCE_ID.encode()).hexdigest()}-stdout.txt").write_text(
        "\n".join(json.dumps(item) for item in (
            {"type": "tool_result", "tool_name": "EditFile", "is_error": False},
            {"type": "tool_result", "tool_name": "RunTest", "is_error": False, "output": "1 passed"},
        )), encoding="utf-8")
    (internal / "validation-events.jsonl").write_text(
        "\n".join(json.dumps(item) for item in (
            {"event_type": "validation_declaration", "payload": {"action": "record_reproduction"}},
            {"event_type": "validation_checkpoint", "payload": {"ordinal": 20, "state": "acknowledged"}},
        )), encoding="utf-8")
    (root / "terminal-ledger.json").write_text(json.dumps({
        "active_reservation": None, "spent_cny": recovery.VERIFIED_COST_CNY,
        "request_charges": [{}] * 40, "settlements": [{}] * 40,
    }), encoding="utf-8")
    return root


def _bind_fixture_candidate(monkeypatch, source: Path) -> None:
    prediction = next((source / "stage-d1-replacement-20260723-52d2b5d-29940818663").glob("*-prediction.json"))
    patch = json.loads(prediction.read_text(encoding="utf-8"))[0]["model_patch"]
    monkeypatch.setattr(recovery, "SOURCE_CANDIDATE_SHA256", hashlib.sha256(patch.encode()).hexdigest())


def _install_fake_evaluator(monkeypatch, *, resolved: bool = False) -> None:
    monkeypatch.setattr(recovery, "_installed_evaluator", lambda: {
        "docker_available": True, "official_evaluator_module_available": True,
        "installed_evaluator_commit": recovery.OFFICIAL_EVALUATOR_COMMIT,
        "expected_evaluator_commit": recovery.OFFICIAL_EVALUATOR_COMMIT,
        "evaluator_commit_matches": True,
    })

    def fake_run(**kwargs: object) -> subprocess.CompletedProcess[str]:
        cwd = Path(str(kwargs["cwd"])); run_id = str(kwargs["run_id"])
        model = json.loads(Path(str(kwargs["predictions_path"])).read_text(encoding="utf-8"))[0]["model_name_or_path"]
        report = cwd / "logs" / "run_evaluation" / run_id / model / recovery.INSTANCE_ID / "report.json"
        report.parent.mkdir(parents=True)
        report.write_text(json.dumps({recovery.INSTANCE_ID: {
            "patch_is_None": False, "patch_exists": True, "patch_successfully_applied": True,
            "resolved": resolved, "tests_status": {},
        }}), encoding="utf-8")
        return subprocess.CompletedProcess([], 0, "official evaluator", "")

    monkeypatch.setattr(recovery, "run_official_evaluator", fake_run)


def test_audit_binds_the_preserved_candidate_without_mutation(tmp_path: Path, monkeypatch) -> None:
    source = _source(tmp_path)
    _bind_fixture_candidate(monkeypatch, source)
    original = (source / "terminal-ledger.json").read_bytes()
    audit = recovery.audit_source_candidate(source)
    assert audit["candidate_nonempty"] is True
    assert audit["candidate_matches_workspace_diff"] is True
    assert audit["tool_counts"] == {"EditFile": 1, "WriteFile": 0, "RunTest": 1}
    assert (source / "terminal-ledger.json").read_bytes() == original


def test_evaluator_smoke_is_zero_provider_and_collects_an_official_report(tmp_path: Path, monkeypatch) -> None:
    _install_fake_evaluator(monkeypatch)
    result = recovery.evaluator_smoke(output_root=tmp_path / "smoke", run_id="stage-d1-smoke")
    assert result["resolved"] is False
    assert result["provider_requests"] == result["usage"] == 0
    assert result["active_reservation"] is None


def test_recovery_uses_one_existing_candidate_without_provider(tmp_path: Path, monkeypatch) -> None:
    _install_fake_evaluator(monkeypatch)
    source = _source(tmp_path)
    _bind_fixture_candidate(monkeypatch, source)
    result = recovery.recover(source_root=source, evidence_root=tmp_path / "recovery", run_id="stage-d1-evaluator-only")
    assert result["outcome"] == "unresolved"
    assert result["provider_requests_added"] == result["usage_added"] == result["settlement_added"] == 0
    assert result["candidate_matches_workspace_diff"] is True
    assert result["recovery_identity"]["candidate_sha256"] == result["candidate_sha256"]
    assert (tmp_path / "recovery" / "official-report.json").is_file()


def test_evaluator_smoke_parses_a_resolved_report(tmp_path: Path, monkeypatch) -> None:
    _install_fake_evaluator(monkeypatch, resolved=True)
    result = recovery.evaluator_smoke(output_root=tmp_path / "smoke", run_id="stage-d1-smoke-resolved")
    assert result["resolved"] is True


def test_audit_fails_closed_when_the_candidate_or_freeze_binding_changes(tmp_path: Path, monkeypatch) -> None:
    source = _source(tmp_path)
    _bind_fixture_candidate(monkeypatch, source)
    prediction = next((source / "stage-d1-replacement-20260723-52d2b5d-29940818663").glob("*-prediction.json"))
    payload = json.loads(prediction.read_text(encoding="utf-8"))
    payload[0]["model_patch"] += "\n# tampered\n"
    prediction.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        recovery.audit_source_candidate(source)


def test_audit_rejects_an_empty_candidate(tmp_path: Path, monkeypatch) -> None:
    source = _source(tmp_path)
    _bind_fixture_candidate(monkeypatch, source)
    prediction = next((source / "stage-d1-replacement-20260723-52d2b5d-29940818663").glob("*-prediction.json"))
    payload = json.loads(prediction.read_text(encoding="utf-8"))
    payload[0]["model_patch"] = ""
    prediction.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        recovery.audit_source_candidate(source)


def test_recovery_workflow_is_manual_zero_provider_and_single_use_guarded() -> None:
    source = Path(__file__).resolve().parents[1] / ".github/workflows/stage-d1-evaluator-recovery.yml"
    workflow = source.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert "perform_recovery" in workflow
    assert "default: false" in workflow
    assert "BAILIAN_API_KEY" not in workflow
    assert "Refuse a second evaluator-only recovery" in workflow
    assert "stage_d1_freeze validate" in workflow

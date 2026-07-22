from __future__ import annotations

import json
from pathlib import Path

from evals.benchmark import current_git_commit
from evals import stage_d1_freeze, stage_d1_paid
from evals.paid_gate import BudgetLedger


ROOT = Path(__file__).resolve().parents[1]
FREEZE = ROOT / "evals/stage_d1/stage_d1_freeze.json"


def _identities() -> dict[str, str]:
    return stage_d1_paid.frozen_identities(ROOT, FREEZE)


def _source_row(instance_id: str) -> dict[str, object]:
    return {
        "instance_id": instance_id,
        "repo": "owner/repo",
        "base_commit": "a" * 40,
        "problem_statement": "Fix the failing bounded test.",
        "platform": None,
        "version": None,
        "environment_setup_commit": None,
    }


def _bundle(tmp_path: Path) -> Path:
    source = tmp_path / "formal-dataset.jsonl"
    source.write_text("".join(json.dumps(_source_row(instance_id)) + "\n" for instance_id in stage_d1_freeze.CANARY_INSTANCE_IDS), encoding="utf-8")
    bundle = tmp_path / "tasks.jsonl"
    stage_d1_paid.build_task_bundle(source_dataset=source, output=bundle)
    return bundle


def _prepare(tmp_path: Path) -> Path:
    identities = _identities()
    evidence = tmp_path / "stage-d1-evidence"
    result = stage_d1_paid.prepare(
        root=ROOT, freeze_path=FREEZE, evidence_root=evidence,
        approved_commit=current_git_commit(ROOT), authorization_identity="stage-d1-test-authorization",
        supplied_freeze_sha256=identities["freeze_sha256"],
        supplied_runtime_contract_hash=identities["runtime_contract_hash"],
        supplied_pricing_hash=identities["pricing_snapshot_hash"],
    )
    assert result["authorization_cap_cny"] == "15"
    return evidence


def test_stage_d1_preflight_and_authorization_are_zero_provider(tmp_path: Path, monkeypatch: object) -> None:
    identities = _identities()
    monkeypatch.setenv("BAILIAN_API_KEY", "offline-stage-d1-fixture")
    result = stage_d1_paid.preflight(
        root=ROOT, freeze_path=FREEZE, approved_commit=current_git_commit(ROOT),
        supplied_freeze_sha256=identities["freeze_sha256"],
        supplied_runtime_contract_hash=identities["runtime_contract_hash"],
        supplied_pricing_hash=identities["pricing_snapshot_hash"], require_secret=False,
    )
    assert result["provider_requests"] == result["usage"] == 0
    assert result["active_reservation"] is None
    assert result["next_request_maximum_cny"] == "1.830912"
    evidence = _prepare(tmp_path)
    ledger = BudgetLedger.model_validate_json((evidence / "terminal-ledger.json").read_text(encoding="utf-8"))
    assert ledger.spent_cny == 0
    assert ledger.active_reservation is None
    assert ledger.request_charges == ledger.settlements == []


def test_stage_d1_empty_candidate_stops_without_provider(tmp_path: Path, monkeypatch: object) -> None:
    evidence = _prepare(tmp_path)
    bundle = _bundle(tmp_path)

    def empty_executor(_task: object, _environment: object, _workspace: object) -> stage_d1_paid.TaskExecution:
        return stage_d1_paid.TaskExecution("", "", "", 0)

    monkeypatch.setattr("evals.paid_gate._git_is_clean", lambda _root: True)
    artifact = stage_d1_paid.execute(root=ROOT, freeze_path=FREEZE, evidence_root=evidence,
                                     run_id="stage-d1-zero-provider-test", task_bundle=bundle,
                                     confirmed=True, executor=empty_executor)
    assert artifact["terminal_statuses"] == {stage_d1_freeze.CANARY_INSTANCE_IDS[0]: "infrastructure_error"}
    ledger = BudgetLedger.model_validate_json((evidence / "terminal-ledger.json").read_text(encoding="utf-8"))
    assert ledger.request_charges == ledger.settlements == []
    assert ledger.active_reservation is None


def test_stage_d1_paid_workflow_is_manual_and_closed_by_default() -> None:
    source = (ROOT / ".github/workflows/stage-d1-canary-paid.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in source
    assert "default: false" in source
    assert "if: ${{ inputs.paid_execution }}" in source
    assert "stage_d1_freeze.json" in source

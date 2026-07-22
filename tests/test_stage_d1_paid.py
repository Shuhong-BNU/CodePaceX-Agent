from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.benchmark import current_git_commit
from evals import stage_d1_freeze, stage_d1_paid
from evals.costing import load_pricing
from evals.paid_gate import BudgetAuthorization, BudgetLedger, PaidRunGate, STAGE_D1_CANARY_BUDGET_STAGE


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
    monkeypatch.setattr("evals.paid_gate._git_is_clean", lambda _root: True)
    preflight_evidence = tmp_path / "paid-path-preflight"
    result = stage_d1_paid.preflight(
        root=ROOT, freeze_path=FREEZE, approved_commit=current_git_commit(ROOT),
        supplied_freeze_sha256=identities["freeze_sha256"],
        supplied_runtime_contract_hash=identities["runtime_contract_hash"],
        supplied_pricing_hash=identities["pricing_snapshot_hash"], require_secret=False,
        evidence_root=preflight_evidence,
    )
    assert result["provider_requests"] == result["usage"] == 0
    assert result["active_reservation"] is None
    assert result["next_request_maximum_cny"] == "1.830912"
    assert result["budget_stage_key"] == STAGE_D1_CANARY_BUDGET_STAGE
    assert result["preflight_reservation_cny"] == "1.830912"
    assert result["preflight_cancellation_status"] == "cancelled"
    assert result["preflight_cancellation_settlement_cny"] == "0"
    ledger = BudgetLedger.model_validate_json((preflight_evidence / "paid-path-preflight-ledger.json").read_text(encoding="utf-8"))
    assert ledger.active_reservation is None
    assert ledger.spent_cny == 0
    assert ledger.request_charges == []
    assert len(ledger.settlements) == 1
    assert ledger.settlements[0].stage == STAGE_D1_CANARY_BUDGET_STAGE
    assert ledger.settlements[0].status == "cancelled"
    evidence = _prepare(tmp_path)
    ledger = BudgetLedger.model_validate_json((evidence / "terminal-ledger.json").read_text(encoding="utf-8"))
    assert ledger.spent_cny == 0
    assert ledger.active_reservation is None
    assert ledger.request_charges == ledger.settlements == []


def test_stage_d1_empty_candidate_stops_without_provider(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setattr("evals.paid_gate._git_is_clean", lambda _root: True)
    evidence = _prepare(tmp_path)
    bundle = _bundle(tmp_path)

    def empty_executor(_task: object, _environment: object, _workspace: object) -> stage_d1_paid.TaskExecution:
        return stage_d1_paid.TaskExecution("", "", "", 0)

    artifact = stage_d1_paid.execute(root=ROOT, freeze_path=FREEZE, evidence_root=evidence,
                                     run_id="stage-d1-zero-provider-test", task_bundle=bundle,
                                     confirmed=True, executor=empty_executor)
    assert artifact["terminal_statuses"] == {stage_d1_freeze.CANARY_INSTANCE_IDS[0]: "infrastructure_error"}
    ledger = BudgetLedger.model_validate_json((evidence / "terminal-ledger.json").read_text(encoding="utf-8"))
    assert ledger.request_charges == ledger.settlements == []
    assert ledger.active_reservation is None


def test_stage_d1_budget_stage_key_is_registered_and_unregistered_stages_fail_closed(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setattr("evals.paid_gate._git_is_clean", lambda _root: True)
    identities = _identities()
    authorization = BudgetAuthorization(
        authorized_total_cny="15", stage_limits_cny={STAGE_D1_CANARY_BUDGET_STAGE: "15"},
        pricing_snapshot_hash=identities["pricing_snapshot_hash"], experiment_commit=current_git_commit(ROOT),
        authorized_at="offline-stage-d1-test",
    )
    with pytest.raises(ValueError, match="unregistered"):
        BudgetAuthorization(
            authorized_total_cny="15", stage_limits_cny={"D.1": "15"},
            pricing_snapshot_hash=identities["pricing_snapshot_hash"], experiment_commit=current_git_commit(ROOT),
            authorized_at="offline-stage-d1-test",
        )
    authorization_path = tmp_path / "authorization.json"
    ledger_path = tmp_path / "ledger.json"
    authorization_path.write_text(authorization.model_dump_json(), encoding="utf-8")
    ledger_path.write_text(BudgetLedger(
        authorization_hash=stage_d1_paid.stage_c_paid.authorization_hash(authorization),
        updated_at="offline-stage-d1-test",
    ).model_dump_json(), encoding="utf-8")
    gate = PaidRunGate(
        root=ROOT, authorization_path=authorization_path, ledger_path=ledger_path,
        pricing=load_pricing(ROOT / stage_d1_paid.PRICING_PATH), pricing_path=ROOT / stage_d1_paid.PRICING_PATH,
        stage="A",
    )
    with pytest.raises(ValueError, match="unregistered budget stage: A"):
        gate.reserve("stage-d1-unregistered", maximum_requests=1,
                     maximum_input_tokens_per_request=stage_d1_paid.MAX_INPUT_TOKENS,
                     maximum_output_tokens_per_request=stage_d1_paid.MAX_OUTPUT_TOKENS)


def test_stage_d1_runner_authorization_and_workflow_share_budget_stage_key(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setattr("evals.paid_gate._git_is_clean", lambda _root: True)
    evidence = _prepare(tmp_path)
    binding = json.loads((evidence / "canary-authorization.json").read_text(encoding="utf-8"))
    authorization = json.loads((evidence / "budget-authorization.json").read_text(encoding="utf-8"))
    _, gate = stage_d1_paid._gate(root=ROOT, evidence_root=evidence)
    assert binding["budget_stage_key"] == STAGE_D1_CANARY_BUDGET_STAGE
    assert set(authorization["stage_limits_cny"]) == {STAGE_D1_CANARY_BUDGET_STAGE}
    assert gate.stage == STAGE_D1_CANARY_BUDGET_STAGE
    workflow = (ROOT / ".github/workflows/stage-d1-canary-paid.yml").read_text(encoding="utf-8")
    assert "STAGE_D1_CANARY" in workflow
    assert "--preflight-evidence-root" in workflow


def test_stage_d1_paid_workflow_is_manual_and_closed_by_default() -> None:
    source = (ROOT / ".github/workflows/stage-d1-canary-paid.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in source
    assert "default: false" in source
    assert "if: ${{ inputs.paid_execution }}" in source
    assert "stage_d1_freeze.json" in source

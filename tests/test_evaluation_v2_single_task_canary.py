from __future__ import annotations

import json
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pytest

from evals.evaluation_v2 import single_task_canary as canary


ROOT = Path(__file__).resolve().parents[1]


def test_derived_freeze_is_fixed_to_the_approved_parent_and_single_task(tmp_path: Path) -> None:
    output = tmp_path / "single-task-freeze.json"
    identity = canary.write_freeze(ROOT, output)
    frozen = json.loads(output.read_text(encoding="utf-8"))

    assert frozen["task"]["instance_id"] == canary.TASK_ID
    assert frozen["parent_readiness"]["freeze_sha256"] == canary.PARENT_FREEZE_SHA256
    assert frozen["parent_readiness"]["runtime_contract_sha256"] == canary.PARENT_RUNTIME_HASH
    assert frozen["parent_readiness"]["pricing_snapshot_sha256"] == canary.PARENT_PRICING_HASH
    assert frozen["parent_readiness"]["run_id"] == canary.READINESS_RUN_ID
    assert frozen["parent_readiness"]["artifact_id"] == canary.READINESS_ARTIFACT_ID
    assert identity["runtime_contract_hash"] != canary.PARENT_RUNTIME_HASH
    assert frozen["budget_contract"]["single_task_spendable_cap_cny"] == "73.236480"
    assert frozen["budget_contract"]["authorization_hard_cap_cny"] == "73.236481"
    assert frozen["budget_contract"]["nonspendable_safety_reserve_cny"] == "0.000001"


def test_committed_derived_freeze_validates() -> None:
    result = canary.validate_freeze(ROOT)
    assert result["valid"] is True
    assert result["pricing_snapshot_hash"] == canary.PARENT_PRICING_HASH


def test_rehearsal_closes_each_ledger_and_preserves_exact_usage_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("evals.paid_gate._git_is_clean", lambda _root: True)
    monkeypatch.setattr(
        canary, "_loopback_agent_provider_dispatch",
        lambda *_args: (
            canary.control_canary.PaidTaskResult(
                canary.TASK_ID, "completed_without_candidate", "not_exported", "executed",
                "not_run", "completed", "completed", terminal_status="agent_no_candidate",
                provider_requests=1, live_executor_invoked=True, agent_dispatch_started=True,
                provider_client_initialized=True, model_response_observed=True,
            ),
            1,
        ),
    )
    preflight = tmp_path / "preflight-summary.json"
    preflight.write_text(json.dumps({"passed": True}), encoding="utf-8")

    result = canary.rehearse(ROOT, preflight, tmp_path / "rehearsal", "single-task-rehearsal-001")

    assert result["formal_trial_count"] == 0
    assert result["task"] == canary.TASK_ID
    assert result["provider_task_coverage"] == "1/1"
    assert result["provider_transport"] == "loopback_fake_openai_compatible"
    assert result["external_provider_transport"] is False
    assert result["loopback_simulated_provider_requests"] == 1
    assert result["external_provider_requests"] == result["provider_requests"] == result["usage"] == 0
    assert result["charge_cny"] == "0"
    assert result["ledger_closed"] is True and result["active_reservation"] is None
    violation = result["usage_contract_violation"]
    assert violation["terminal_status"] == "provider_usage_contract_violation"
    assert violation["candidate_status"] == "exported_nonempty"
    assert violation["evaluator_status"] == "not_run"
    diagnostics = violation["provider_usage_contract_violation"]["diagnostics"]
    assert diagnostics["raw_completion_tokens"] == diagnostics["raw_text_tokens"] == 8197
    assert diagnostics["raw_reasoning_tokens"] == 6144
    assert diagnostics["exceeded_by"] == {"completion_tokens": 5}

    for scenario in ("normal", "usage-contract-violation"):
        ledger = canary.BudgetLedger.model_validate_json(
            (tmp_path / "rehearsal" / scenario / "ledger.json").read_text(encoding="utf-8")
        )
        assert ledger.active_reservation is None
        assert len(ledger.request_charges) == len(ledger.settlements) == 1


def test_single_task_allocation_cannot_increase_spendable_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("evals.paid_gate._git_is_clean", lambda _root: True)
    artifact = tmp_path / "gate"
    artifact.mkdir()
    gate = canary._fresh_gate(ROOT, artifact, "test-only")
    allocation = canary.StageCBudgetAllocation.model_validate_json(
        (artifact / "single-task-allocation.json").read_text(encoding="utf-8")
    )
    assert allocation.spendable_total_cny == allocation.category_limits_cny["swe"] == Decimal("73.236480")
    assert allocation.safety_reserve_cny == Decimal("0.000001")
    assert gate.authorization.authorized_total_cny == Decimal("73.236481")
    assert gate.pricing_path == ROOT / canary.PRICING_PATH


def test_loopback_dispatch_uses_the_fixed_safe_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("evals.paid_gate._git_is_clean", lambda _root: True)

    @contextmanager
    def loopback(_scenario: str):
        yield type("Provider", (), {"request_count": 1})(), "http://127.0.0.1:1/v1"

    captured: dict[str, object] = {}
    def executor(**kwargs):
        captured.update(kwargs)
        return canary.control_canary.PaidTaskResult(
            canary.TASK_ID, "completed_without_candidate", "not_exported", "executed",
            "not_run", "completed", "completed", terminal_status="agent_no_candidate",
            agent_dispatch_started=True, provider_requests=1,
        )

    monkeypatch.setattr(canary.full_replay, "_loopback_fake_provider", loopback)
    monkeypatch.setattr(canary.control_canary, "_live_task_executor", executor)
    canary._loopback_agent_provider_dispatch(ROOT, tmp_path / "loopback", "loopback-001")

    payload = Path(str(captured["payload_path"]))
    assert payload.is_file()
    assert json.loads(payload.read_text(encoding="utf-8"))["instance_id"] == canary.TASK_ID
    assert captured["trial_namespace"] == "v2-single-task-zero-provider"


def test_paid_entry_rejects_wrong_freeze_before_any_executor(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Freeze SHA"):
        canary.run_paid(
            ROOT, tmp_path / "paid", "0" * 64, "73.236481", "test-only", "single-task-paid-001",
        )
    assert not (tmp_path / "paid").exists()


def test_workflow_is_fixed_task_zero_provider_by_default() -> None:
    workflow = (ROOT / canary.WORKFLOW_PATH).read_text(encoding="utf-8")
    zero_provider, paid = workflow.split("  paid-execution:", maxsplit=1)
    assert "inputs.paid_execution == false" in zero_provider
    assert "BAILIAN_API_KEY" not in zero_provider
    assert "aws-cloudformation__cfn-lint-3749" in workflow
    assert "paid_execution == true" in paid
    assert "evaluation-v2-full-20" not in workflow
    assert "retry" not in workflow.lower()

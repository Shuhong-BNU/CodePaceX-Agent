from __future__ import annotations

import json
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pytest

from evals.evaluation_v2 import single_task_canary as canary
from evals.evaluation_v2 import full_replay


ROOT = Path(__file__).resolve().parents[1]


def test_historical_parent_refuses_new_derived_freeze_before_any_execution(tmp_path: Path) -> None:
    """The committed single-task parent is historical and must never be rebound."""
    output = tmp_path / "single-task-freeze.json"
    parent = full_replay.validate_contract(ROOT)
    assert parent["freeze_sha256"] != canary.PARENT_FREEZE_SHA256
    assert parent["runtime_contract_sha256"] != canary.PARENT_RUNTIME_HASH
    with pytest.raises(ValueError, match="parent full-20 identity differs.*runtime_contract_sha256.*freeze_sha256"):
        canary.write_freeze(ROOT, output)
    assert not output.exists()


def test_historical_committed_freeze_fails_closed_on_current_parent_binding() -> None:
    with pytest.raises(ValueError, match="parent full-20 identity differs"):
        canary.validate_freeze(ROOT)


def test_historical_rehearsal_fails_closed_before_loopback_provider_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("evals.paid_gate._git_is_clean", lambda _root: True)
    dispatched = False
    def no_dispatch(*_args):
        nonlocal dispatched
        dispatched = True
        raise AssertionError("historical parent rejection must precede loopback dispatch")
    monkeypatch.setattr(canary, "_loopback_agent_provider_dispatch", no_dispatch)
    preflight = tmp_path / "preflight-summary.json"
    preflight.write_text(json.dumps({"passed": True}), encoding="utf-8")
    with pytest.raises(ValueError, match="parent full-20 identity differs"):
        canary.rehearse(ROOT, preflight, tmp_path / "rehearsal", "single-task-rehearsal-001")
    assert dispatched is False
    assert not (tmp_path / "rehearsal").exists()


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


def test_historical_paid_entry_rejects_before_any_provider_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor_called = False
    def no_executor(**_kwargs):
        nonlocal executor_called
        executor_called = True
        raise AssertionError("historical parent rejection must precede provider executor")
    monkeypatch.setattr(canary.control_canary, "_live_task_executor", no_executor)
    with pytest.raises(ValueError, match="parent full-20 identity differs"):
        canary.run_paid(
            ROOT, tmp_path / "paid", "0" * 64, "73.236481", "test-only", "single-task-paid-001",
        )
    assert executor_called is False
    assert not (tmp_path / "paid").exists()


def test_workflow_is_fixed_task_zero_provider_by_default() -> None:
    workflow = (ROOT / canary.WORKFLOW_PATH).read_text(encoding="utf-8")
    zero_provider, paid = workflow.split("  paid-execution:", maxsplit=1)
    assert "inputs.paid_execution == false" in zero_provider
    assert "BAILIAN_API_KEY" not in zero_provider
    assert "aws-cloudformation__cfn-lint-3749" in workflow
    assert "historical single-task parent fails closed before Provider transport" in workflow
    assert "historical_parent_binding_mismatch" in workflow
    assert "provider_transport_started" in workflow
    assert "paid_execution == true" in paid
    assert "evaluation-v2-full-20" not in workflow
    assert "retry" not in workflow.lower()

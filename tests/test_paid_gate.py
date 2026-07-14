import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from evals.costing import load_pricing, pricing_snapshot_hash
from evals.paid_gate import (
    BudgetAuthorization,
    BudgetLedger,
    PaidRunGate,
    StageCBudgetAllocation,
    actual_cost,
    authorization_hash,
    ledger_fingerprint,
    load_authorization,
    reconcile_unknown_usage,
    rebind_ledger_authorization,
    worst_case_reservation,
)


PRICING_PATH = Path("evals/goal2/pricing_bailian_qwen37_max_2026-07-13.json")
COMMIT = "a" * 40


def _authorization(path: Path, total: str = "100") -> None:
    pricing = load_pricing(PRICING_PATH)
    authorized_total = Decimal(total)
    payload = BudgetAuthorization(
        authorized_total_cny=authorized_total,
        stage_limits_cny={
            "A": min(Decimal("100"), authorized_total),
            "B": min(Decimal("400"), authorized_total),
            "C": authorized_total,
        },
        pricing_snapshot_hash=pricing_snapshot_hash(pricing),
        experiment_commit=COMMIT,
        authorized_at="2026-07-13T00:00:00Z",
    )
    path.write_text(payload.model_dump_json(indent=2), encoding="utf-8")


def _gate(tmp_path: Path, total: str = "100", stage: str = "A") -> PaidRunGate:
    authorization = tmp_path / "authorization.json"
    _authorization(authorization, total)
    with patch("evals.paid_gate._git_commit", return_value=COMMIT), patch(
        "evals.paid_gate._git_is_clean", return_value=True,
    ):
        return PaidRunGate(
            root=tmp_path, authorization_path=authorization,
            ledger_path=tmp_path / "ledger.json", pricing=load_pricing(PRICING_PATH),
            stage=stage,
        )


def test_cost_functions_use_frozen_standard_prices() -> None:
    pricing = load_pricing(PRICING_PATH)
    assert actual_cost(pricing, input_tokens=1_000_000, output_tokens=1_000_000) == Decimal("48.000000")
    assert worst_case_reservation(
        pricing, maximum_requests=2,
        maximum_input_tokens_per_request=1000,
        maximum_output_tokens_per_request=500,
    ) == Decimal("0.060000")


def test_gate_reserves_and_settles_each_provider_request_in_one_trial(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    with patch("evals.paid_gate._git_commit", return_value=COMMIT), patch(
        "evals.paid_gate._git_is_clean", return_value=True,
    ), gate.locked():
        reservation = gate.reserve(
            "mcp/task/1", maximum_requests=1,
            maximum_input_tokens_per_request=1000,
            maximum_output_tokens_per_request=500,
        )
        settlement = gate.settle(reservation, request_usages=[(1000, 500)])
        next_reservation = gate.reserve(
            "mcp/task/1", maximum_requests=1,
            maximum_input_tokens_per_request=1000,
            maximum_output_tokens_per_request=500,
        )
        next_settlement = gate.settle(next_reservation, request_usages=[(10, 1)])
    assert settlement.actual_cny == Decimal("0.030000")
    assert next_settlement.actual_cny == Decimal("0.000156")
    assert gate.summary()["remaining_cny"] == "99.969844"
    ledger = json.loads((tmp_path / "ledger.json").read_text())
    assert ledger["active_reservation"] is None
    assert [item["status"] for item in ledger["settlements"]] == ["settled", "settled"]
    assert ledger["request_charges"][0] == {
        "actual_cny": "0.030000",
        "input_tokens": 1000,
        "output_tokens": 500,
        "recorded_at": ledger["request_charges"][0]["recorded_at"],
        "request_index": 1,
        "reservation_id": reservation.reservation_id,
        "trial_id": "mcp/task/1",
    }
    assert ledger["request_charges"][1]["request_index"] == 2


def test_gate_enforces_stage_limit_before_total_authorization(tmp_path: Path) -> None:
    gate = _gate(tmp_path, "600", stage="A")
    ledger = {
        "schema_version": 2,
        "currency": "CNY",
        "authorization_hash": gate._authorization_hash,
        "spent_cny": "99.980000",
        "active_reservation": None,
        "request_charges": [],
        "settlements": [],
        "updated_at": "2026-07-13T00:00:00Z",
    }
    gate.ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    with patch("evals.paid_gate._git_commit", return_value=COMMIT), patch(
        "evals.paid_gate._git_is_clean", return_value=True,
    ), gate.locked(), pytest.raises(ValueError, match="stage A budget"):
        gate.reserve(
            "stage-a-overflow", maximum_requests=1,
            maximum_input_tokens_per_request=1000,
            maximum_output_tokens_per_request=500,
        )


def test_gate_settles_provider_overage_when_total_cost_remains_reserved(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    with patch("evals.paid_gate._git_commit", return_value=COMMIT), patch(
        "evals.paid_gate._git_is_clean", return_value=True,
    ), gate.locked():
        reservation = gate.reserve(
            "too-many-tokens", maximum_requests=1,
            maximum_input_tokens_per_request=1000,
            maximum_output_tokens_per_request=500,
        )
        settlement = gate.settle(reservation, request_usages=[(1001, 1)])
    assert settlement.actual_cny == Decimal("0.012048")


def test_gate_rejects_provider_usage_when_actual_cost_exceeds_reservation(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    with patch("evals.paid_gate._git_commit", return_value=COMMIT), patch(
        "evals.paid_gate._git_is_clean", return_value=True,
    ), gate.locked():
        reservation = gate.reserve(
            "too-expensive-after-provider-usage", maximum_requests=1,
            maximum_input_tokens_per_request=1000,
            maximum_output_tokens_per_request=500,
        )
        with pytest.raises(ValueError, match="observed token cost exceeded"):
            gate.settle(reservation, request_usages=[(1000, 501)])


def test_gate_fails_closed_when_worst_next_trial_exceeds_remaining_budget(tmp_path: Path) -> None:
    gate = _gate(tmp_path, "0.01")
    with patch("evals.paid_gate._git_commit", return_value=COMMIT), patch(
        "evals.paid_gate._git_is_clean", return_value=True,
    ), gate.locked(), pytest.raises(ValueError, match="insufficient stage A budget"):
        gate.reserve(
            "too-expensive", maximum_requests=1,
            maximum_input_tokens_per_request=1000,
            maximum_output_tokens_per_request=500,
        )


def test_gate_rejects_dirty_or_wrong_commit_authorization(tmp_path: Path) -> None:
    authorization = tmp_path / "authorization.json"
    _authorization(authorization)
    with patch("evals.paid_gate._git_commit", return_value="b" * 40), patch(
        "evals.paid_gate._git_is_clean", return_value=True,
    ), pytest.raises(ValueError, match="current HEAD"):
        PaidRunGate(
            root=tmp_path, authorization_path=authorization,
            ledger_path=tmp_path / "ledger.json", pricing=load_pricing(PRICING_PATH),
            stage="A",
        )


def test_gate_lock_prevents_concurrent_paid_sessions(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    gate.lock_path.parent.mkdir(parents=True, exist_ok=True)
    gate.lock_path.write_text("held", encoding="utf-8")
    with pytest.raises(ValueError, match="holds the budget lock"), gate.locked():
        pass


def test_stage_c_disabled_category_fails_before_any_provider_request(tmp_path: Path) -> None:
    authorization_path = tmp_path / "authorization.json"
    allocation_path = tmp_path / "allocation.json"
    ledger_path = tmp_path / "ledger.json"
    _authorization(authorization_path, "600")
    authorization = load_authorization(authorization_path)
    ledger = BudgetLedger(
        authorization_hash=authorization_hash(authorization),
        updated_at="2026-07-13T00:00:00Z",
    )
    ledger_path.write_text(ledger.model_dump_json(indent=2), encoding="utf-8")
    allocation = StageCBudgetAllocation(
        experiment_commit=COMMIT,
        pricing_snapshot_hash=authorization.pricing_snapshot_hash,
        baseline_ledger_sha256=ledger_fingerprint(ledger),
        baseline_authorization_hash=ledger.authorization_hash,
        baseline_spent_cny="0",
        baseline_request_charge_count=0,
        baseline_settlement_count=0,
        baseline_rebind_count=0,
        safety_reserve_cny="90",
        spendable_total_cny="510",
        category_limits_cny={
            "swe": "0", "mcp": "10", "retention": "0",
            "permission": "0", "multi_agent": "0", "long_session": "0",
        },
    )
    allocation_path.write_text(allocation.model_dump_json(indent=2), encoding="utf-8")
    with patch("evals.paid_gate._git_commit", return_value=COMMIT), patch(
        "evals.paid_gate._git_is_clean", return_value=True,
    ):
        gate = PaidRunGate(
            root=tmp_path, authorization_path=authorization_path, ledger_path=ledger_path,
            pricing=load_pricing(PRICING_PATH), stage="C", allocation_path=allocation_path,
        )
        with gate.locked(), pytest.raises(ValueError, match="Stage C swe category budget"):
            gate.reserve(
                "swe/formal/blocked", maximum_requests=1,
                maximum_input_tokens_per_request=1000,
                maximum_output_tokens_per_request=500,
            )
    ledger = BudgetLedger.model_validate_json(ledger_path.read_text(encoding="utf-8"))
    assert ledger.budget_blocks[-1].reason == "category_limit"


def test_stage_c_second_request_is_blocked_by_its_category_before_provider_call(tmp_path: Path) -> None:
    authorization_path = tmp_path / "authorization.json"
    allocation_path = tmp_path / "allocation.json"
    ledger_path = tmp_path / "ledger.json"
    _authorization(authorization_path, "600")
    authorization = load_authorization(authorization_path)
    ledger = BudgetLedger(authorization_hash=authorization_hash(authorization), updated_at="now")
    ledger_path.write_text(ledger.model_dump_json(indent=2), encoding="utf-8")
    allocation = StageCBudgetAllocation(
        experiment_commit=COMMIT, pricing_snapshot_hash=authorization.pricing_snapshot_hash,
        baseline_ledger_sha256=ledger_fingerprint(ledger),
        baseline_authorization_hash=ledger.authorization_hash, baseline_spent_cny="0",
        baseline_request_charge_count=0, baseline_settlement_count=0,
        baseline_rebind_count=0, safety_reserve_cny="90", spendable_total_cny="510",
        category_limits_cny={
            "swe": "0", "mcp": "0.035", "retention": "0",
            "permission": "0", "multi_agent": "0", "long_session": "0",
        },
    )
    allocation_path.write_text(allocation.model_dump_json(indent=2), encoding="utf-8")
    with patch("evals.paid_gate._git_commit", return_value=COMMIT), patch(
        "evals.paid_gate._git_is_clean", return_value=True,
    ):
        gate = PaidRunGate(
            root=tmp_path, authorization_path=authorization_path, ledger_path=ledger_path,
            pricing=load_pricing(PRICING_PATH), stage="C", allocation_path=allocation_path,
        )
        first = gate.reserve(
            "mcp/eager/task/1", maximum_requests=1,
            maximum_input_tokens_per_request=1000,
            maximum_output_tokens_per_request=500,
        )
        gate.settle(first, request_usages=[(1000, 500)])
        with pytest.raises(ValueError, match="mcp category budget"):
            gate.reserve(
                "mcp/eager/task/1", maximum_requests=1,
                maximum_input_tokens_per_request=1000,
                maximum_output_tokens_per_request=500,
            )
    after = BudgetLedger.model_validate_json(ledger_path.read_text(encoding="utf-8"))
    assert after.active_reservation is None
    assert len(after.request_charges) == 1
    assert after.budget_blocks[-1].reason == "category_limit"


def test_stage_c_unknown_trial_category_fails_closed_and_is_audited(tmp_path: Path) -> None:
    authorization_path = tmp_path / "authorization.json"
    allocation_path = tmp_path / "allocation.json"
    ledger_path = tmp_path / "ledger.json"
    _authorization(authorization_path, "600")
    authorization = load_authorization(authorization_path)
    ledger = BudgetLedger(authorization_hash=authorization_hash(authorization), updated_at="now")
    ledger_path.write_text(ledger.model_dump_json(indent=2), encoding="utf-8")
    allocation = StageCBudgetAllocation(
        experiment_commit=COMMIT, pricing_snapshot_hash=authorization.pricing_snapshot_hash,
        baseline_ledger_sha256=ledger_fingerprint(ledger),
        baseline_authorization_hash=ledger.authorization_hash, baseline_spent_cny="0",
        baseline_request_charge_count=0, baseline_settlement_count=0,
        baseline_rebind_count=0, safety_reserve_cny="90", spendable_total_cny="510",
        category_limits_cny={
            "swe": "0", "mcp": "1", "retention": "0",
            "permission": "0", "multi_agent": "0", "long_session": "0",
        },
    )
    allocation_path.write_text(allocation.model_dump_json(indent=2), encoding="utf-8")
    with patch("evals.paid_gate._git_commit", return_value=COMMIT), patch(
        "evals.paid_gate._git_is_clean", return_value=True,
    ):
        gate = PaidRunGate(
            root=tmp_path, authorization_path=authorization_path, ledger_path=ledger_path,
            pricing=load_pricing(PRICING_PATH), stage="C", allocation_path=allocation_path,
        )
        with pytest.raises(ValueError, match="no Stage C budget category"):
            gate.reserve(
                "unknown/task/1", maximum_requests=1,
                maximum_input_tokens_per_request=1000,
                maximum_output_tokens_per_request=500,
            )
    after = BudgetLedger.model_validate_json(ledger_path.read_text(encoding="utf-8"))
    assert after.budget_blocks[-1].reason == "unknown_category"


def test_authorization_rebind_preserves_settled_request_evidence(tmp_path: Path) -> None:
    old_path = tmp_path / "old-authorization.json"
    replacement_path = tmp_path / "replacement-authorization.json"
    _authorization(old_path, "600")
    old = load_authorization(old_path)
    with patch("evals.paid_gate._git_commit", return_value=COMMIT), patch(
        "evals.paid_gate._git_is_clean", return_value=True,
    ):
        gate = PaidRunGate(
            root=tmp_path, authorization_path=old_path,
            ledger_path=tmp_path / "ledger.json", pricing=load_pricing(PRICING_PATH), stage="A",
        )
        with gate.locked():
            reservation = gate.reserve(
                "mcp/pilot/1", maximum_requests=1,
                maximum_input_tokens_per_request=1000,
                maximum_output_tokens_per_request=500,
            )
            gate.settle(reservation, request_usages=[(1000, 500)])
    replacement = old.model_copy(update={"experiment_commit": "b" * 40})
    replacement_path.write_text(replacement.model_dump_json(indent=2), encoding="utf-8")
    rebound = rebind_ledger_authorization(
        tmp_path / "ledger.json", previous=old, replacement=replacement,
    )
    assert rebound.spent_cny == Decimal("0.030000")
    assert len(rebound.request_charges) == len(rebound.settlements) == 1
    assert rebound.authorization_hash == authorization_hash(replacement)
    assert len(rebound.authorization_rebinds) == 1


def test_rebind_detaches_a_settled_old_stage_c_allocation(tmp_path: Path) -> None:
    old_path = tmp_path / "old-authorization.json"
    replacement_path = tmp_path / "replacement-authorization.json"
    _authorization(old_path, "600")
    old = load_authorization(old_path)
    ledger = BudgetLedger(
        authorization_hash=authorization_hash(old), allocation_hash="a" * 64,
        updated_at="2026-07-14T00:00:00Z",
    )
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(ledger.model_dump_json(indent=2), encoding="utf-8")
    replacement = old.model_copy(update={"experiment_commit": "b" * 40})
    replacement_path.write_text(replacement.model_dump_json(indent=2), encoding="utf-8")
    rebound = rebind_ledger_authorization(
        ledger_path, previous=old, replacement=replacement,
    )
    assert rebound.allocation_hash is None
    assert rebound.authorization_rebinds[-1].previous_allocation_hash == "a" * 64


def test_request_with_missing_usage_keeps_active_reservation(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    with patch("evals.paid_gate._git_commit", return_value=COMMIT), patch(
        "evals.paid_gate._git_is_clean", return_value=True,
    ):
        reservation = gate.reserve(
            "mcp/task/missing-usage", maximum_requests=1,
            maximum_input_tokens_per_request=1000,
            maximum_output_tokens_per_request=500,
        )
    ledger = BudgetLedger.model_validate_json(gate.ledger_path.read_text(encoding="utf-8"))
    assert ledger.active_reservation is not None
    assert ledger.active_reservation.reservation_id == reservation.reservation_id
    assert not ledger.request_charges


def test_unknown_usage_is_conservatively_settled_without_fabricating_tokens(tmp_path: Path) -> None:
    authorization_path = tmp_path / "authorization.json"
    _authorization(authorization_path)
    gate = _gate(tmp_path)
    with patch("evals.paid_gate._git_commit", return_value=COMMIT), patch(
        "evals.paid_gate._git_is_clean", return_value=True,
    ):
        reservation = gate.reserve(
            "mcp/task/unknown-usage", maximum_requests=1,
            maximum_input_tokens_per_request=1000,
            maximum_output_tokens_per_request=500,
        )
    with pytest.raises(TypeError):
        gate.cancel(reservation)  # type: ignore[call-arg]
    settlement = reconcile_unknown_usage(
        gate.ledger_path, authorization=load_authorization(authorization_path),
        reservation_id=reservation.reservation_id,
        evidence_gap="no durable provider request ID, Usage, or billing evidence",
    )
    ledger = BudgetLedger.model_validate_json(gate.ledger_path.read_text(encoding="utf-8"))
    assert settlement.actual_cny == reservation.reserved_cny
    assert settlement.status == "conservative_settled"
    assert settlement.settlement_method == "conservative_reserved_amount"
    assert settlement.usage_status == "unknown"
    assert settlement.requests is settlement.input_tokens is settlement.output_tokens is None
    assert ledger.spent_cny == reservation.reserved_cny
    assert ledger.active_reservation is None
    assert not ledger.request_charges
    assert gate.trial_accounting("mcp/task/unknown-usage") == {
        "request_count": 0,
        "actual_cny": str(reservation.reserved_cny),
        "reservation_ids": [],
        "budget_blocked": False,
        "budget_block_reasons": [],
        "active_reservation": None,
        "settlement_count": 1,
        "usage_unknown": True,
        "claim_exclusion_reason": "unknown_provider_usage_conservative_reservation",
    }
    with pytest.raises(ValueError, match="not active"):
        reconcile_unknown_usage(
            gate.ledger_path, authorization=load_authorization(authorization_path),
            reservation_id=reservation.reservation_id, evidence_gap="duplicate",
        )


def test_conservative_settlement_debits_stage_c_category_before_next_request(tmp_path: Path) -> None:
    authorization_path = tmp_path / "authorization.json"
    allocation_path = tmp_path / "allocation.json"
    ledger_path = tmp_path / "ledger.json"
    _authorization(authorization_path, "600")
    authorization = load_authorization(authorization_path)
    ledger = BudgetLedger(authorization_hash=authorization_hash(authorization), updated_at="now")
    ledger_path.write_text(ledger.model_dump_json(indent=2), encoding="utf-8")
    allocation = StageCBudgetAllocation(
        experiment_commit=COMMIT, pricing_snapshot_hash=authorization.pricing_snapshot_hash,
        baseline_ledger_sha256=ledger_fingerprint(ledger),
        baseline_authorization_hash=ledger.authorization_hash, baseline_spent_cny="0",
        baseline_request_charge_count=0, baseline_settlement_count=0,
        baseline_rebind_count=0, safety_reserve_cny="90", spendable_total_cny="510",
        category_limits_cny={
            "swe": "0", "mcp": "0.030000", "retention": "0",
            "permission": "0", "multi_agent": "0", "long_session": "0",
        },
    )
    allocation_path.write_text(allocation.model_dump_json(indent=2), encoding="utf-8")
    with patch("evals.paid_gate._git_commit", return_value=COMMIT), patch(
        "evals.paid_gate._git_is_clean", return_value=True,
    ):
        gate = PaidRunGate(
            root=tmp_path, authorization_path=authorization_path, ledger_path=ledger_path,
            pricing=load_pricing(PRICING_PATH), stage="C", allocation_path=allocation_path,
        )
        reservation = gate.reserve(
            "mcp/run/deferred/task/1", maximum_requests=1,
            maximum_input_tokens_per_request=1000,
            maximum_output_tokens_per_request=500,
        )
        gate.conservatively_settle_unknown_usage(
            reservation, evidence_gap="Provider Usage was irrecoverable",
        )
        with pytest.raises(ValueError, match="mcp category budget"):
            gate.reserve(
                "mcp/run/deferred/task/2", maximum_requests=1,
                maximum_input_tokens_per_request=1000,
                maximum_output_tokens_per_request=500,
            )


def test_duplicate_settlement_is_rejected(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    with patch("evals.paid_gate._git_commit", return_value=COMMIT), patch(
        "evals.paid_gate._git_is_clean", return_value=True,
    ):
        reservation = gate.reserve(
            "mcp/task/duplicate", maximum_requests=1,
            maximum_input_tokens_per_request=1000,
            maximum_output_tokens_per_request=500,
        )
        gate.settle(reservation, request_usages=[(1, 1)])
        with pytest.raises(ValueError, match="not active"):
            gate.settle(reservation, request_usages=[(1, 1)])


def test_multi_request_reservation_is_refused(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    with pytest.raises(ValueError, match="exactly one Provider request"):
        gate.reserve(
            "mcp/task/legacy", maximum_requests=2,
            maximum_input_tokens_per_request=1000,
            maximum_output_tokens_per_request=500,
        )


def test_run_scoped_trial_ids_do_not_mix_request_accounting(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    with patch("evals.paid_gate._git_commit", return_value=COMMIT), patch(
        "evals.paid_gate._git_is_clean", return_value=True,
    ):
        for trial_id in ("mcp/run-a/eager/task/1", "mcp/run-b/eager/task/1"):
            reservation = gate.reserve(
                trial_id, maximum_requests=1,
                maximum_input_tokens_per_request=1000,
                maximum_output_tokens_per_request=500,
            )
            gate.settle(reservation, request_usages=[(1, 1)])
    assert gate.trial_accounting("mcp/run-a/eager/task/1")["request_count"] == 1
    assert gate.trial_accounting("mcp/run-b/eager/task/1")["request_count"] == 1

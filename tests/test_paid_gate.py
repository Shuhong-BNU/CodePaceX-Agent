import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from evals.costing import load_pricing, pricing_snapshot_hash
from evals.paid_gate import (
    BudgetAuthorization,
    PaidRunGate,
    actual_cost,
    worst_case_reservation,
)


PRICING_PATH = Path("evals/goal2/pricing_bailian_qwen37_max_2026-07-13.json")
COMMIT = "a" * 40


def _authorization(path: Path, total: str = "100") -> None:
    pricing = load_pricing(PRICING_PATH)
    payload = BudgetAuthorization(
        authorized_total_cny=Decimal(total),
        pricing_snapshot_hash=pricing_snapshot_hash(pricing),
        experiment_commit=COMMIT,
        authorized_at="2026-07-13T00:00:00Z",
    )
    path.write_text(payload.model_dump_json(indent=2), encoding="utf-8")


def _gate(tmp_path: Path, total: str = "100") -> PaidRunGate:
    authorization = tmp_path / "authorization.json"
    _authorization(authorization, total)
    with patch("evals.paid_gate._git_commit", return_value=COMMIT), patch(
        "evals.paid_gate._git_is_clean", return_value=True,
    ):
        return PaidRunGate(
            root=tmp_path, authorization_path=authorization,
            ledger_path=tmp_path / "ledger.json", pricing=load_pricing(PRICING_PATH),
        )


def test_cost_functions_use_frozen_standard_prices() -> None:
    pricing = load_pricing(PRICING_PATH)
    assert actual_cost(pricing, input_tokens=1_000_000, output_tokens=1_000_000) == Decimal("48.000000")
    assert worst_case_reservation(
        pricing, maximum_requests=2,
        maximum_input_tokens_per_request=1000,
        maximum_output_tokens_per_request=500,
    ) == Decimal("0.060000")


def test_gate_reserves_worst_next_trial_and_settles_actual_usage(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    with patch("evals.paid_gate._git_commit", return_value=COMMIT), patch(
        "evals.paid_gate._git_is_clean", return_value=True,
    ), gate.locked():
        reservation = gate.reserve(
            "mcp/task/1", maximum_requests=2,
            maximum_input_tokens_per_request=1000,
            maximum_output_tokens_per_request=500,
        )
        settlement = gate.settle(
            reservation, requests=1, input_tokens=1000, output_tokens=500,
        )
    assert settlement.actual_cny == Decimal("0.030000")
    assert gate.summary()["remaining_cny"] == "99.970000"
    ledger = json.loads((tmp_path / "ledger.json").read_text())
    assert ledger["active_reservation"] is None
    assert ledger["settlements"][0]["status"] == "settled"


def test_gate_fails_closed_when_worst_next_trial_exceeds_remaining_budget(tmp_path: Path) -> None:
    gate = _gate(tmp_path, "0.01")
    with patch("evals.paid_gate._git_commit", return_value=COMMIT), patch(
        "evals.paid_gate._git_is_clean", return_value=True,
    ), gate.locked(), pytest.raises(ValueError, match="insufficient authorized budget"):
        gate.reserve(
            "too-expensive", maximum_requests=2,
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
        )


def test_gate_lock_prevents_concurrent_paid_sessions(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    gate.lock_path.parent.mkdir(parents=True, exist_ok=True)
    gate.lock_path.write_text("held", encoding="utf-8")
    with pytest.raises(ValueError, match="holds the budget lock"), gate.locked():
        pass

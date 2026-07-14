from decimal import Decimal
from pathlib import Path

from evals.costing import load_pricing, pricing_snapshot_hash
from evals.paid_gate import (
    BudgetAuthorization,
    BudgetLedger,
    RequestCharge,
    Settlement,
    authorization_hash,
)
from evals.stage_c_budget import derive_allocation, formal_trial_counts


PRICING = Path("evals/goal2/pricing_bailian_qwen37_max_2026-07-13.json")


def test_stage_c_budget_uses_all_observed_categories_and_disables_swe_long(tmp_path: Path) -> None:
    pricing = load_pricing(PRICING)
    authorization = BudgetAuthorization(
        authorized_total_cny="600",
        stage_limits_cny={"A": "100", "B": "400", "C": "600"},
        pricing_snapshot_hash=pricing_snapshot_hash(pricing),
        experiment_commit="a" * 40, authorized_at="2026-07-14T00:00:00Z",
    )
    authorization_path = tmp_path / "authorization.json"
    authorization_path.write_text(authorization.model_dump_json(indent=2), encoding="utf-8")
    charges = [
        RequestCharge(
            reservation_id=f"r-{category}", trial_id=f"{prefix}/pilot/1",
            request_index=1, input_tokens=1, output_tokens=1, actual_cny="0.010000",
            recorded_at="2026-07-14T00:00:00Z",
        )
        for category, prefix in (
            ("mcp", "mcp"), ("retention", "retention"),
            ("permission", "permission"), ("multi", "multi"),
            ("pilot", "pilot"),
        )
    ]
    settlements = [Settlement(
        reservation_id=charge.reservation_id, trial_id=charge.trial_id, stage="B",
        requests=1, input_tokens=1, output_tokens=1, actual_cny=charge.actual_cny,
        status="settled", settled_at="2026-07-14T00:00:00Z",
    ) for charge in charges]
    ledger = BudgetLedger(
        authorization_hash=authorization_hash(authorization), spent_cny=Decimal("0.050000"),
        request_charges=charges, settlements=settlements, updated_at="2026-07-14T00:00:00Z",
    )
    allocation = derive_allocation(
        ledger=ledger, authorization_path=authorization_path, pricing_path=PRICING,
        studies_path=Path("evals/goal2/studies.yaml"),
        mcp_study_path=Path("evals/goal2/mcp_study.yaml"),
    )
    assert formal_trial_counts(
        studies_path=Path("evals/goal2/studies.yaml"),
        mcp_study_path=Path("evals/goal2/mcp_study.yaml"),
    ) == {
        "swe": 0, "mcp": 300, "retention": 20,
        "permission": 200, "multi_agent": 50, "long_session": 0,
    }
    assert allocation.category_limits_cny == {
        "swe": Decimal("0"), "mcp": Decimal("6.000000"),
        "retention": Decimal("0.688128"), "permission": Decimal("4.000000"),
        "multi_agent": Decimal("1.830912"), "long_session": Decimal("0"),
    }
    assert allocation.safety_reserve_cny == Decimal("90.000000")

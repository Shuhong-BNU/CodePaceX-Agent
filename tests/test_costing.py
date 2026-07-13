from pathlib import Path

from evals.costing import estimate_scenarios, load_pricing


PRICING = Path("evals/goal2/pricing_bailian_qwen37_max_2026-07-13.json")
STUDIES = Path("evals/goal2/studies.yaml")


def test_official_pricing_snapshot_is_frozen_conservatively() -> None:
    pricing = load_pricing(PRICING)
    assert pricing.input_price == 12.0
    assert pricing.output_price == 36.0
    assert any("Do not assume" in item for item in pricing.assumptions)


def test_cost_estimate_distinguishes_top_level_runs_from_provider_requests() -> None:
    estimate = estimate_scenarios(pricing=load_pricing(PRICING), studies_path=STUDIES)
    assert estimate["top_level_paid_runs"] == 608
    assert estimate["long_session_workload_cycles"] == 104
    scenarios = estimate["scenarios"]
    assert scenarios["minimum"]["provider_requests"] == 708
    assert scenarios["expected"]["provider_requests"] == 2624
    assert scenarios["hard_engineering_ceiling"]["provider_requests"] == 31240
    assert scenarios["minimum"]["estimated_cny"] < scenarios["expected"]["estimated_cny"]
    assert scenarios["expected"]["estimated_cny"] < scenarios["hard_engineering_ceiling"]["estimated_cny"]

"""Conservative CNY estimates for the frozen Goal 2 paid-run matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from evals.goal2_studies import load_studies, planned_paid_runs


class PricingSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    retrieved_at: str
    source_url: str
    rate_limit_source_url: str
    provider: Literal["bailian-qwen37-max"]
    model_id: Literal["qwen3.7-max-2026-06-08"]
    deployment_scope: Literal["Chinese mainland"]
    region: Literal["China (Beijing)"]
    currency: Literal["CNY"]
    token_range: Literal["0<Token<=1M"]
    unit_tokens: Literal[1000000]
    input_price: Literal[12.0]
    output_price: Literal[36.0]
    requests_per_minute: Literal[600]
    tokens_per_minute: Literal[1000000]
    assumptions: list[str] = Field(min_length=3)

    def cost(self, *, requests: int, input_tokens: int, output_tokens: int) -> float:
        return requests * (
            input_tokens * self.input_price / self.unit_tokens
            + output_tokens * self.output_price / self.unit_tokens
        )


def load_pricing(path: Path) -> PricingSnapshot:
    return PricingSnapshot.model_validate_json(path.read_text(encoding="utf-8"))


def pricing_snapshot_hash(pricing: PricingSnapshot) -> str:
    return hashlib.sha256(
        json.dumps(
            pricing.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
        ).encode()
    ).hexdigest()


def estimate_scenarios(
    *, pricing: PricingSnapshot, studies_path: Path,
) -> dict[str, object]:
    studies = load_studies(studies_path)
    top = planned_paid_runs(studies)
    ordinary_runs = top["total"] - top["long_session"]
    long_cycles = studies.long_session.workload_cycle_count()
    scenarios = {
        "minimum": {
            "provider_requests": ordinary_runs + long_cycles,
            "average_input_tokens_per_request": 2000,
            "average_output_tokens_per_request": 500,
        },
        "expected": {
            "provider_requests": ordinary_runs * 4 + long_cycles * 2,
            "average_input_tokens_per_request": 12000,
            "average_output_tokens_per_request": 2000,
        },
        "hard_engineering_ceiling": {
            "provider_requests": (
                ordinary_runs * 50
                + long_cycles * studies.long_session.maximum_provider_requests_per_cycle
            ),
            "average_input_tokens_per_request": 128000,
            "average_output_tokens_per_request": 8192,
        },
    }
    for scenario in scenarios.values():
        scenario["estimated_cny"] = round(pricing.cost(
            requests=int(scenario["provider_requests"]),
            input_tokens=int(scenario["average_input_tokens_per_request"]),
            output_tokens=int(scenario["average_output_tokens_per_request"]),
        ), 2)
    return {
        "schema_version": 1,
        "currency": pricing.currency,
        "top_level_paid_runs": top["total"],
        "ordinary_top_level_runs": ordinary_runs,
        "long_session_workload_cycles": long_cycles,
        "scenarios": scenarios,
        "pricing_snapshot_hash": pricing_snapshot_hash(pricing),
        "warning": "Provider requests, not top-level Runs, determine actual cost.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Estimate frozen Goal 2 costs")
    parser.add_argument(
        "--pricing", type=Path,
        default=Path("evals/goal2/pricing_bailian_qwen37_max_2026-07-13.json"),
    )
    parser.add_argument(
        "--studies", type=Path, default=Path("evals/goal2/studies.yaml"),
    )
    args = parser.parse_args(argv)
    payload = estimate_scenarios(
        pricing=load_pricing(args.pricing), studies_path=args.studies,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from pathlib import Path

from evals.goal2_studies import (
    load_studies,
    planned_paid_runs,
    retention_canaries,
)


CONFIG = Path("evals/goal2/studies.yaml")


def test_frozen_goal2_paid_run_count_is_608() -> None:
    studies = load_studies(CONFIG)
    assert planned_paid_runs(studies) == {
        "minimum_pilot": 1,
        "swe_bench": 33,
        "mcp_tool_loading": 300,
        "retention": 20,
        "permission": 200,
        "multi_agent": 50,
        "hook": 0,
        "long_session": 4,
        "total": 608,
    }


def test_retention_has_120_unique_opaque_canaries() -> None:
    study = load_studies(CONFIG).retention
    canaries = [
        value for index in range(10)
        for value in retention_canaries(study, index)
    ]
    assert len(canaries) == len(set(canaries)) == 120
    assert all(value.startswith("CNY-") and len(value) == 28 for value in canaries)


def test_swe_and_long_session_boundaries_are_frozen() -> None:
    studies = load_studies(CONFIG)
    assert studies.swe_bench.branch == "python-only"
    assert studies.swe_bench.split == "lite"
    assert studies.long_session.maximum_concurrent_paid_sessions == 1
    assert studies.long_session.formal.count == 3

import pytest

from evals.hook_study import PATHS, run_case, run_study


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PATHS)
async def test_each_hook_execution_path_has_allow_and_deny_case(path: str) -> None:
    allowed = await run_case(path, 1)
    denied = await run_case(path, 2)
    assert allowed.passed and allowed.expected_effect == "allow"
    assert denied.passed and denied.expected_effect == "deny"
    assert allowed.target_sentinel_exists and allowed.target_subprocess_count > 0
    assert not denied.target_sentinel_exists and denied.target_subprocess_count == 0
    assert allowed.target_network_attempt_count == denied.target_network_attempt_count == 0


@pytest.mark.asyncio
async def test_full_hook_study_has_100_zero_model_cases() -> None:
    result = await run_study()
    assert result["case_count"] == result["passed_case_count"] == 100
    assert result["rate"] == 1.0
    assert result["model_called"] is result["network_called"] is False
    assert len(result["git_commit"]) == 40
    assert len(result["benchmark_asset_hash"]) == 64

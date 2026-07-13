import pytest

from evals.hook_study import PATHS, run_case, run_study


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PATHS)
async def test_each_hook_execution_path_has_allow_and_deny_case(path: str) -> None:
    allowed = await run_case(path, 1)
    denied = await run_case(path, 2)
    assert allowed.passed and allowed.expected_effect == "allow"
    assert denied.passed and denied.expected_effect == "deny"


@pytest.mark.asyncio
async def test_full_hook_study_has_100_zero_model_cases() -> None:
    result = await run_study()
    assert result["case_count"] == result["passed_case_count"] == 100
    assert result["rate"] == 1.0
    assert result["model_called"] is result["network_called"] is False

from __future__ import annotations

import pytest

from codepacex.validation import OperationClass, classify_bash_command, classify_operation


@pytest.mark.parametrize(("command", "expected"), [
    ("git status", OperationClass.READ_ONLY),
    ("git diff -- src/module.py", OperationClass.READ_ONLY),
    ("pytest tests/test_one.py", OperationClass.TEST_EXECUTION),
    ("python -m pytest tests", OperationClass.TEST_EXECUTION),
    ("uv run pytest tests", OperationClass.TEST_EXECUTION),
    ("sed -i '' 's/old/new/' file.py", OperationClass.IMPLEMENTATION_WRITE),
    ("git apply change.patch", OperationClass.IMPLEMENTATION_WRITE),
    ("echo changed > file.py", OperationClass.IMPLEMENTATION_WRITE),
    ("custom-tool --mutate", OperationClass.UNKNOWN_SIDE_EFFECT),
    ("pytest tests; rm file.py", OperationClass.TEST_EXECUTION),
])
def test_bash_classifier_is_deterministic(command: str, expected: OperationClass) -> None:
    assert classify_bash_command(command) is expected


def test_mcp_is_conservatively_side_effecting() -> None:
    assert classify_operation(
        "server_tool", "command", {}, tool_module="codepacex.mcp.tool_wrapper",
    ) is OperationClass.UNKNOWN_SIDE_EFFECT


def test_plan_artifact_write_is_narrow(tmp_path) -> None:
    plan = tmp_path / "plan.md"
    assert classify_operation(
        "WriteFile", "write", {"file_path": str(plan)}, plan_mode=True,
        plan_artifact_path=str(plan),
    ) is OperationClass.PLAN_ARTIFACT_WRITE
    assert classify_operation(
        "WriteFile", "write", {"file_path": str(tmp_path / "source.py")}, plan_mode=True,
        plan_artifact_path=str(plan),
    ) is OperationClass.IMPLEMENTATION_WRITE

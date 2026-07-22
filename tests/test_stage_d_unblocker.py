from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from codepacex.agent import Agent
from codepacex.conversation import ConversationManager
from codepacex.permissions.checker import PermissionChecker
from codepacex.permissions.dangerous import DangerousCommandDetector
from codepacex.permissions.modes import PermissionMode
from codepacex.permissions.rules import RuleEngine
from codepacex.permissions.sandbox import PathSandbox
from codepacex.tools.edit_file import EditFile, Params as EditParams
from codepacex.tools.run_test import RunTest, RunTestParams
from codepacex.tools import ToolRegistry
from codepacex.tools.base import StreamEnd, ToolCallComplete
from codepacex.tools.validation_checkpoint import ValidationCheckpoint, ValidationCheckpointParams
from codepacex.validation import OperationClass, ValidationController, ValidationProfile, classify_bash_command


def _inventory() -> dict:
    return {
        "target_behavior": "increment must add one",
        "failure_assertions": ["test_increment"],
        "touched_symbols": ["increment"],
        "direct_callers": ["test_app"],
        "implementations": ["app.py"],
        "config_surfaces": [], "default_values": [], "serialization_surfaces": [], "fixtures": [],
        "target_tests": [{"command": "pytest test_app.py", "scope": ["test_app.py"]}],
        "regression_tests": [{"command": "pytest test_app.py"}], "known_unknowns": [],
    }


def _checker(root: Path) -> PermissionChecker:
    return PermissionChecker(DangerousCommandDetector(), PathSandbox(root), RuleEngine(), PermissionMode.DEFAULT, session_allow_all=True)


def _observe(controller: ValidationController, tool_id: str, tool, arguments: dict, result) -> None:
    controller.observe_tool_result(
        agent_id="agent", parent_agent_id=None, workspace_id="workspace", tool_call_id=tool_id,
        tool_name=tool.name, tool_category=tool.category, tool_module=tool.__class__.__module__,
        arguments=arguments, is_error=result.is_error, output=result.output,
        exit_code=result.exit_code, timed_out=result.timed_out,
    )


class _ScriptedClient:
    def __init__(self, responses: list[list[object]]) -> None:
        self._responses = responses
        self._call = 0

    async def stream(self, *args, **kwargs):
        response = self._responses[self._call]
        self._call += 1
        for event in response:
            yield event

    def set_max_output_tokens(self, value: int) -> None:
        return None


@pytest.mark.asyncio
async def test_stage_d_live_like_edit_test_export_loop(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def increment(value):\n    return value\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text("from app import increment\n\ndef test_increment():\n    assert increment(1) == 2\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=test@example.invalid", "-c", "user.name=Test", "commit", "-qm", "initial"], cwd=tmp_path, check=True)

    controller = ValidationController(ValidationProfile.stage_b(), state_dir=tmp_path / ".validation")
    checkpoint = ValidationCheckpoint(controller, "agent")
    run_test = RunTest()
    checker = _checker(tmp_path)
    test_args = {"cwd": str(tmp_path), "argv": ["test_app.py"], "timeout_seconds": 30, "output_cap_chars": 4000}
    assert checker.check(run_test, test_args).effect == "allow"
    failed = await run_test.execute(RunTestParams(**test_args))
    assert failed.is_error and failed.exit_code == 1
    _observe(controller, "repro", run_test, test_args, failed)
    declared = await checkpoint.execute(ValidationCheckpointParams(action="record_reproduction", use_recent_observed_result=True, reproduction_status="observed_failure"))
    assert not declared.is_error
    inventory = await checkpoint.execute(ValidationCheckpointParams(action="declare_contract_inventory", contract_inventory=_inventory()))
    assert not inventory.is_error
    baseline = await run_test.execute(RunTestParams(**test_args))
    _observe(controller, "baseline", run_test, test_args, baseline)

    edit = EditFile()
    edit_args = {"file_path": str(tmp_path / "app.py"), "old_string": "return value", "new_string": "return value + 1"}
    assert checker.check(edit, edit_args).effect == "allow"
    allowed = controller.assess_tool(agent_id="agent", parent_agent_id=None, workspace_id="workspace", tool_call_id="edit", tool_name=edit.name, tool_category=edit.category, tool_module=edit.__class__.__module__, arguments=edit_args)
    assert allowed.allowed
    edited = await edit.execute(EditParams(**edit_args))
    assert not edited.is_error
    _observe(controller, "edit", edit, edit_args, edited)
    passed = await run_test.execute(RunTestParams(**test_args))
    assert not passed.is_error and passed.exit_code == 0
    _observe(controller, "target-post", run_test, test_args, passed)
    _observe(controller, "regression-post", run_test, test_args, passed)
    for _ in range(20):
        controller.observe_request_completed(agent_id="agent")
    ack = await checkpoint.execute(ValidationCheckpointParams(action="ack_request_checkpoint", checkpoint_ordinal=20, checkpoint_summary="target and regression evidence recorded", checkpoint_details={"reproduction_status": "observed", "root_cause_hypothesis": "off by one", "inventory_revision": 1, "target_tests_registered": 1, "target_tests_executed": 1}))
    assert not ack.is_error
    assert controller.assess_completion(agent_id="agent").allowed
    assert subprocess.run(["git", "diff", "--", "app.py"], cwd=tmp_path, text=True, capture_output=True, check=True).stdout


@pytest.mark.asyncio
async def test_stage_d_scripted_agent_executes_the_unblocked_tool_path(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def increment(value):\n    return value\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text("from app import increment\n\ndef test_increment():\n    assert increment(1) == 2\n", encoding="utf-8")
    test_args = {"cwd": str(tmp_path), "argv": ["test_app.py"]}
    inventory = _inventory()
    client = _ScriptedClient([
        [ToolCallComplete("repro", "RunTest", test_args), StreamEnd("tool_use")],
        [ToolCallComplete("reproduction", "ValidationCheckpoint", {"action": "record_reproduction", "use_recent_observed_result": True, "reproduction_status": "observed_failure"}), StreamEnd("tool_use")],
        [ToolCallComplete("inventory", "ValidationCheckpoint", {"action": "declare_contract_inventory", "contract_inventory": inventory}), StreamEnd("tool_use")],
        [ToolCallComplete("baseline", "RunTest", test_args), StreamEnd("tool_use")],
        [ToolCallComplete("edit", "EditFile", {"file_path": str(tmp_path / "app.py"), "old_string": "return value", "new_string": "return value + 1"}), StreamEnd("tool_use")],
        [ToolCallComplete("post", "RunTest", test_args), StreamEnd("tool_use")],
        [StreamEnd("end_turn")],
    ])
    registry = ToolRegistry()
    registry.register(EditFile())
    agent = Agent(
        client, registry, "openai-compat", work_dir=str(tmp_path),
        permission_checker=_checker(tmp_path), validation_profile=ValidationProfile.stage_b(),
    )
    result = await agent.run_to_completion("repair increment")
    assert result == ""
    assert "return value + 1" in (tmp_path / "app.py").read_text(encoding="utf-8")
    summary = agent.validation_controller.summary()
    assert summary["reproduction"]["evidence_reference"] == "repro"
    assert summary["regression_comparisons"][0]["baseline"]["exit_code"] == 1
    assert summary["regression_comparisons"][0]["post"]["exit_code"] == 0


@pytest.mark.asyncio
async def test_checkpoint_rejects_forged_id_and_invalid_exception(tmp_path: Path) -> None:
    controller = ValidationController(ValidationProfile.stage_b(), state_dir=tmp_path / ".validation")
    checkpoint = ValidationCheckpoint(controller, "agent")
    forged = await checkpoint.execute(ValidationCheckpointParams(action="record_reproduction", observed_tool_call_id="invented", reproduction_status="observed_failure"))
    assert forged.is_error and '"error_code": "validation_checkpoint_rejected"' in forged.output
    with pytest.raises(ValidationError):
        ValidationCheckpointParams(action="record_reproduction_exception", reproduction_exception_reason="not_a_reason")


def test_run_test_rejects_unbounded_pytest_arguments() -> None:
    for argv in (["--rootdir=/tmp", "test_app.py"], ["-k", "increment"], ["../outside.py"], ["/tmp/outside.py"], ["."], ["test_app.py && whoami"]):
        with pytest.raises(ValidationError):
            RunTestParams(cwd=".", argv=argv)


def test_recent_observation_binding_survives_controller_recovery(tmp_path: Path) -> None:
    profile = ValidationProfile.stage_b()
    controller = ValidationController(profile, state_dir=tmp_path / ".validation")
    controller.observe_tool_result(
        agent_id="agent", parent_agent_id=None, workspace_id="workspace", tool_call_id="actual",
        tool_name="RunTest", tool_category="command", tool_module="codepacex.tools.run_test",
        arguments={"cwd": str(tmp_path), "argv": ["test_app.py"]}, is_error=True,
        output="failed", exit_code=1, timed_out=False,
    )
    recovered = ValidationController(
        profile, session_id=controller.session_id, state_dir=tmp_path / ".validation",
    )
    assert recovered.latest_observed_tool_call_id() == "actual"


def test_compound_bash_stays_blocked() -> None:
    assert classify_bash_command("cd workspace && pytest test_app.py") is OperationClass.UNKNOWN_SIDE_EFFECT

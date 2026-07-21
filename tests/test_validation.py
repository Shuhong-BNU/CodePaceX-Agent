from __future__ import annotations

from pathlib import Path

from codepacex.validation import (
    CompletionStatus,
    OperationClass,
    ValidationController,
    ValidationProfile,
)


def _inventory() -> dict:
    return {
        "target_behavior": "preserve the command contract",
        "failure_assertions": ["target regression"],
        "touched_symbols": ["command.run"],
        "direct_callers": ["cli.main"],
        "implementations": ["src/command.py"],
        "config_surfaces": [],
        "default_values": [],
        "serialization_surfaces": [],
        "fixtures": [],
        "target_tests": [{"command": "pytest tests/test_target.py", "scope": ["tests/test_target.py"]}],
        "regression_tests": [{"command": "pytest tests/test_command.py"}],
        "known_unknowns": [],
    }


def _observe(controller: ValidationController, tool_id: str, command: str, *, error: bool, output: str, exit_code: int) -> None:
    controller.observe_tool_result(
        agent_id="agent", parent_agent_id=None, workspace_id="workspace", tool_call_id=tool_id,
        tool_name="Bash", tool_category="command", tool_module="codepacex.tools.bash",
        arguments={"command": command}, is_error=error, output=output, exit_code=exit_code,
    )


def _ready(controller: ValidationController) -> None:
    _observe(controller, "repro", "pytest tests/test_target.py", error=True,
             output="FAILED tests/test_target.py::test_target", exit_code=1)
    assert controller.declare("record_reproduction", {
        "evidence_reference": "repro", "observed_failure": True,
    }, agent_id="agent").allowed
    assert controller.declare("declare_contract_inventory", {"inventory": _inventory()}, agent_id="agent").allowed
    _observe(controller, "baseline", "pytest tests/test_command.py", error=False, output="2 passed", exit_code=0)


def test_disabled_controller_has_no_obligations(tmp_path: Path) -> None:
    controller = ValidationController(state_dir=tmp_path / "unused")
    decision = controller.assess_tool(
        agent_id="a", parent_agent_id=None, workspace_id="w", tool_call_id="write",
        tool_name="EditFile", tool_category="write", tool_module="codepacex.tools.edit_file", arguments={},
    )
    assert decision.allowed
    assert decision.operation is OperationClass.IMPLEMENTATION_WRITE
    assert not (tmp_path / "unused").exists()


def test_reproduction_and_inventory_gate_writes(tmp_path: Path) -> None:
    controller = ValidationController(ValidationProfile.stage_b(), session_id="one", state_dir=tmp_path)
    before = controller.assess_tool(
        agent_id="parent", parent_agent_id=None, workspace_id="w", tool_call_id="write-1",
        tool_name="EditFile", tool_category="write", tool_module="codepacex.tools.edit_file", arguments={},
    )
    assert not before.allowed and "reproduction" in before.reason
    _observe(controller, "repro", "pytest tests/test_target.py", error=True, output="FAILED target", exit_code=1)
    assert controller.declare("record_reproduction", {"evidence_reference": "repro", "observed_failure": True}, agent_id="parent").allowed
    no_inventory = controller.assess_tool(
        agent_id="child", parent_agent_id="parent", workspace_id="w", tool_call_id="write-2",
        tool_name="EditFile", tool_category="write", tool_module="codepacex.tools.edit_file", arguments={},
    )
    assert not no_inventory.allowed and "inventory" in no_inventory.reason
    assert controller.declare("declare_contract_inventory", {"inventory": _inventory()}, agent_id="parent").allowed
    allowed = controller.assess_tool(
        agent_id="child", parent_agent_id="parent", workspace_id="w", tool_call_id="write-3",
        tool_name="EditFile", tool_category="write", tool_module="codepacex.tools.edit_file", arguments={},
    )
    assert allowed.allowed


def test_target_and_regression_gate_require_post_edit_evidence(tmp_path: Path) -> None:
    controller = ValidationController(ValidationProfile.stage_b(), state_dir=tmp_path)
    _ready(controller)
    controller.observe_tool_result(
        agent_id="agent", parent_agent_id=None, workspace_id="w", tool_call_id="edit",
        tool_name="EditFile", tool_category="write", tool_module="codepacex.tools.edit_file",
        arguments={"file_path": "src/command.py"}, is_error=False, output="edited",
    )
    decision = controller.assess_completion(agent_id="agent")
    assert not decision.allowed
    assert any("target test" in item for item in decision.blockers)
    _observe(controller, "target", "pytest tests/test_target.py", error=False, output="1 passed", exit_code=0)
    _observe(controller, "post", "pytest tests/test_command.py", error=False, output="2 passed", exit_code=0)
    assert controller.assess_completion(agent_id="agent").status is CompletionStatus.VALIDATED_COMPLETE


def test_new_regression_is_not_hidden_by_target_test(tmp_path: Path) -> None:
    controller = ValidationController(ValidationProfile.stage_b(), state_dir=tmp_path)
    _ready(controller)
    controller.observe_tool_result(
        agent_id="agent", parent_agent_id=None, workspace_id="w", tool_call_id="edit",
        tool_name="EditFile", tool_category="write", tool_module="", arguments={}, is_error=False, output="edited",
    )
    _observe(controller, "target", "pytest tests/test_target.py", error=False, output="1 passed", exit_code=0)
    _observe(controller, "post", "pytest tests/test_command.py", error=True,
             output="FAILED tests/test_command.py::test_p2p", exit_code=1)
    decision = controller.assess_completion(agent_id="agent")
    assert not decision.allowed
    assert any("new regression" in item for item in decision.blockers)


def test_shared_request_checkpoints_are_stateful_and_structured(tmp_path: Path) -> None:
    controller = ValidationController(ValidationProfile.stage_b(), state_dir=tmp_path)
    for _ in range(20):
        controller.observe_request_completed(agent_id="parent")
    assert controller.summary()["pending_checkpoints"] == [20]
    assert not controller.declare("ack_request_checkpoint", {"ordinal": 20, "summary": "x", "details": {}}, agent_id="child").allowed
    assert controller.declare("ack_request_checkpoint", {
        "ordinal": 20, "summary": "reconciled", "details": {
            "reproduction_status": "missing", "root_cause_hypothesis": "unknown",
            "inventory_revision": 0, "target_tests_registered": 0, "target_tests_executed": 0,
        },
    }, agent_id="child").allowed
    for _ in range(16):
        controller.observe_request_completed(agent_id="child")
    assert controller.summary()["pending_checkpoints"] == [30, 36]


def test_state_recovers_across_controller_instances(tmp_path: Path) -> None:
    first = ValidationController(ValidationProfile.stage_b(), session_id="shared", state_dir=tmp_path)
    _observe(first, "repro", "pytest tests/test_target.py", error=True, output="FAILED target", exit_code=1)
    second = ValidationController(ValidationProfile.stage_b(), session_id="shared", state_dir=tmp_path)
    assert second.declare("record_reproduction", {"evidence_reference": "repro", "observed_failure": True}, agent_id="child").allowed
    assert second.summary()["reproduction"]["evidence_reference"] == "repro"

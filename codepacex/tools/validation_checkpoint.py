"""Structured, non-side-effecting declarations for Stage B validation."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from codepacex.tools.base import Tool, ToolResult
from codepacex.validation import ValidationController


class ValidationCheckpointParams(BaseModel):
    action: Literal[
        "record_reproduction", "record_reproduction_exception", "declare_contract_inventory",
        "amend_contract_inventory", "declare_target_tests", "declare_regression_slice",
        "ack_request_checkpoint", "select_request_36_outcome",
    ]
    observed_tool_call_id: str | None = Field(default=None, description="Actual prior tool_call_id, or omit with use_recent_observed_result=true")
    use_recent_observed_result: bool = Field(default=False, description="Bind the most recent actual tool result; never invent an id")
    reproduction_status: Literal["observed_failure", "exception"] | None = None
    reproduction_exception_reason: Literal["environment_unavailable", "dependency_unavailable", "network_required", "test_not_runnable", "issue_not_reproducible", "external_service_unavailable", "platform_mismatch"] | None = None
    attempted_commands: str | None = None
    observed_results: str | None = None
    explanation: str | None = None
    remaining_uncertainty: str | None = None
    contract_inventory: dict[str, Any] | None = Field(default=None, description="All inventory fields: target_behavior, failure_assertions, touched_symbols, direct_callers, implementations, config_surfaces, default_values, serialization_surfaces, fixtures, target_tests, regression_tests, known_unknowns")
    target_tests: list[dict[str, Any]] | None = None
    regression_tests: list[dict[str, Any]] | None = None
    checkpoint_ordinal: int | None = None
    checkpoint_summary: str | None = None
    checkpoint_details: dict[str, Any] | None = None
    checkpoint_decision: Literal["FINALIZE_VALIDATED_PATCH", "ROLLBACK_TO_COHERENT_PATCH", "DECLARE_UNRESOLVED"] | None = None


class ValidationCheckpoint(Tool):
    name = "ValidationCheckpoint"
    description = "Record structured Stage B validation evidence or checkpoint acknowledgements."
    params_model = ValidationCheckpointParams
    category = "read"
    is_system_tool = True

    def __init__(self, controller: ValidationController, agent_id: str, parent_agent_id: str | None = None) -> None:
        self._controller = controller
        self._agent_id = agent_id
        self._parent_agent_id = parent_agent_id

    async def execute(self, params: ValidationCheckpointParams) -> ToolResult:
        reference = params.observed_tool_call_id
        if params.use_recent_observed_result:
            reference = self._controller.latest_observed_tool_call_id()
        payload: dict[str, Any] = {}
        if params.action == "record_reproduction":
            payload = {"evidence_reference": reference, "observed_failure": params.reproduction_status == "observed_failure"}
        elif params.action == "record_reproduction_exception":
            payload = {"reason_code": params.reproduction_exception_reason, "attempted_commands": params.attempted_commands, "observed_results": params.observed_results, "explanation": params.explanation, "remaining_uncertainty": params.remaining_uncertainty}
        elif params.action in {"declare_contract_inventory", "amend_contract_inventory"}:
            payload = {"inventory": params.contract_inventory}
        elif params.action == "declare_target_tests":
            payload = {"tests": params.target_tests}
        elif params.action == "declare_regression_slice":
            payload = {"tests": params.regression_tests}
        elif params.action == "ack_request_checkpoint":
            payload = {"ordinal": params.checkpoint_ordinal, "summary": params.checkpoint_summary, "details": params.checkpoint_details}
        elif params.action == "select_request_36_outcome":
            payload = {"choice": params.checkpoint_decision}
        decision = self._controller.declare(
            params.action, payload, agent_id=self._agent_id, parent_agent_id=self._parent_agent_id,
        )
        if not decision.allowed:
            return ToolResult(output=json.dumps(self._controller.checkpoint_error(params.action, decision.reason), sort_keys=True), is_error=True)
        return ToolResult(output=json.dumps({"status": "recorded", "action": params.action, **self._controller.checkpoint_remediation()}, sort_keys=True))

"""Structured, non-side-effecting declarations for Stage B validation."""

from __future__ import annotations

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
    payload: dict[str, Any] = Field(default_factory=dict)


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
        decision = self._controller.declare(
            params.action, params.payload, agent_id=self._agent_id, parent_agent_id=self._parent_agent_id,
        )
        if not decision.allowed:
            return ToolResult(output=f"Validation checkpoint rejected: {decision.reason}", is_error=True)
        return ToolResult(output=f"Validation checkpoint recorded: {params.action}")

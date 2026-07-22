"""Structured, non-side-effecting declarations for Stage B validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from codepacex.tools.base import Tool, ToolResult
from codepacex.validation import ValidationController


@dataclass(frozen=True)
class ContainerNormalization:
    value: dict[str, Any] | list[dict[str, Any]] | None
    telemetry: dict[str, Any]


def _normalize_container(value: Any, *, field: str, expected_type: type[dict] | type[list]) -> ContainerNormalization:
    """Accept exactly one JSON encoding for a structured tool field.

    The Provider may serialize a JSON object/array as a JSON string.  We repair
    only that unambiguous transport representation; natural-language strings
    and double encodings remain invalid inputs.
    """
    original_type = type(value).__name__
    normalized = False
    try:
        if isinstance(value, str):
            value = json.loads(value)
            normalized = True
        if not isinstance(value, expected_type):
            raise ValueError(f"{field} must be a JSON {expected_type.__name__}")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return ContainerNormalization(
            value=None,
            telemetry={
                "field": field,
                "original_type": original_type,
                "normalized": normalized,
                "normalization_success": False,
                "validation_result": "rejected",
                "reason": str(exc),
            },
        )
    return ContainerNormalization(
        value=value,
        telemetry={
            "field": field,
            "original_type": original_type,
            "normalized": normalized,
            "normalization_success": True,
            "validation_result": "pending",
        },
    )


class ValidationCheckpointParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
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
    contract_inventory: dict[str, Any] | str | None = Field(default=None, description="All inventory fields: target_behavior, failure_assertions, touched_symbols, direct_callers, implementations, config_surfaces, default_values, serialization_surfaces, fixtures, target_tests, regression_tests, known_unknowns. A single JSON object encoding is accepted for Provider transport compatibility.")
    target_tests: list[dict[str, Any]] | str | None = None
    regression_tests: list[dict[str, Any]] | str | None = None
    checkpoint_ordinal: int | None = None
    checkpoint_summary: str | None = None
    checkpoint_details: dict[str, Any] | str | None = None
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

    def _container_payload(
        self, params: ValidationCheckpointParams
    ) -> tuple[dict[str, Any] | None, ContainerNormalization | None]:
        action = params.action
        field: str | None = None
        expected: type[dict] | type[list] | None = None
        if action in {"declare_contract_inventory", "amend_contract_inventory"}:
            field, expected = "contract_inventory", dict
        elif action == "declare_target_tests":
            field, expected = "target_tests", list
        elif action == "declare_regression_slice":
            field, expected = "regression_tests", list
        elif action == "ack_request_checkpoint":
            field, expected = "checkpoint_details", dict
        if field is None or expected is None:
            return {}, None
        normalized = _normalize_container(getattr(params, field), field=field, expected_type=expected)
        if normalized.value is None:
            return None, normalized
        return {field: normalized.value}, normalized

    async def execute(self, params: ValidationCheckpointParams) -> ToolResult:
        reference = params.observed_tool_call_id
        if params.use_recent_observed_result:
            reference = self._controller.latest_observed_tool_call_id()
        containers, normalization = self._container_payload(params)
        if normalization is not None and containers is None:
            self._controller.record_payload_normalization(
                agent_id=self._agent_id, parent_agent_id=self._parent_agent_id,
                telemetry=normalization.telemetry,
            )
            return ToolResult(
                output=json.dumps(
                    {
                        "error_code": "validation_checkpoint_rejected",
                        "message": normalization.telemetry["reason"],
                        "normalization": normalization.telemetry,
                        **self._controller.checkpoint_remediation(),
                    },
                    sort_keys=True,
                ),
                is_error=True,
            )
        payload: dict[str, Any] = containers or {}
        if params.action == "record_reproduction":
            payload = {"evidence_reference": reference, "observed_failure": params.reproduction_status == "observed_failure"}
        elif params.action == "record_reproduction_exception":
            payload = {"reason_code": params.reproduction_exception_reason, "attempted_commands": params.attempted_commands, "observed_results": params.observed_results, "explanation": params.explanation, "remaining_uncertainty": params.remaining_uncertainty}
        elif params.action in {"declare_contract_inventory", "amend_contract_inventory"}:
            payload = {"inventory": payload["contract_inventory"]}
        elif params.action == "declare_target_tests":
            payload = {"tests": payload["target_tests"]}
        elif params.action == "declare_regression_slice":
            payload = {"tests": payload["regression_tests"]}
        elif params.action == "ack_request_checkpoint":
            payload = {"ordinal": params.checkpoint_ordinal, "summary": params.checkpoint_summary, "details": payload["checkpoint_details"]}
        elif params.action == "select_request_36_outcome":
            payload = {"choice": params.checkpoint_decision}
        decision = self._controller.declare(
            params.action, payload, agent_id=self._agent_id, parent_agent_id=self._parent_agent_id,
        )
        if normalization is not None:
            telemetry = {
                **normalization.telemetry,
                "validation_result": "accepted" if decision.allowed else "rejected",
            }
            self._controller.record_payload_normalization(
                agent_id=self._agent_id, parent_agent_id=self._parent_agent_id, telemetry=telemetry,
            )
        if not decision.allowed:
            error = self._controller.checkpoint_error(params.action, decision.reason)
            if normalization is not None:
                error["normalization"] = telemetry
            return ToolResult(output=json.dumps(error, sort_keys=True), is_error=True)
        return ToolResult(output=json.dumps({"status": "recorded", "action": params.action, **self._controller.checkpoint_remediation()}, sort_keys=True))

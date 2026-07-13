from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from codepacex.experiments import (
    AgentMode,
    CompressionProfile,
    ExperimentProfile,
    PermissionStrategy,
    ToolLoading,
    combined_runtime_hash,
)


def _profile(**overrides: object) -> ExperimentProfile:
    values = {
        "tool_loading": "deferred",
        "compression_profile": "recovery_v1",
        "permission_strategy": "default",
        "agent_mode": "single",
        **overrides,
    }
    return ExperimentProfile.model_validate(values)


def test_profile_is_closed_and_typed() -> None:
    profile = _profile()
    assert profile.tool_loading is ToolLoading.DEFERRED
    assert profile.compression_profile is CompressionProfile.RECOVERY_V1
    assert profile.permission_strategy is PermissionStrategy.DEFAULT
    assert profile.agent_mode is AgentMode.SINGLE

    with pytest.raises(ValidationError):
        _profile(tool_loading="invented")
    with pytest.raises(ValidationError):
        ExperimentProfile.model_validate({**profile.model_dump(), "extra": True})


def test_effective_runtime_maps_every_profile_field() -> None:
    eager = _profile(
        tool_loading="eager",
        compression_profile="summary_only",
        permission_strategy="sandbox_auto_allow",
        agent_mode="multi",
    )
    assert eager.effective_runtime() == {
        "schema_version": 1,
        "tool_loading": "eager",
        "defer_mcp_tools": False,
        "compression_profile": "summary_only",
        "recovery_attachments_enabled": False,
        "permission_strategy": "sandbox_auto_allow",
        "agent_mode": "multi",
        "multi_agent_tools_enabled": True,
    }


def test_hashes_are_canonical_and_bind_actual_runtime_payload() -> None:
    first = _profile()
    second = ExperimentProfile.model_validate(
        json.loads(json.dumps(first.model_dump(mode="json"), sort_keys=False))
    )
    assert first.profile_hash() == second.profile_hash()
    assert first.runtime_contract_hash() == second.runtime_contract_hash()

    baseline = combined_runtime_hash(
        profile_hash=first.profile_hash(), system_sha256="system", tools_sha256="tools"
    )
    assert baseline != combined_runtime_hash(
        profile_hash=_profile(tool_loading="eager").profile_hash(),
        system_sha256="system",
        tools_sha256="tools",
    )

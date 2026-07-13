"""Typed, reproducible runtime profiles used by non-interactive benchmarks.

Experiment profiles are deliberately separate from normal user configuration.
They are accepted only by the ``-p`` benchmark path and describe runtime
behaviour that can be verified from emitted telemetry.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict


class ToolLoading(str, Enum):
    EAGER = "eager"
    DEFERRED = "deferred"


class CompressionProfile(str, Enum):
    SUMMARY_ONLY = "summary_only"
    RECOVERY_V1 = "recovery_v1"


class PermissionStrategy(str, Enum):
    DEFAULT = "default"
    SESSION_ALLOW = "session_allow"
    EXPLICIT_RULES = "explicit_rules"
    SANDBOX_AUTO_ALLOW = "sandbox_auto_allow"


class AgentMode(str, Enum):
    SINGLE = "single"
    MULTI = "multi"


class ExperimentProfile(BaseModel):
    """Closed set of benchmark-controlled runtime capabilities."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    tool_loading: ToolLoading
    compression_profile: CompressionProfile
    permission_strategy: PermissionStrategy
    agent_mode: AgentMode

    def canonical_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def profile_hash(self) -> str:
        return canonical_hash(self.canonical_payload())

    def effective_runtime(self) -> dict[str, Any]:
        """Return the concrete switches applied by the runtime assembler."""
        return {
            "schema_version": self.schema_version,
            "tool_loading": self.tool_loading.value,
            "defer_mcp_tools": self.tool_loading is ToolLoading.DEFERRED,
            "compression_profile": self.compression_profile.value,
            "recovery_attachments_enabled": (
                self.compression_profile is CompressionProfile.RECOVERY_V1
            ),
            "permission_strategy": self.permission_strategy.value,
            "agent_mode": self.agent_mode.value,
            "multi_agent_tools_enabled": self.agent_mode is AgentMode.MULTI,
        }

    def runtime_contract_hash(self) -> str:
        return canonical_hash(self.effective_runtime())


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def combined_runtime_hash(
    *, profile_hash: str, system_sha256: str, tools_sha256: str
) -> str:
    """Bind the selected profile to the actual provider request payload."""
    return canonical_hash(
        {
            "experiment_profile_hash": profile_hash,
            "system_sha256": system_sha256,
            "tools_sha256": tools_sha256,
        }
    )


def load_experiment_profile(path: Path) -> ExperimentProfile:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("experiment profile must be a mapping")
    return ExperimentProfile.model_validate(raw)

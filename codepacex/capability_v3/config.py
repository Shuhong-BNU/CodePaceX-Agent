"""Frozen, opt-in configuration for Capability V3."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


FEATURE_FLAG_KEY = "capability_v3_flag"


class CapabilityV3Flag(str, Enum):
    V2_CONTROL = "V2_CONTROL"
    V3_A_ONLY = "V3_A_ONLY"
    V3_B_ONLY = "V3_B_ONLY"
    V3_C_ONLY = "V3_C_ONLY"
    V3_D_ONLY = "V3_D_ONLY"
    V3_CORE = "V3_CORE"
    V3_CORE_NO_HYP = "V3_CORE_NO_HYP"
    V3_CORE_NO_MATRIX = "V3_CORE_NO_MATRIX"


@dataclass(frozen=True)
class CapabilityV3Config:
    enabled: bool = False
    contract_recovery_enabled: bool = True
    bounded_hypotheses_enabled: bool = True
    impact_slice_enabled: bool = True
    contract_matrix_enabled: bool = True
    reversible_reproducer_enabled: bool = True
    oracle_guard_enabled: bool = True
    candidate_snapshot_enabled: bool = True
    budget_finalization_enabled: bool = True
    differential_validation_enabled: bool = True
    max_hypotheses: int = 3
    max_contract_dimensions: int = 6
    max_matrix_cases: int = 12
    max_selected_tests: int = 12
    finalization_reserve_fraction: float = 0.20
    finalization_reserve_min_requests: int = 6
    restore_floor_requests: int = 3
    full_suite_fallback_enabled: bool = False
    risk_triggered_baseline_enabled: bool = True
    fail_open_on_internal_error: bool = True

    def __post_init__(self) -> None:
        if not 1 <= self.max_hypotheses <= 3:
            raise ValueError("max_hypotheses must be in [1, 3]")
        if not 1 <= self.max_contract_dimensions <= 6:
            raise ValueError("max_contract_dimensions must be in [1, 6]")
        if not 1 <= self.max_matrix_cases <= 16:
            raise ValueError("max_matrix_cases must be in [1, 16]")
        if self.max_selected_tests < 1:
            raise ValueError("max_selected_tests must be positive")
        if not 0 < self.finalization_reserve_fraction <= 1:
            raise ValueError("finalization_reserve_fraction must be in (0, 1]")
        if self.finalization_reserve_min_requests < 1:
            raise ValueError("finalization_reserve_min_requests must be positive")
        if self.restore_floor_requests < 0:
            raise ValueError("restore_floor_requests cannot be negative")

    @classmethod
    def from_flag(cls, flag: CapabilityV3Flag | str) -> "CapabilityV3Config":
        flag = CapabilityV3Flag(flag)
        if flag is CapabilityV3Flag.V2_CONTROL:
            return cls(enabled=False)
        common = dict(enabled=True)
        if flag is CapabilityV3Flag.V3_A_ONLY:
            return cls(**common, impact_slice_enabled=False, contract_matrix_enabled=False,
                       reversible_reproducer_enabled=False, candidate_snapshot_enabled=False,
                       budget_finalization_enabled=False, differential_validation_enabled=False)
        if flag is CapabilityV3Flag.V3_B_ONLY:
            return cls(**common, contract_recovery_enabled=False, bounded_hypotheses_enabled=False,
                       oracle_guard_enabled=False, candidate_snapshot_enabled=False,
                       budget_finalization_enabled=False, differential_validation_enabled=False)
        if flag is CapabilityV3Flag.V3_C_ONLY:
            return cls(**common, contract_recovery_enabled=False, bounded_hypotheses_enabled=False,
                       impact_slice_enabled=False, contract_matrix_enabled=False,
                       reversible_reproducer_enabled=False, oracle_guard_enabled=False,
                       differential_validation_enabled=False)
        if flag is CapabilityV3Flag.V3_D_ONLY:
            return cls(**common, contract_recovery_enabled=False, bounded_hypotheses_enabled=False,
                       impact_slice_enabled=False, contract_matrix_enabled=False,
                       reversible_reproducer_enabled=False, oracle_guard_enabled=False,
                       candidate_snapshot_enabled=False, budget_finalization_enabled=False)
        if flag is CapabilityV3Flag.V3_CORE_NO_HYP:
            return cls(**common, bounded_hypotheses_enabled=False)
        if flag is CapabilityV3Flag.V3_CORE_NO_MATRIX:
            return cls(**common, contract_matrix_enabled=False)
        return cls(**common)


def flag_from_feature_flags(feature_flags: Mapping[str, Any] | None) -> CapabilityV3Flag:
    """Resolve the sole V3 evaluation feature flag, defaulting to the V2 control."""
    value = (feature_flags or {}).get(FEATURE_FLAG_KEY, CapabilityV3Flag.V2_CONTROL.value)
    return CapabilityV3Flag(str(value))

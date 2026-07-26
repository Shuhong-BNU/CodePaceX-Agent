"""Immutable V3 evidence, candidate, budget, and differential models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class CandidateLevel(str, Enum):
    C0 = "C0"
    C1 = "C1"
    C2 = "C2"
    C3 = "C3"
    C4 = "C4"


class ReproducerStatus(str, Enum):
    PRE_FAIL_POST_PASS = "pre_fail_post_pass"
    PRE_PASS_POST_PASS = "pre_pass_post_pass"
    PRE_FAIL_POST_FAIL = "pre_fail_post_fail"
    UNAVAILABLE = "unavailable"
    INVALID_ORACLE = "invalid_oracle"


@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: str
    evidence_type: str
    path: str | None
    symbol: str | None
    line_start: int | None
    line_end: int | None
    commit: str
    excerpt_hash: str
    summary: str
    confidence: str = "medium"


@dataclass(frozen=True)
class EvidenceConflict:
    conflict_id: str
    evidence_ids: tuple[str, ...]
    description: str


@dataclass(frozen=True)
class ContractEvidencePacket:
    packet_id: str
    issue_entities: tuple[str, ...] = ()
    expected_behaviors: tuple[EvidenceRef, ...] = ()
    target_symbols: tuple[EvidenceRef, ...] = ()
    callers: tuple[EvidenceRef, ...] = ()
    implementations: tuple[EvidenceRef, ...] = ()
    tests_and_fixtures: tuple[EvidenceRef, ...] = ()
    defaults_and_config: tuple[EvidenceRef, ...] = ()
    serialization_and_output: tuple[EvidenceRef, ...] = ()
    history: tuple[EvidenceRef, ...] = ()
    conflicts: tuple[EvidenceConflict, ...] = ()
    unknowns: tuple[str, ...] = ()


@dataclass(frozen=True)
class HypothesisRecord:
    hypothesis_id: str
    claim: str
    supporting_evidence_ids: tuple[str, ...]
    contradicting_evidence_ids: tuple[str, ...]
    observable_prediction: str
    cheapest_falsifier: str
    status: str = "live"
    created_request: int = 0
    updated_request: int = 0


@dataclass(frozen=True)
class OracleRisk:
    risk_type: str
    level: str
    message: str


@dataclass(frozen=True)
class TestRecommendation:
    test: str
    score: int
    reasons: tuple[str, ...] = ()
    mandatory: bool = False


@dataclass(frozen=True)
class ImpactSlice:
    diff_sha: str
    changed_files: tuple[str, ...] = ()
    changed_symbols: tuple[str, ...] = ()
    reverse_callers: tuple[str, ...] = ()
    implementations: tuple[str, ...] = ()
    config_readers: tuple[str, ...] = ()
    serializers: tuple[str, ...] = ()
    impacted_tests: tuple[TestRecommendation, ...] = ()
    unknown_edges: tuple[str, ...] = ()
    fallback_reason: str | None = None


@dataclass(frozen=True)
class ContractDimension:
    name: str
    values: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    risk: str = "medium"


@dataclass(frozen=True)
class ContractDimensionMatrix:
    dimensions: tuple[ContractDimension, ...] = ()
    selected_cases: tuple[dict[str, str], ...] = ()
    generation_method: str = "explicit"
    uncovered_dimensions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReproducerEvidence:
    reproducer_id: str
    source: str
    command: tuple[str, ...]
    temporary_paths: tuple[str, ...]
    pre_patch_outcome: str
    post_patch_outcome: str | None
    oracle_evidence_ids: tuple[str, ...]
    reversible_status: ReproducerStatus


@dataclass(frozen=True)
class CandidateSnapshot:
    candidate_id: str
    level: CandidateLevel
    base_commit: str
    diff_sha: str
    patch_path: str
    created_request: int
    changed_files: tuple[str, ...]
    impact_slice_id: str | None = None
    evidence_packet_id: str | None = None
    test_evidence_ids: tuple[str, ...] = ()
    known_failures: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()
    oracle_risks: tuple[str, ...] = ()
    restorable: bool = True


@dataclass(frozen=True)
class BudgetState:
    request_limit: int = 40
    requests_used: int = 0
    requests_remaining: int = 40
    phase: str = "explore"
    finalization_reserve: int = 8
    restore_floor: int = 3
    current_candidate_id: str | None = None
    wip_since_candidate: bool = False
    phase_reason: str = "initialized"


@dataclass(frozen=True)
class ComparableRunIdentity:
    execution_commit: str
    environment_fingerprint: str
    dependency_fingerprint: str
    interpreter: str
    command: tuple[str, ...]
    test_slice_id: str
    network_mode: str
    timeout_seconds: int


@dataclass(frozen=True)
class FailureRecord:
    failure_id: str
    test_nodeid: str | None
    category: str
    normalized_signature: str


@dataclass(frozen=True)
class FailureAttribution:
    resolved_failures: tuple[str, ...] = ()
    new_regression_candidates: tuple[str, ...] = ()
    baseline_existing: tuple[str, ...] = ()
    persistent_failures: tuple[str, ...] = ()
    flaky_suspects: tuple[str, ...] = ()
    environment_errors: tuple[str, ...] = ()
    incomparable: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()


def jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {key: jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, tuple):
        return [jsonable(item) for item in value]
    if isinstance(value, list):
        return [jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    return value

"""Deterministic, fail-open Capability V3 state controller.

This module deliberately has no client, permission, or evaluator dependency.
It can therefore be replayed in zero-provider tests and cannot deny a tool.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any, Iterable

from .config import CapabilityV3Config
from .models import (
    BudgetState, CandidateLevel, CandidateSnapshot, ComparableRunIdentity,
    ContractDimension, ContractDimensionMatrix, ContractEvidencePacket,
    EvidenceConflict, EvidenceRef, FailureAttribution, FailureRecord,
    HypothesisRecord, ImpactSlice, OracleRisk, ReproducerEvidence,
    ReproducerStatus, TestRecommendation, jsonable,
)

SCHEMA_VERSION = 1
_WORDS = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


@dataclass(frozen=True)
class V3Advice:
    level: str = "none"
    messages: tuple[str, ...] = ()
    suggested_actions: tuple[str, ...] = ()
    recommended_tests: tuple[str, ...] = ()
    finalization_required: bool = False


class CapabilityV3Controller:
    """Append-only V3 state, intentionally advisory and provider-free."""

    def __init__(self, config: CapabilityV3Config | None = None, *, task_id: str = "",
                 base_commit: str = "", state_dir: Path | None = None,
                 request_limit: int = 40) -> None:
        self.config = config or CapabilityV3Config()
        self.task_id, self.base_commit, self.state_dir = task_id, base_commit, state_dir
        reserve = max(self.config.finalization_reserve_min_requests,
                      round(request_limit * self.config.finalization_reserve_fraction))
        if self.config.restore_floor_requests >= reserve:
            raise ValueError("restore_floor_requests must be below finalization reserve")
        self.budget = BudgetState(request_limit=request_limit, requests_remaining=request_limit,
                                  finalization_reserve=reserve,
                                  restore_floor=self.config.restore_floor_requests)
        self.events: list[dict[str, Any]] = []
        self.evidence: ContractEvidencePacket | None = None
        self.hypotheses: list[HypothesisRecord] = []
        self.oracle_risks: list[OracleRisk] = []
        self.impact: ImpactSlice | None = None
        self.matrix: ContractDimensionMatrix | None = None
        self.reproducer: ReproducerEvidence | None = None
        self.candidates: list[CandidateSnapshot] = []
        self.differential: FailureAttribution | None = None
        self.internal_errors: list[str] = []
        if self.config.enabled:
            self._emit("V3Initialized", config=jsonable(self.config), task_id=task_id,
                       base_commit=base_commit)

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def begin_run(self, *, task_id: str, base_commit: str, feature_flag: str) -> None:
        """Bind the advisory state to the concrete run before collecting evidence."""
        if not self.enabled:
            return
        self.task_id = task_id
        self.base_commit = base_commit
        self._emit("V3RunConfigured", task_id=task_id, base_commit=base_commit,
                   feature_flag=feature_flag, config=self.config)

    def _emit(self, event_type: str, **payload: Any) -> None:
        self.events.append({"schema_version": SCHEMA_VERSION, "sequence": len(self.events) + 1,
                            "event_type": event_type, "payload": jsonable(payload)})

    def _fail_open(self, operation: str, exc: Exception) -> None:
        message = f"{operation}: {type(exc).__name__}: {exc}"
        self.internal_errors.append(message)
        self._emit("V3InternalError", operation=operation, error=message)

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()

    def _ref(self, *, kind: str, path: Path | None, symbol: str | None, text: str,
             commit: str, line: int | None = None, confidence: str = "medium") -> EvidenceRef:
        digest = self._hash(text)
        return EvidenceRef(f"ev-{digest[:16]}", kind, str(path) if path else None, symbol,
                           line, line, commit, digest, text[:300], confidence)

    def collect_evidence(self, issue: str, repository: Path,
                         target_symbols: Iterable[str] = ()) -> ContractEvidencePacket | None:
        """Read only repository evidence, bounded by category and file size."""
        if not (self.enabled and self.config.contract_recovery_enabled):
            return None
        try:
            symbols = tuple(dict.fromkeys([*target_symbols, *_WORDS.findall(issue)]))[:12]
            issue_ref = self._ref(kind="issue_statement", path=None, symbol=None, text=issue,
                                  commit=self.base_commit, confidence="high")
            buckets: dict[str, list[EvidenceRef]] = {name: [] for name in (
                "target", "callers", "implementations", "tests", "defaults", "serialization")}
            for path in sorted(repository.rglob("*.py"))[:800]:
                if any(part.startswith(".") for part in path.relative_to(repository).parts):
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                    tree = ast.parse(text)
                except (OSError, SyntaxError):
                    continue
                lines = text.splitlines()
                names = {node.name: node for node in ast.walk(tree)
                         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
                for symbol in symbols:
                    if symbol in names and len(buckets["target"]) < 8:
                        node = names[symbol]
                        buckets["target"].append(self._ref(kind="implementation", path=path, symbol=symbol,
                            text=lines[node.lineno - 1], commit=self.base_commit, line=node.lineno, confidence="high"))
                    if symbol in text and symbol not in names and len(buckets["callers"]) < 12:
                        line_no = next((i + 1 for i, line in enumerate(lines) if symbol in line), 1)
                        buckets["callers"].append(self._ref(kind="direct_caller", path=path, symbol=symbol,
                            text=lines[line_no - 1], commit=self.base_commit, line=line_no))
                lower = str(path).lower()
                if ("test" in lower or "fixture" in lower) and any(symbol in text for symbol in symbols) and len(buckets["tests"]) < 12:
                    buckets["tests"].append(self._ref(kind="test", path=path, symbol=None, text=lines[0] if lines else "",
                        commit=self.base_commit, line=1))
                if any(word in text.lower() for word in ("default", "config", "setting")) and len(buckets["defaults"]) < 8:
                    buckets["defaults"].append(self._ref(kind="default_or_config", path=path, symbol=None,
                        text=next((line for line in lines if "default" in line.lower() or "config" in line.lower()), ""),
                        commit=self.base_commit))
                if any(word in text.lower() for word in ("json", "serializ", "format", "output")) and len(buckets["serialization"]) < 8:
                    buckets["serialization"].append(self._ref(kind="serialization_or_output", path=path, symbol=None,
                        text=next((line for line in lines if any(word in line.lower() for word in ("json", "serializ", "format", "output"))), ""),
                        commit=self.base_commit))
            caller_ids = {item.evidence_id for item in buckets["callers"]}
            implementation_ids = {item.evidence_id for item in buckets["target"]}
            conflict = ()
            if caller_ids and implementation_ids:
                conflict = (EvidenceConflict("conflict-caller-implementation",
                    tuple(sorted(caller_ids | implementation_ids)), "caller and implementation evidence both retained"),)
            self.evidence = ContractEvidencePacket(
                packet_id=f"packet-{self._hash(issue)[:16]}", issue_entities=symbols,
                expected_behaviors=(issue_ref,), target_symbols=tuple(buckets["target"]),
                callers=tuple(buckets["callers"]), implementations=tuple(buckets["target"]),
                tests_and_fixtures=tuple(buckets["tests"]), defaults_and_config=tuple(buckets["defaults"]),
                serialization_and_output=tuple(buckets["serialization"]), conflicts=conflict,
                unknowns=tuple() if buckets["target"] else ("no target-symbol anchor found",),
            )
            self._emit("EvidenceCollected", packet=self.evidence)
            for item in conflict:
                self._emit("EvidenceConflictDetected", conflict=item)
            return self.evidence
        except Exception as exc:
            self._fail_open("collect_evidence", exc)
            return None

    def record_hypotheses(self, records: Iterable[HypothesisRecord]) -> tuple[HypothesisRecord, ...]:
        if not (self.enabled and self.config.bounded_hypotheses_enabled):
            return tuple(self.hypotheses)
        try:
            existing_predictions = {item.observable_prediction for item in self.hypotheses}
            for record in records:
                if len(self.hypotheses) >= self.config.max_hypotheses:
                    break
                if record.observable_prediction in existing_predictions:
                    continue
                self.hypotheses.append(record)
                existing_predictions.add(record.observable_prediction)
                self._emit("HypothesisProposed", hypothesis=record)
            return tuple(self.hypotheses)
        except Exception as exc:
            self._fail_open("record_hypotheses", exc)
            return tuple(self.hypotheses)

    def reject_hypothesis(self, hypothesis_id: str, *, tool_evidence_id: str) -> bool:
        """Only a concrete tool evidence id can reject an otherwise live hypothesis."""
        if not tool_evidence_id:
            return False
        for index, record in enumerate(self.hypotheses):
            if record.hypothesis_id == hypothesis_id:
                updated = HypothesisRecord(**{**record.__dict__, "status": "rejected",
                                              "contradicting_evidence_ids": tuple(sorted(set(record.contradicting_evidence_ids + (tool_evidence_id,))))})
                self.hypotheses[index] = updated
                self._emit("HypothesisRejected", hypothesis=updated, tool_evidence_id=tool_evidence_id)
                return True
        return False

    def inspect_oracle(self, *, changed_files: Iterable[str], diff_text: str = "") -> tuple[OracleRisk, ...]:
        if not (self.enabled and self.config.oracle_guard_enabled):
            return ()
        try:
            source = diff_text.lower()
            risks: list[OracleRisk] = []
            if re.search(r"^\+\s*(def|class)\s+", diff_text, re.M) and not (self.evidence and self.evidence.target_symbols):
                risks.append(OracleRisk("unanchored_public_api", "high", "new public API lacks repository anchor"))
            if any("test" in path.lower() or "golden" in path.lower() for path in changed_files):
                risks.append(OracleRisk("expected_or_fixture_changed", "warning", "changed test or expected fixture requires review"))
            if "except" in source and ("pass" in source or "return none" in source):
                risks.append(OracleRisk("silent_exception", "high", "patch may weaken an exception contract"))
            if "default" in source and not (self.evidence and self.evidence.defaults_and_config):
                risks.append(OracleRisk("default_without_evidence", "warning", "default changed without config evidence"))
            self.oracle_risks.extend(risks)
            for risk in risks:
                self._emit("OracleRiskDetected", risk=risk)
            return tuple(risks)
        except Exception as exc:
            self._fail_open("inspect_oracle", exc)
            return ()

    def build_impact(self, *, diff_sha: str, changed_files: Iterable[str], repository: Path,
                     prior_failing_tests: Iterable[str] = (), f2p_tests: Iterable[str] = ()) -> ImpactSlice | None:
        if not (self.enabled and self.config.impact_slice_enabled):
            return None
        try:
            files = tuple(sorted(set(changed_files)))
            symbols: set[str] = set()
            for name in files:
                path = repository / name
                if path.suffix == ".py" and path.exists():
                    try:
                        symbols.update(node.name for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                                       if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
                    except (OSError, SyntaxError):
                        pass
            recommendations: dict[str, TestRecommendation] = {}
            for test in sorted(set(prior_failing_tests) | set(f2p_tests)):
                recommendations[test] = TestRecommendation(test, 10_000, ("prior_failure_or_f2p",), True)
            for path in sorted(repository.rglob("test*.py"))[:800]:
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                hits = sum(symbol in text for symbol in symbols)
                if hits:
                    relative = str(path.relative_to(repository))
                    recommendations.setdefault(relative, TestRecommendation(relative, hits * 4, ("symbol_reference",)))
            ordered = sorted(recommendations.values(), key=lambda item: (-item.mandatory, -item.score, item.test))
            mandatory = [item for item in ordered if item.mandatory]
            optional = [item for item in ordered if not item.mandatory][:self.config.max_selected_tests]
            self.impact = ImpactSlice(diff_sha=diff_sha, changed_files=files, changed_symbols=tuple(sorted(symbols)),
                impacted_tests=tuple(mandatory + optional), unknown_edges=("dynamic Python edges may be incomplete",),
                fallback_reason=None if symbols else "no parseable changed Python symbols")
            self._emit("ImpactSliceBuilt", impact=self.impact)
            self._emit("TestSliceRecommended", tests=self.impact.impacted_tests)
            return self.impact
        except Exception as exc:
            self._fail_open("build_impact", exc)
            return None

    def build_matrix(self, dimensions: Iterable[ContractDimension]) -> ContractDimensionMatrix | None:
        if not (self.enabled and self.config.contract_matrix_enabled):
            return None
        try:
            valid = [item for item in dimensions if item.evidence_ids][:self.config.max_contract_dimensions]
            uncovered = tuple(item.name for item in dimensions if not item.evidence_ids)
            cases = [dict(zip([item.name for item in valid], values)) for values in product(*(item.values for item in valid))] if valid else []
            self.matrix = ContractDimensionMatrix(tuple(valid), tuple(cases[:self.config.max_matrix_cases]),
                                                  "bounded_cartesian" if len(valid) > 1 else "explicit", uncovered)
            self._emit("ContractMatrixBuilt", matrix=self.matrix)
            return self.matrix
        except Exception as exc:
            self._fail_open("build_matrix", exc)
            return None

    def register_reproducer(self, evidence: ReproducerEvidence) -> None:
        if self.enabled and self.config.reversible_reproducer_enabled:
            self.reproducer = evidence
            self._emit("ReproducerRegistered", reproducer=evidence)

    def observe_diff(self, *, diff_text: str, changed_files: Iterable[str], patch_path: Path | None = None) -> CandidateSnapshot | None:
        if not (self.enabled and self.config.candidate_snapshot_enabled):
            return None
        try:
            diff_sha = self._hash(diff_text)
            self.inspect_oracle(changed_files=changed_files, diff_text=diff_text)
            return self.snapshot_candidate(CandidateLevel.C1, diff_sha=diff_sha, patch_path=patch_path,
                                           changed_files=tuple(changed_files))
        except Exception as exc:
            self._fail_open("observe_diff", exc)
            return None

    def snapshot_candidate(self, level: CandidateLevel, *, diff_sha: str, patch_path: Path | None,
                           changed_files: tuple[str, ...], test_evidence_ids: Iterable[str] = ()) -> CandidateSnapshot | None:
        if not diff_sha or level is CandidateLevel.C0:
            return None
        candidate = CandidateSnapshot(
            candidate_id=f"candidate-{len(self.candidates) + 1:03d}", level=level,
            base_commit=self.base_commit, diff_sha=diff_sha, patch_path=str(patch_path or ""),
            created_request=self.budget.requests_used, changed_files=tuple(sorted(set(changed_files))),
            impact_slice_id=self.impact.diff_sha if self.impact else None,
            evidence_packet_id=self.evidence.packet_id if self.evidence else None,
            test_evidence_ids=tuple(test_evidence_ids), oracle_risks=tuple(r.risk_type for r in self.oracle_risks),
            restorable=bool(patch_path),
        )
        self.candidates.append(candidate)
        self.budget = BudgetState(**{**self.budget.__dict__, "current_candidate_id": candidate.candidate_id,
                                     "wip_since_candidate": False})
        self._emit("CandidateSnapshotCreated", candidate=candidate)
        return candidate

    def observe_test_result(self, *, passed: bool, test_evidence_id: str,
                            reproducer_status: ReproducerStatus | None = None,
                            regression_bounded: bool = False) -> CandidateSnapshot | None:
        if not (self.enabled and self.candidates):
            return None
        latest = self.candidates[-1]
        level = latest.level
        if passed and (reproducer_status is ReproducerStatus.PRE_FAIL_POST_PASS or test_evidence_id):
            level = max(level, CandidateLevel.C2, key=lambda item: list(CandidateLevel).index(item))
        if passed and regression_bounded and self.impact and self.matrix:
            level = max(level, CandidateLevel.C3, key=lambda item: list(CandidateLevel).index(item))
        if level is latest.level:
            return latest
        promoted = CandidateSnapshot(**{**latest.__dict__, "candidate_id": f"candidate-{len(self.candidates)+1:03d}",
                                        "level": level, "test_evidence_ids": latest.test_evidence_ids + (test_evidence_id,)})
        self.candidates.append(promoted)
        self.budget = BudgetState(**{**self.budget.__dict__, "current_candidate_id": promoted.candidate_id})
        self._emit("CandidatePromoted", candidate=promoted)
        return promoted

    def update_budget(self, requests_used: int) -> BudgetState:
        if not (self.enabled and self.config.budget_finalization_enabled):
            return self.budget
        try:
            remaining = max(0, self.budget.request_limit - requests_used)
            phase = "explore"
            reason = "normal budget"
            if remaining <= self.budget.restore_floor:
                phase, reason = "finalize", "restore floor reached"
            elif remaining <= self.budget.finalization_reserve:
                phase, reason = "finalize", "finalization reserve reached"
            self.budget = BudgetState(self.budget.request_limit, requests_used, remaining, phase,
                                      self.budget.finalization_reserve, self.budget.restore_floor,
                                      self.budget.current_candidate_id, self.budget.wip_since_candidate, reason)
            self._emit("BudgetPhaseChanged", budget=self.budget)
            return self.budget
        except Exception as exc:
            self._fail_open("update_budget", exc)
            return self.budget

    def finalize(
        self,
        reason: str,
        *,
        fallback_patch_path: Path | None = None,
        fallback_changed_files: Iterable[str] = (),
    ) -> CandidateSnapshot | None:
        if not self.enabled:
            return None
        self._emit("FinalizationStarted", reason=reason)
        stable = [item for item in self.candidates if item.restorable]
        if not stable:
            if (
                fallback_patch_path is not None
                and fallback_patch_path.is_file()
                and fallback_patch_path.stat().st_size
            ):
                self._emit(
                    "WorkspaceDiffFallbackRetained",
                    reason="candidate_bookkeeping_missing",
                    patch_path=str(fallback_patch_path),
                    changed_files=tuple(fallback_changed_files),
                    audit_only=True,
                )
                self._emit(
                    "V3Completed",
                    reason="workspace_diff_fallback_retained",
                    exported_patch=str(fallback_patch_path),
                    candidate_status="audit_only_workspace_diff",
                )
                return None
            self._emit("V3Completed", reason="no_stable_candidate", exported_patch="")
            return None
        selected = sorted(stable, key=lambda item: (-list(CandidateLevel).index(item.level),
            len(item.oracle_risks), len(item.changed_files), -item.created_request, item.candidate_id))[0]
        self._emit("CandidateRestored", candidate=selected, reason=reason)
        self._emit("V3Completed", reason=reason, exported_patch=selected.patch_path)
        return selected

    def attribute_failures(self, baseline: Iterable[FailureRecord] | None, post: Iterable[FailureRecord], *,
                           baseline_identity: ComparableRunIdentity | None, post_identity: ComparableRunIdentity) -> FailureAttribution:
        if not (self.enabled and self.config.differential_validation_enabled):
            return FailureAttribution()
        if baseline is None:
            self.differential = FailureAttribution(unknown=("baseline unavailable",))
        elif baseline_identity != post_identity:
            self.differential = FailureAttribution(incomparable=("run identities differ",))
        else:
            before, after = {item.failure_id: item for item in baseline}, {item.failure_id: item for item in post}
            new = tuple(sorted(set(after) - set(before)))
            env = tuple(item.failure_id for item in after.values() if item.category in {"environment", "network", "collection"})
            self.differential = FailureAttribution(resolved_failures=tuple(sorted(set(before) - set(after))),
                new_regression_candidates=tuple(item for item in new if item not in env),
                baseline_existing=tuple(sorted(set(before) & set(after))), persistent_failures=tuple(sorted(set(before) & set(after))),
                environment_errors=tuple(sorted(env)))
        self._emit("FailureAttributionCompleted", attribution=self.differential)
        return self.differential

    def before_tool_call(self, tool_name: str) -> V3Advice:
        if not self.enabled:
            return V3Advice()
        messages = [risk.message for risk in self.oracle_risks[-3:]]
        if self.budget.phase == "finalize":
            messages.append("finalization reserve active; avoid new root-cause directions")
        return V3Advice("strong_warning" if messages else "none", tuple(messages),
                        ("record unknown rather than blocking the tool",),
                        tuple(item.test for item in (self.impact.impacted_tests if self.impact else ())),
                        self.budget.phase == "finalize")

    def artifact(self) -> dict[str, Any]:
        return jsonable({"schema_version": SCHEMA_VERSION, "config": self.config, "events": self.events,
            "derived_state": {"evidence": self.evidence, "hypotheses": tuple(self.hypotheses),
                "oracle_risks": tuple(self.oracle_risks), "impact_slice": self.impact, "contract_matrix": self.matrix,
                "reproducer": self.reproducer, "candidate_snapshots": tuple(self.candidates), "budget": self.budget,
                "differential_validation": self.differential, "internal_errors": tuple(self.internal_errors)}})

    def write_artifact(self, root: Path) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        artifact = self.artifact()
        (root / "events.jsonl").write_text("".join(json.dumps(event, sort_keys=True) + "\n" for event in artifact["events"]), encoding="utf-8")
        (root / "summary.json").write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
        return root

    @classmethod
    def replay(cls, events: Iterable[dict[str, Any]], *, config: CapabilityV3Config | None = None) -> "CapabilityV3Controller":
        controller = cls(config or CapabilityV3Config(enabled=True))
        controller.events = [dict(event) for event in events]
        # The source event log remains authoritative; consumers can reconstruct richer typed state progressively.
        return controller

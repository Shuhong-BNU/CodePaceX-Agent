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
_EXCLUDED_PATH_PARTS = frozenset({
    ".git", ".venv", "venv", ".evaluation-v2-preflight-venv", "site-packages",
    "build", "dist", "__pycache__", ".pytest_cache", ".mypy_cache",
})
_ISSUE_WRAPPERS = frozenset({
    "after", "agent", "bug", "change", "code", "failure", "fix", "for", "from",
    "implement", "issue", "must", "please", "problem", "project", "repository",
    "should", "statement", "task", "test", "tests", "the", "this", "update", "with",
})
_CONTRACT_TERMS = frozenset({
    "api", "backend", "compatibility", "config", "default", "exception", "python",
    "runtime", "serializ", "type", "validation",
})


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

    @staticmethod
    def _is_excluded_path(path: Path, repository: Path) -> bool:
        try:
            parts = path.relative_to(repository).parts
        except ValueError:
            return True
        return any(part in _EXCLUDED_PATH_PARTS for part in parts)

    @staticmethod
    def _issue_entities(issue: str, target_symbols: Iterable[str]) -> tuple[str, ...]:
        """Retain named task entities while discarding generic SWE task wrappers."""
        explicit = [item.strip("`'\".,:;()[]{}") for item in target_symbols]
        words = [word for word in _WORDS.findall(issue) if word.lower() not in _ISSUE_WRAPPERS]
        # Mixed/capitalized identifiers and config-like names are higher value than prose.
        ranked = sorted(
            dict.fromkeys([*explicit, *words]),
            key=lambda word: (
                not ("_" in word or any(char.isupper() for char in word) or word.lower() in _CONTRACT_TERMS),
                word.lower(),
            ),
        )
        return tuple(word for word in ranked if word)[:12]

    def _concrete_unknown(self, *, entities: tuple[str, ...], target_count: int) -> tuple[str, ...]:
        if target_count:
            return ()
        if not entities:
            return ("no named entity in issue statement",)
        return ("no repository definition matched named entities: " + ", ".join(entities[:3]),)

    def collect_evidence(self, issue: str, repository: Path,
                         target_symbols: Iterable[str] = ()) -> ContractEvidencePacket | None:
        """Read only repository evidence, bounded by category and file size."""
        if not (self.enabled and self.config.contract_recovery_enabled):
            return None
        try:
            symbols = self._issue_entities(issue, target_symbols)
            issue_ref = self._ref(kind="issue_statement", path=None, symbol=None, text=issue,
                                  commit=self.base_commit, confidence="high")
            buckets: dict[str, list[EvidenceRef]] = {name: [] for name in (
                "target", "callers", "implementations", "tests", "defaults", "serialization")}
            for path in sorted(repository.rglob("*.py"))[:800]:
                if self._is_excluded_path(path, repository):
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
                unknowns=self._concrete_unknown(entities=symbols, target_count=len(buckets["target"])),
            )
            self._emit("EvidenceCollected", packet=self.evidence)
            for item in conflict:
                self._emit("EvidenceConflictDetected", conflict=item)
            return self.evidence
        except Exception as exc:
            self._fail_open("collect_evidence", exc)
            return None

    def observe_tool_evidence(self, *, tool_name: str, arguments: dict[str, Any], output: str,
                              is_error: bool) -> EvidenceRef | None:
        """Merge bounded read-tool facts into the single V3 evidence packet.

        This is deliberately lossy: output is represented by a digest and a short
        preview, so artifacts do not become a second transcript or secret store.
        """
        if not (self.enabled and self.evidence and not is_error):
            return None
        if tool_name not in {"ReadFile", "Grep", "Glob"} or not output:
            return None
        try:
            raw_path = str(arguments.get("file_path") or arguments.get("path") or "")
            path = Path(raw_path) if raw_path else None
            kind = {"ReadFile": "tool_read", "Grep": "tool_grep", "Glob": "tool_glob"}[tool_name]
            symbol = str(arguments.get("pattern") or "") or None
            ref = self._ref(kind=kind, path=path, symbol=symbol, text=output,
                            commit=self.base_commit, confidence="high")
            packet = self.evidence
            tests = list(packet.tests_and_fixtures)
            callers = list(packet.callers)
            if tool_name == "Glob" or "test" in output.lower() or "test" in raw_path.lower():
                tests.append(ref)
            else:
                callers.append(ref)
            self.evidence = ContractEvidencePacket(
                **{**packet.__dict__, "callers": tuple(callers[:12]),
                   "tests_and_fixtures": tuple(tests[:12])}
            )
            self._emit("ToolEvidenceObserved", tool_name=tool_name, evidence=ref)
            self._update_hypotheses_from_evidence(ref)
            return ref
        except Exception as exc:
            self._fail_open("observe_tool_evidence", exc)
            return None

    def initialize_contract_reasoning(self, issue: str) -> None:
        """Create at most two evidence-backed alternatives for contract-heavy tasks."""
        if not (self.enabled and self.evidence):
            return
        lowered = issue.lower()
        if not any(term in lowered for term in _CONTRACT_TERMS):
            return
        evidence_ids = tuple(item.evidence_id for item in self.evidence.target_symbols[:2])
        if not evidence_ids:
            return
        self.record_hypotheses((
            HypothesisRecord("hyp-contract-current", "Existing target behavior is the intended contract.",
                             evidence_ids, (), "A targeted test preserves the observed implementation behavior.",
                             "Read target implementation", "proposed", self.budget.requests_used, self.budget.requests_used),
            HypothesisRecord("hyp-contract-adjacent", "A caller, config, or sibling defines a different boundary.",
                             evidence_ids, (), "A caller/config/sibling read contradicts the target-only interpretation.",
                             "Grep named entity in project sources", "proposed", self.budget.requests_used, self.budget.requests_used),
        ))
        dimensions: list[ContractDimension] = []
        if self.evidence.defaults_and_config:
            dimensions.append(ContractDimension("configuration", ("default", "explicit"),
                                                tuple(item.evidence_id for item in self.evidence.defaults_and_config[:2])))
        if self.evidence.implementations or self.evidence.callers:
            dimensions.append(ContractDimension("implementation_boundary", ("target", "adjacent"), evidence_ids))
        if dimensions:
            self.build_matrix(dimensions)

    def _update_hypotheses_from_evidence(self, evidence: EvidenceRef) -> None:
        for record in tuple(self.hypotheses):
            if record.status != "proposed":
                continue
            if "adjacent" in record.hypothesis_id and evidence.evidence_type in {"tool_grep", "tool_read"}:
                self.reject_hypothesis(record.hypothesis_id, tool_evidence_id=evidence.evidence_id)
                break

    def request_advice(self) -> str:
        """Build a compact, bounded request supplement from the current evidence."""
        if not self.enabled or not self.evidence:
            return ""
        entities = ", ".join(self.evidence.issue_entities[:6]) or "none"
        targets = ", ".join(item.symbol or item.path or "evidence" for item in self.evidence.target_symbols[:3]) or "unresolved"
        tests = ", ".join(item.test for item in (self.impact.impacted_tests if self.impact else ())[:4]) or "derive a targeted project test"
        unknowns = "; ".join(self.evidence.unknowns[:2])
        advice = "\n".join((
            "[Capability V3 evidence advice]",
            f"Entities: {entities}", f"Anchors: {targets}", f"Targeted validation: {tests}",
            f"Unknowns: {unknowns or 'none'}", "Keep this advisory; inspect evidence before changing behavior.",
        ))[:1800]
        self._emit("AdviceGenerated", digest=self._hash(advice), preview=advice[:300], chars=len(advice))
        return advice

    def record_advice_injected(self, advice: str, request_system: str) -> None:
        if not (self.enabled and advice):
            return
        digest = self._hash(advice)
        self._emit("AdviceInjected", digest=digest, chars=len(advice))
        self._emit("AdvicePresentInRequest", digest=digest, present=advice in request_system)

    def observe_advice_outcome(self, *, tool_name: str, arguments: dict[str, Any]) -> None:
        if not self.enabled or not self.evidence:
            return
        target = " ".join(str(value) for value in arguments.values())
        recommended = tuple(item.test for item in (self.impact.impacted_tests if self.impact else ()))
        if tool_name == "RunTest" and (not recommended or any(test in target for test in recommended)):
            self._emit("AdviceReferenced", tool_name=tool_name)
        elif tool_name in {"EditFile", "WriteFile", "RunTest"}:
            self._emit("AdviceIgnoredOrRejected", tool_name=tool_name, reason="no matching advised validation")

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
            paths = tuple(str(path).lower() for path in changed_files)
            if any(any(marker in path for marker in ("gold", "evaluator", "swe-bench")) for path in paths):
                risks.append(OracleRisk(
                    "evaluator_or_gold_modified", "high",
                    "changed evaluator or gold material requires strong review",
                ))
            elif any("test" in path or "fixture" in path for path in paths):
                risks.append(OracleRisk(
                    "expected_or_fixture_changed", "warning",
                    "changed project test or fixture requires review",
                ))
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
                if path.suffix == ".py" and path.exists() and not self._is_excluded_path(path, repository):
                    try:
                        symbols.update(node.name for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                                       if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
                    except (OSError, SyntaxError):
                        pass
            recommendations: dict[str, TestRecommendation] = {}
            for test in sorted(set(prior_failing_tests) | set(f2p_tests)):
                recommendations[test] = TestRecommendation(test, 10_000, ("prior_failure_or_f2p",), True)
            for path in sorted(repository.rglob("test*.py"))[:800]:
                if self._is_excluded_path(path, repository):
                    continue
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

    def observe_diff(self, *, diff_text: str, changed_files: Iterable[str], patch_path: Path | None = None,
                     repository: Path | None = None) -> CandidateSnapshot | None:
        if not (self.enabled and self.config.candidate_snapshot_enabled):
            return None
        try:
            changed = tuple(changed_files)
            diff_sha = self._hash(diff_text)
            risks = self.inspect_oracle(changed_files=changed, diff_text=diff_text)
            if repository is not None and self._has_python38_union_regression(diff_text, repository):
                compatibility = OracleRisk(
                    "python_runtime_compatibility", "high",
                    "PEP 604 union syntax is incompatible with this repository's Python 3.8 runtime floor",
                )
                self.oracle_risks.append(compatibility)
                risks = tuple(risks) + (compatibility,)
                self._emit("RuntimeCompatibilityRiskDetected", risk=compatibility)
            return self.snapshot_candidate(CandidateLevel.C1, diff_sha=diff_sha, patch_path=patch_path,
                                           changed_files=changed, oracle_risks=risks)
        except Exception as exc:
            self._fail_open("observe_diff", exc)
            return None

    @staticmethod
    def _has_python38_union_regression(diff_text: str, repository: Path) -> bool:
        if not re.search(r"^\+.*\b[A-Za-z_][A-Za-z0-9_]*\s*\|\s*(?:None|[A-Za-z_])", diff_text, re.M):
            return False
        try:
            metadata = (repository / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        return bool(re.search(r"requires-python\s*=\s*['\"]>=\s*3\.(?:[0-8])", metadata))

    def snapshot_candidate(self, level: CandidateLevel, *, diff_sha: str, patch_path: Path | None,
                           changed_files: tuple[str, ...], test_evidence_ids: Iterable[str] = (),
                           oracle_risks: Iterable[OracleRisk] | None = None) -> CandidateSnapshot | None:
        if not diff_sha or level is CandidateLevel.C0:
            return None
        candidate = CandidateSnapshot(
            candidate_id=f"candidate-{len(self.candidates) + 1:03d}", level=level,
            base_commit=self.base_commit, diff_sha=diff_sha, patch_path=str(patch_path or ""),
            created_request=self.budget.requests_used, changed_files=tuple(sorted(set(changed_files))),
            impact_slice_id=self.impact.diff_sha if self.impact else None,
            evidence_packet_id=self.evidence.packet_id if self.evidence else None,
            test_evidence_ids=tuple(test_evidence_ids),
            # A snapshot owns only risks observed for its own diff.  Retaining all
            # prior global observations made later candidates look artificially
            # risky and was the reason a narrow first C1 could win finalization.
            oracle_risks=tuple(
                risk.risk_type for risk in (self.oracle_risks if oracle_risks is None else oracle_risks)
            ),
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
        if not passed and test_evidence_id:
            failed = CandidateSnapshot(**{
                **latest.__dict__,
                "candidate_id": f"candidate-{len(self.candidates)+1:03d}",
                "known_failures": tuple(sorted(set(latest.known_failures + (test_evidence_id,)))),
            })
            self.candidates.append(failed)
            self.budget = BudgetState(**{**self.budget.__dict__, "current_candidate_id": failed.candidate_id})
            self._emit("CandidateValidationFailed", candidate=failed, test_evidence_id=test_evidence_id)
            return failed
        level = latest.level
        compatibility_risk = "python_runtime_compatibility" in latest.oracle_risks
        if passed and compatibility_risk:
            self._emit("CandidatePromotionDeferred", candidate=latest,
                       reason="runtime compatibility risk requires a compatible patch")
        elif passed and (reproducer_status is ReproducerStatus.PRE_FAIL_POST_PASS or test_evidence_id):
            level = max(level, CandidateLevel.C2, key=lambda item: list(CandidateLevel).index(item))
        if passed and not compatibility_risk and regression_bounded and self.impact and self.matrix:
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
        try:
            selected, selection = self._select_final_candidate(stable)
            self._emit("CandidateSelectionEvaluated", selected_candidate_id=selected.candidate_id,
                       reason="evidence_weighted_finalization", candidates=selection)
        except Exception as exc:
            self._fail_open("select_final_candidate", exc)
            selected = sorted(stable, key=lambda item: (
                -list(CandidateLevel).index(item.level), -item.created_request, item.candidate_id,
            ))[0]
            self._emit("CandidateSelectionFallback", selected_candidate_id=selected.candidate_id,
                       reason="selection_bookkeeping_error", audit_only=True)
        self._emit("CandidateRestored", candidate=selected, reason=reason)
        self._emit("V3Completed", reason=reason, exported_patch=selected.patch_path)
        return selected

    @staticmethod
    def _oracle_risk_penalty(risks: Iterable[str]) -> int:
        """Return a graded advisory penalty without turning risks into vetoes."""
        weights = {
            "evaluator_or_gold_modified": 30,
            "silent_exception": 12,
            "unanchored_public_api": 5,
            "default_without_evidence": 3,
            "expected_or_fixture_changed": 1,
        }
        return sum(weights.get(risk, 4) for risk in risks)

    def _candidate_selection_components(self, candidate: CandidateSnapshot) -> dict[str, Any]:
        """Explain a generic, evidence-first final Candidate choice.

        Passing tests and higher validation levels dominate, while recency and
        changed-file coverage break ties.  Oracle observations are graded rather
        than treated as a blanket veto: evaluator/gold edits are a strong warning,
        normal project tests/fixtures are not.  Existing failures remain a
        regression penalty.
        """
        level = list(CandidateLevel).index(candidate.level)
        evidence = len(candidate.test_evidence_ids)
        regression_count = len(candidate.known_failures)
        risk_penalty = self._oracle_risk_penalty(candidate.oracle_risks)
        coverage = len(candidate.changed_files)
        score = (
            evidence * 100
            + level * 20
            + min(coverage, 12) * 2
            + min(candidate.created_request, 40)
            - regression_count * 15
            - risk_penalty
        )
        return {
            "candidate_id": candidate.candidate_id,
            "score": score,
            "validation_evidence_count": evidence,
            "candidate_level": candidate.level.value,
            "regression_count": regression_count,
            "oracle_risks": candidate.oracle_risks,
            "oracle_risk_penalty": risk_penalty,
            "changed_file_coverage": coverage,
            "created_request": candidate.created_request,
        }

    def _select_final_candidate(
        self, stable: Iterable[CandidateSnapshot],
    ) -> tuple[CandidateSnapshot, tuple[dict[str, Any], ...]]:
        """Select a restorable Candidate and retain every decision component."""
        snapshots = tuple(stable)
        latest_by_diff: dict[str, CandidateSnapshot] = {}
        for candidate in snapshots:
            # A later validation result is authoritative for the same patch.
            latest_by_diff[candidate.diff_sha] = candidate
        evaluated_rows: list[dict[str, Any]] = []
        for candidate in snapshots:
            components = self._candidate_selection_components(candidate)
            authoritative = latest_by_diff[candidate.diff_sha]
            components["selection_eligible"] = candidate.candidate_id == authoritative.candidate_id
            if not components["selection_eligible"]:
                components["superseded_by"] = authoritative.candidate_id
            evaluated_rows.append(components)
        evaluated = tuple(evaluated_rows)
        by_id = {item.candidate_id: item for item in latest_by_diff.values()}
        selected_components = sorted(
            (item for item in evaluated if item["selection_eligible"]),
            key=lambda item: (
                -int(item["score"]),
                -int(item["validation_evidence_count"]),
                -int(item["created_request"]),
                -int(item["changed_file_coverage"]),
                str(item["candidate_id"]),
            ),
        )[0]
        return by_id[str(selected_components["candidate_id"])], evaluated

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
        if self.differential.unknown:
            self._emit("DifferentialIncomparable", reasons=self.differential.unknown)
        if self.differential.incomparable:
            self._emit("DifferentialIncomparable", reasons=self.differential.incomparable)
        for failure_id in self.differential.resolved_failures:
            self._emit("Fixed", failure_id=failure_id)
        for failure_id in self.differential.new_regression_candidates:
            self._emit("New", failure_id=failure_id)
        for failure_id in self.differential.persistent_failures:
            self._emit("Persistent", failure_id=failure_id)
        return self.differential

    def observe_test_execution(self, *, arguments: dict[str, Any], output: str, is_error: bool,
                               test_evidence_id: str, candidate_exists: bool) -> FailureAttribution | None:
        """Record a comparable actual RunTest result as baseline or post-patch evidence."""
        if not self.enabled:
            return None
        try:
            command = tuple(str(item) for item in arguments.get("argv", ()))
            identity = ComparableRunIdentity(
                self.base_commit, str(arguments.get("cwd", "")), "local", "python",
                command, self._hash("\0".join(command))[:16], "offline", int(arguments.get("timeout_seconds", 120)),
            )
            failure = self._failure_records(output, command) if is_error else ()
            baseline = getattr(self, "_baseline_test_result", None)
            if not candidate_exists and baseline is None:
                self._baseline_test_result = (failure, identity)
                self._emit("BaselineObserved", test_evidence_id=test_evidence_id, failures=failure, identity=identity)
                return None
            self._emit("PostObserved", test_evidence_id=test_evidence_id, failures=failure, identity=identity)
            before, before_identity = baseline if baseline is not None else (None, None)
            return self.attribute_failures(before, failure, baseline_identity=before_identity, post_identity=identity)
        except Exception as exc:
            self._fail_open("observe_test_execution", exc)
            return None

    def _failure_records(self, output: str, command: tuple[str, ...]) -> tuple[FailureRecord, ...]:
        nodeids = re.findall(r"^FAILED\s+([^\s]+)", output, re.M)
        if not nodeids:
            nodeids = [command[0] if command else self._hash(output)[:16]]
        category = "collection" if "collect" in output.lower() else "failure"
        return tuple(
            FailureRecord(nodeid, nodeid, category, nodeid)
            for nodeid in sorted(set(nodeids))
        )

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

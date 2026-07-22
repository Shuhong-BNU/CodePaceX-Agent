"""Deterministic, opt-in validation gates for code-changing agent sessions.

The controller deliberately keeps policy decisions outside prompts.  It is
disabled by default and has no observable effect until a ``stage_b`` profile is
supplied to an Agent.  Enabled sessions write an append-only event stream and an
atomic state snapshot under the session directory so separately constructed
Agents can resume the same obligations without resetting them.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
CHECKPOINT_ORDINALS = (20, 30, 36)
INVENTORY_FIELDS = (
    "target_behavior", "failure_assertions", "touched_symbols", "direct_callers",
    "implementations", "config_surfaces", "default_values", "serialization_surfaces",
    "fixtures", "target_tests", "regression_tests", "known_unknowns",
)
EXCEPTION_REASONS = frozenset({
    "environment_unavailable", "dependency_unavailable", "network_required",
    "test_not_runnable", "issue_not_reproducible", "external_service_unavailable",
    "platform_mismatch",
})
REQUEST_36_CHOICES = frozenset({
    "FINALIZE_VALIDATED_PATCH", "ROLLBACK_TO_COHERENT_PATCH", "DECLARE_UNRESOLVED",
})


class ValidationMode(str, Enum):
    DISABLED = "disabled"
    STAGE_B = "stage_b"


class OperationClass(str, Enum):
    READ_ONLY = "read_only"
    TEST_EXECUTION = "test_execution"
    IMPLEMENTATION_WRITE = "implementation_write"
    PLAN_ARTIFACT_WRITE = "plan_artifact_write"
    UNKNOWN_SIDE_EFFECT = "unknown_side_effect"


class CompletionStatus(str, Enum):
    VALIDATED_COMPLETE = "VALIDATED_COMPLETE"
    UNRESOLVED = "UNRESOLVED"
    BLOCKED = "BLOCKED"
    INVALID_COMPLETION_ATTEMPT = "INVALID_COMPLETION_ATTEMPT"


@dataclass(frozen=True)
class ValidationProfile:
    mode: ValidationMode = ValidationMode.DISABLED
    schema_version: int = SCHEMA_VERSION
    checkpoint_ordinals: tuple[int, ...] = CHECKPOINT_ORDINALS
    max_invalid_completion_attempts: int = 3

    @property
    def enabled(self) -> bool:
        return self.mode is ValidationMode.STAGE_B

    @classmethod
    def stage_b(cls) -> "ValidationProfile":
        return cls(mode=ValidationMode.STAGE_B)


@dataclass(frozen=True)
class ValidationDecision:
    allowed: bool
    operation: OperationClass
    reason: str = ""


@dataclass(frozen=True)
class CompletionDecision:
    status: CompletionStatus
    allowed: bool
    terminal: bool
    blockers: tuple[str, ...] = ()

    def message(self) -> str:
        if not self.blockers:
            return self.status.value
        return "Validation prevented a completed claim:\n- " + "\n- ".join(self.blockers)


@dataclass
class ValidationEvent:
    schema_version: int
    event_id: str
    event_sequence: int
    validation_session_id: str
    trial_id: str | None
    agent_id: str | None
    parent_agent_id: str | None
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolObservation:
    tool_call_id: str
    operation: str
    command: str | None
    command_fingerprint: str | None
    workspace_id: str
    sequence: int
    is_error: bool
    exit_code: int | None
    timed_out: bool
    output: str


@dataclass
class TestObligation:
    obligation_id: str
    command: str
    fingerprint: str
    scope: list[str]
    required: bool
    created_at_edit_sequence: int
    last_run_sequence: int | None = None
    last_result: str = "NOT_RUN"
    evidence_reference: str | None = None


def normalize_command(command: str) -> str:
    return " ".join(command.strip().split())


def command_fingerprint(command: str) -> str:
    # A readable stable key is sufficient here: it is an audit matcher, not a secret.
    return normalize_command(command).lower()


def classify_bash_command(command: str) -> OperationClass:
    """Classify shell input conservatively without executing it.

    Any ambiguous compound command is a possible side effect.  Test runners are
    recognized before write markers because their cache files are not source
    edits.  This classifier is intentionally smaller than a shell interpreter.
    """
    normalized = normalize_command(command)
    lowered = normalized.lower()
    if not normalized:
        return OperationClass.UNKNOWN_SIDE_EFFECT
    # Do not treat a test runner embedded in a shell program as a pure test.
    # A chained command or redirection can modify implementation files after the
    # test process returns, so it must pass through the conservative side-effect
    # path instead.
    if re.search(r"(^|[^<])>{1,2}(?!&)", normalized):
        return OperationClass.IMPLEMENTATION_WRITE
    if re.search(r"[;&|`$()]", normalized):
        return OperationClass.UNKNOWN_SIDE_EFFECT
    test_patterns = (
        r"(^|\s)(pytest|tox|nox|unittest)(\s|$)",
        r"(^|\s)(python|python3)\s+-m\s+(pytest|unittest)(\s|$)",
        r"(^|\s)(uv|poetry)\s+run\s+pytest(\s|$)",
        r"(^|\s)(npm|pnpm|yarn)\s+(run\s+)?test(\s|$)",
    )
    if any(re.search(pattern, lowered) for pattern in test_patterns):
        return OperationClass.TEST_EXECUTION
    write_markers = (
        r"(^|\s)(git\s+apply|patch|sed\s+-i|perl\s+-i|tee|touch|mkdir|rm|mv|cp|chmod|chown)\b",
        r"(^|\s)(pip|pip3|uv|npm|pnpm|yarn)\s+(install|add|remove|uninstall)\b",
        r"(^|\s)(python|python3)\b",
        r"(^|\s)(bash|sh|zsh)\s+-c\b",
    )
    if any(re.search(pattern, lowered) for pattern in write_markers):
        return OperationClass.IMPLEMENTATION_WRITE
    read_patterns = (
        r"^(git\s+(status|diff|log|show|branch|rev-parse))\b",
        r"^(rg|grep|egrep|fgrep|find|ls|cat|pwd|which|head|tail|wc|stat|diff|env|printenv)\b",
        r"^(echo)\b",
    )
    if any(re.search(pattern, lowered) for pattern in read_patterns) and not re.search(r"[;&|`$()]", normalized):
        return OperationClass.READ_ONLY
    return OperationClass.UNKNOWN_SIDE_EFFECT


def classify_operation(
    tool_name: str,
    tool_category: str,
    arguments: dict[str, Any],
    *,
    plan_mode: bool = False,
    plan_artifact_path: str | None = None,
    tool_module: str = "",
) -> OperationClass:
    if tool_name == "RunTest":
        return OperationClass.TEST_EXECUTION
    if tool_name == "Bash":
        command = arguments.get("command")
        return classify_bash_command(command) if isinstance(command, str) else OperationClass.UNKNOWN_SIDE_EFFECT
    if tool_module.startswith("codepacex.mcp") or tool_name.startswith("mcp_"):
        return OperationClass.UNKNOWN_SIDE_EFFECT
    if tool_category == "read":
        return OperationClass.READ_ONLY
    if tool_category == "write":
        path = arguments.get("file_path") or arguments.get("path")
        if plan_mode and plan_artifact_path and isinstance(path, str):
            try:
                if Path(path).resolve() == Path(plan_artifact_path).resolve():
                    return OperationClass.PLAN_ARTIFACT_WRITE
            except OSError:
                pass
        return OperationClass.IMPLEMENTATION_WRITE
    return OperationClass.UNKNOWN_SIDE_EFFECT


class ValidationController:
    """Shared, append-only validation state for a Trial/session.

    The controller is intentionally usable without an Agent so deterministic
    fixtures and preserved trace replay exercise exactly the same state machine.
    """

    def __init__(
        self,
        profile: ValidationProfile | None = None,
        *,
        session_id: str | None = None,
        trial_id: str | None = None,
        state_dir: Path | None = None,
    ) -> None:
        self.profile = profile or ValidationProfile()
        self.session_id = session_id or uuid.uuid4().hex
        self.trial_id = trial_id
        self.state_dir = state_dir
        self._lock = threading.RLock()
        self._sequence = 0
        self._events: list[ValidationEvent] = []
        self._observations: dict[str, ToolObservation] = {}
        self._last_observation_id: str | None = None
        self._inventory: dict[str, Any] | None = None
        self._inventory_revision = 0
        self._reproduction: dict[str, Any] | None = None
        self._exception: dict[str, Any] | None = None
        self._obligations: dict[str, TestObligation] = {}
        self._regression: dict[str, dict[str, Any]] = {}
        self._edit_sequence = 0
        self._request_ordinal = 0
        self._pending_checkpoints: set[int] = set()
        self._acknowledged_checkpoints: set[int] = set()
        self._request_36_choice: str | None = None
        self._invalid_completion_attempts = 0
        if self.profile.enabled and self.state_dir is not None:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            self._load_snapshot()

    @property
    def enabled(self) -> bool:
        return self.profile.enabled

    @property
    def event_sequence(self) -> int:
        return self._sequence

    def child(self, *, workspace_id: str | None = None) -> "ValidationController":
        """Return the same controller; scopes are carried by each observation."""
        return self

    def _emit(self, event_type: str, *, agent_id: str | None = None, parent_agent_id: str | None = None, **payload: Any) -> None:
        if not self.enabled:
            return
        self._sequence += 1
        event = ValidationEvent(
            schema_version=SCHEMA_VERSION,
            event_id=f"{self.session_id}:{self._sequence}",
            event_sequence=self._sequence,
            validation_session_id=self.session_id,
            trial_id=self.trial_id,
            agent_id=agent_id,
            parent_agent_id=parent_agent_id,
            event_type=event_type,
            payload=payload,
        )
        self._events.append(event)
        self._persist(event)

    def _persist(self, event: ValidationEvent) -> None:
        if self.state_dir is None:
            return
        event_path = self.state_dir / "validation-events.jsonl"
        with event_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")
        snapshot = self.summary()
        fd, name = tempfile.mkstemp(prefix="validation-", suffix=".json", dir=self.state_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(snapshot, fh, sort_keys=True, indent=2)
                fh.write("\n")
            os.replace(name, self.state_dir / "validation-state.json")
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _load_snapshot(self) -> None:
        snapshot_path = self.state_dir / "validation-state.json" if self.state_dir else None
        if snapshot_path is None or not snapshot_path.exists():
            return
        try:
            data = json.loads(snapshot_path.read_text(encoding="utf-8"))
            if data.get("schema_version") != SCHEMA_VERSION or data.get("validation_session_id") != self.session_id:
                raise ValueError("validation state identity mismatch")
            self._sequence = int(data.get("event_sequence", 0))
            self._edit_sequence = int(data.get("edit_sequence", 0))
            self._request_ordinal = int(data.get("request_ordinal", 0))
            self._inventory = data.get("contract_inventory")
            self._inventory_revision = int(data.get("contract_inventory_revision", 0))
            self._reproduction = data.get("reproduction")
            self._exception = data.get("reproduction_exception")
            self._pending_checkpoints = {int(item) for item in data.get("pending_checkpoints", [])}
            self._acknowledged_checkpoints = {int(item) for item in data.get("acknowledged_checkpoints", [])}
            self._request_36_choice = data.get("request_36_choice")
            self._invalid_completion_attempts = int(data.get("invalid_completion_attempts", 0))
            self._observations = {
                item["tool_call_id"]: ToolObservation(**item)
                for item in data.get("observations", [])
                if isinstance(item, dict) and isinstance(item.get("tool_call_id"), str)
            }
            last_observation_id = data.get("last_observed_tool_call_id")
            self._last_observation_id = (
                last_observation_id
                if isinstance(last_observation_id, str)
                and last_observation_id in self._observations
                else None
            )
            self._regression = {
                item["fingerprint"]: item for item in data.get("regression_state", [])
                if isinstance(item, dict) and isinstance(item.get("fingerprint"), str)
            }
            for item in data.get("target_obligations", []):
                obligation = TestObligation(**item)
                self._obligations[obligation.obligation_id] = obligation
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"validation state is unavailable: {exc}") from exc

    @contextmanager
    def _state_lock(self):
        """Serialize updates across threads and local Unix teammate processes."""
        with self._lock:
            handle = None
            try:
                if self.state_dir is not None:
                    lock_path = self.state_dir / "validation-state.lock"
                    handle = lock_path.open("a+")
                    try:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                    except ImportError:  # pragma: no cover - non-Unix fallback
                        pass
                    self._load_snapshot()
                yield
            finally:
                if handle is not None:
                    try:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except ImportError:  # pragma: no cover - non-Unix fallback
                        pass
                    handle.close()

    def _reproduction_satisfied(self) -> bool:
        return self._reproduction is not None or self._exception is not None

    def assess_tool(
        self,
        *,
        agent_id: str,
        parent_agent_id: str | None,
        workspace_id: str,
        tool_call_id: str,
        tool_name: str,
        tool_category: str,
        tool_module: str,
        arguments: dict[str, Any],
        plan_mode: bool = False,
        plan_artifact_path: str | None = None,
    ) -> ValidationDecision:
        operation = classify_operation(
            tool_name, tool_category, arguments, plan_mode=plan_mode,
            plan_artifact_path=plan_artifact_path, tool_module=tool_module,
        )
        if not self.enabled:
            return ValidationDecision(True, operation)
        with self._state_lock():
            if operation is OperationClass.PLAN_ARTIFACT_WRITE:
                self._emit("validation_observation", agent_id=agent_id, parent_agent_id=parent_agent_id,
                           tool_call_id=tool_call_id, operation_class=operation.value, decision="allow")
                return ValidationDecision(True, operation)
            if operation in {OperationClass.IMPLEMENTATION_WRITE, OperationClass.UNKNOWN_SIDE_EFFECT}:
                if not self._reproduction_satisfied():
                    reason = "reproduction evidence or a structured exception is required before side effects"
                    self._emit("validation_blocked", agent_id=agent_id, parent_agent_id=parent_agent_id,
                               tool_call_id=tool_call_id, operation_class=operation.value, reason=reason)
                    return ValidationDecision(False, operation, reason)
                if self._inventory is None:
                    reason = "a complete contract inventory is required before implementation edits"
                    self._emit("validation_blocked", agent_id=agent_id, parent_agent_id=parent_agent_id,
                               tool_call_id=tool_call_id, operation_class=operation.value, reason=reason)
                    return ValidationDecision(False, operation, reason)
                if self._pending_checkpoints:
                    reason = f"request checkpoint(s) pending: {sorted(self._pending_checkpoints)}"
                    self._emit("validation_blocked", agent_id=agent_id, parent_agent_id=parent_agent_id,
                               tool_call_id=tool_call_id, operation_class=operation.value, reason=reason)
                    return ValidationDecision(False, operation, reason)
            self._emit("validation_observation", agent_id=agent_id, parent_agent_id=parent_agent_id,
                       tool_call_id=tool_call_id, operation_class=operation.value, decision="allow")
            return ValidationDecision(True, operation)

    def observe_tool_result(
        self,
        *,
        agent_id: str,
        parent_agent_id: str | None,
        workspace_id: str,
        tool_call_id: str,
        tool_name: str,
        tool_category: str,
        tool_module: str,
        arguments: dict[str, Any],
        is_error: bool,
        output: str,
        exit_code: int | None = None,
        timed_out: bool = False,
        plan_mode: bool = False,
        plan_artifact_path: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        operation = classify_operation(tool_name, tool_category, arguments, plan_mode=plan_mode,
                                       plan_artifact_path=plan_artifact_path, tool_module=tool_module)
        command = arguments.get("command") if isinstance(arguments.get("command"), str) else None
        if tool_name == "RunTest" and isinstance(arguments.get("argv"), list):
            argv = arguments["argv"]
            if all(isinstance(item, str) for item in argv):
                command = "pytest " + " ".join(argv)
        with self._state_lock():
            observation = ToolObservation(
                tool_call_id=tool_call_id, operation=operation.value, command=command,
                command_fingerprint=command_fingerprint(command) if command else None,
                workspace_id=workspace_id, sequence=self._sequence + 1, is_error=is_error,
                exit_code=exit_code, timed_out=timed_out, output=output,
            )
            self._observations[tool_call_id] = observation
            self._last_observation_id = tool_call_id
            # Opaque commands are gated before execution and must also invalidate
            # test evidence afterwards: a wrapper script or MCP tool may write
            # implementation files even when static classification cannot prove it.
            if operation in {OperationClass.IMPLEMENTATION_WRITE, OperationClass.UNKNOWN_SIDE_EFFECT}:
                self._edit_sequence += 1
                for obligation in self._obligations.values():
                    obligation.last_result = "STALE"
                for regression in self._regression.values():
                    regression["post"] = None
            if operation is OperationClass.TEST_EXECUTION and command:
                self._apply_test_result(observation)
            self._emit("validation_observation", agent_id=agent_id, parent_agent_id=parent_agent_id,
                       tool_call_id=tool_call_id, operation_class=operation.value,
                       command_fingerprint=observation.command_fingerprint, is_error=is_error,
                       exit_code=exit_code, timed_out=timed_out, edit_sequence=self._edit_sequence)

    def _apply_test_result(self, observation: ToolObservation) -> None:
        result = "PASSED" if not observation.is_error and not observation.timed_out and observation.exit_code in (None, 0) else "FAILED"
        for obligation in self._obligations.values():
            if obligation.fingerprint == observation.command_fingerprint:
                obligation.last_run_sequence = observation.sequence
                obligation.evidence_reference = observation.tool_call_id
                obligation.last_result = result if obligation.created_at_edit_sequence <= self._edit_sequence else "STALE"
        for regression in self._regression.values():
            if regression["fingerprint"] == observation.command_fingerprint:
                parsed = self._parse_test_result(observation)
                if self._edit_sequence == 0 and regression.get("baseline") is None:
                    regression["baseline"] = parsed
                elif self._edit_sequence > 0:
                    regression["post"] = parsed

    @staticmethod
    def _parse_test_result(observation: ToolObservation) -> dict[str, Any]:
        text = observation.output.lower()
        failures = sorted(set(re.findall(r"(?:failed|error)\s+([\w./:-]+)", text)))
        collection_error = "collection error" in text or "collected 0 items /" in text
        return {
            "evidence_reference": observation.tool_call_id,
            "exit_code": observation.exit_code,
            "is_error": observation.is_error,
            "timed_out": observation.timed_out,
            "failures": failures,
            "collection_error": collection_error,
            "comparable": not observation.timed_out,
        }

    def observe_request_completed(self, *, agent_id: str, parent_agent_id: str | None = None) -> int:
        if not self.enabled:
            return 0
        with self._state_lock():
            self._request_ordinal += 1
            ordinal = self._request_ordinal
            if ordinal in self.profile.checkpoint_ordinals:
                self._pending_checkpoints.add(ordinal)
                self._emit("validation_checkpoint", agent_id=agent_id, parent_agent_id=parent_agent_id,
                           ordinal=ordinal, state="pending")
            else:
                self._emit("validation_request", agent_id=agent_id, parent_agent_id=parent_agent_id,
                           ordinal=ordinal)
            return ordinal

    def declare(self, action: str, payload: dict[str, Any], *, agent_id: str, parent_agent_id: str | None = None) -> ValidationDecision:
        if not self.enabled:
            return ValidationDecision(False, OperationClass.UNKNOWN_SIDE_EFFECT, "validation is disabled")
        with self._state_lock():
            try:
                if action == "record_reproduction":
                    reference = self._require_observation(payload, "evidence_reference")
                    if reference.operation not in {OperationClass.TEST_EXECUTION.value, OperationClass.READ_ONLY.value}:
                        raise ValueError("reproduction must reference a read-only or test observation")
                    if not bool(payload.get("observed_failure")) or not reference.is_error:
                        raise ValueError("reproduction requires an observed failing result")
                    self._reproduction = {"evidence_reference": reference.tool_call_id, "observed_failure": bool(payload.get("observed_failure")), "result": reference.output[:1000]}
                elif action == "record_reproduction_exception":
                    reason = payload.get("reason_code")
                    if reason not in EXCEPTION_REASONS:
                        raise ValueError("invalid reproduction exception reason")
                    required = ("attempted_commands", "observed_results", "explanation", "remaining_uncertainty")
                    if any(not isinstance(payload.get(name), str) or not payload[name].strip() for name in required):
                        raise ValueError("reproduction exception is incomplete")
                    self._exception = {name: payload[name] for name in ("reason_code", *required)}
                elif action in {"declare_contract_inventory", "amend_contract_inventory"}:
                    inventory = payload.get("inventory")
                    if not isinstance(inventory, dict) or any(name not in inventory for name in INVENTORY_FIELDS):
                        raise ValueError("contract inventory is missing required fields")
                    if action == "amend_contract_inventory" and self._inventory is None:
                        raise ValueError("cannot amend a missing inventory")
                    self._inventory_revision += 1
                    self._inventory = {**inventory, "schema_version": SCHEMA_VERSION, "revision": self._inventory_revision, "reason": payload.get("reason", "initial declaration")}
                    self._register_obligations(inventory.get("target_tests"), self._edit_sequence)
                    self._register_regressions(inventory.get("regression_tests"))
                elif action == "declare_target_tests":
                    self._register_obligations(payload.get("tests"), self._edit_sequence)
                elif action == "declare_regression_slice":
                    self._register_regressions(payload.get("tests"))
                elif action == "ack_request_checkpoint":
                    ordinal = payload.get("ordinal")
                    if not isinstance(ordinal, int) or ordinal not in self._pending_checkpoints:
                        raise ValueError("checkpoint is not pending")
                    if not isinstance(payload.get("summary"), str) or not payload["summary"].strip():
                        raise ValueError("checkpoint acknowledgement requires a summary")
                    details = payload.get("details")
                    required_details = {
                        20: {"reproduction_status", "root_cause_hypothesis", "inventory_revision", "target_tests_registered", "target_tests_executed"},
                        30: {"patch_test_feedback_loop", "remaining_contract", "regression_status", "target_tests_pending"},
                        36: {"completion_plan"},
                    }.get(ordinal, set())
                    if not isinstance(details, dict) or not required_details.issubset(details):
                        raise ValueError("checkpoint acknowledgement is missing required structured details")
                    self._pending_checkpoints.remove(ordinal)
                    self._acknowledged_checkpoints.add(ordinal)
                elif action == "select_request_36_outcome":
                    choice = payload.get("choice")
                    if choice not in REQUEST_36_CHOICES:
                        raise ValueError("invalid request 36 outcome")
                    if 36 not in self._acknowledged_checkpoints:
                        raise ValueError("request 36 checkpoint must be acknowledged first")
                    self._request_36_choice = choice
                else:
                    raise ValueError("unknown validation checkpoint action")
            except ValueError as exc:
                self._emit("validation_blocked", agent_id=agent_id, parent_agent_id=parent_agent_id,
                           action=action, reason=str(exc))
                return ValidationDecision(False, OperationClass.READ_ONLY, str(exc))
            self._emit("validation_declaration", agent_id=agent_id, parent_agent_id=parent_agent_id,
                       action=action, revision=self._inventory_revision)
            return ValidationDecision(True, OperationClass.READ_ONLY)

    def latest_observed_tool_call_id(self) -> str | None:
        """Return the most recent actual tool result, never a model-supplied id."""
        with self._state_lock():
            return self._last_observation_id

    def checkpoint_remediation(self) -> dict[str, Any]:
        """Machine-readable next action for an enabled Stage B session."""
        with self._state_lock():
            missing: list[str] = []
            if not self._reproduction_satisfied():
                missing.append("reproduction")
            if self._inventory is None:
                missing.append("contract_inventory")
            if self._pending_checkpoints:
                missing.append("checkpoint_acknowledgement")
            can_edit = not missing
            return {
                "missing_conditions": missing,
                "next_allowed_actions": [
                    "RunTest", "record_reproduction", "record_reproduction_exception",
                    "declare_contract_inventory", "ack_request_checkpoint",
                ],
                "can_edit": can_edit,
                "can_test": True,
                "pending_checkpoints": sorted(self._pending_checkpoints),
                "remaining_requests_to_ceiling": max(0, 40 - self._request_ordinal),
                "request_36_choices": sorted(REQUEST_36_CHOICES) if self._request_ordinal >= 36 else [],
                "latest_observed_tool_call_id": self._last_observation_id,
            }

    def checkpoint_error(self, action: str, reason: str) -> dict[str, Any]:
        fields = {
            "record_reproduction": ["observed_tool_call_id", "reproduction_status"],
            "record_reproduction_exception": ["reproduction_exception_reason", "attempted_commands", "observed_results", "explanation", "remaining_uncertainty"],
            "declare_contract_inventory": ["contract_inventory"],
            "amend_contract_inventory": ["contract_inventory"],
            "declare_target_tests": ["target_tests"],
            "declare_regression_slice": ["regression_tests"],
            "ack_request_checkpoint": ["checkpoint_ordinal", "checkpoint_summary", "checkpoint_details"],
            "select_request_36_outcome": ["checkpoint_decision"],
        }.get(action, [])
        return {
            "error_code": "validation_checkpoint_rejected",
            "message": reason,
            "missing_fields": fields if "missing" in reason or "requires" in reason or "reference" in reason else [],
            "invalid_fields": fields if "invalid" in reason or "not pending" in reason else [],
            "valid_choices": {"reproduction_exception_reason": sorted(EXCEPTION_REASONS), "checkpoint_decision": sorted(REQUEST_36_CHOICES)},
            **self.checkpoint_remediation(),
        }

    def _require_observation(self, payload: dict[str, Any], key: str) -> ToolObservation:
        reference = payload.get(key)
        if not isinstance(reference, str) or reference not in self._observations:
            raise ValueError("declaration must reference an observed tool result")
        return self._observations[reference]

    def _register_obligations(self, values: Any, edit_sequence: int) -> None:
        if not isinstance(values, list) or not values:
            raise ValueError("at least one target test is required")
        for value in values:
            if not isinstance(value, dict) or not isinstance(value.get("command"), str) or not value["command"].strip():
                raise ValueError("target tests require a command")
            obligation_id = str(value.get("obligation_id") or uuid.uuid4().hex[:12])
            command = normalize_command(value["command"])
            self._obligations[obligation_id] = TestObligation(
                obligation_id=obligation_id, command=command, fingerprint=command_fingerprint(command),
                scope=[str(item) for item in value.get("scope", [])], required=bool(value.get("required", True)),
                created_at_edit_sequence=edit_sequence,
            )

    def _register_regressions(self, values: Any) -> None:
        if not isinstance(values, list) or not values:
            raise ValueError("at least one bounded regression test is required")
        for value in values:
            command = value.get("command") if isinstance(value, dict) else value
            if not isinstance(command, str) or not command.strip():
                raise ValueError("regression tests require a command")
            normalized = normalize_command(command)
            self._regression.setdefault(command_fingerprint(normalized), {
                "command": normalized, "fingerprint": command_fingerprint(normalized), "baseline": None, "post": None,
            })

    def assess_completion(self, *, agent_id: str, parent_agent_id: str | None = None) -> CompletionDecision:
        if not self.enabled:
            return CompletionDecision(CompletionStatus.VALIDATED_COMPLETE, True, True)
        with self._state_lock():
            blockers: list[str] = []
            if not self._reproduction_satisfied():
                blockers.append("reproduction evidence or structured exception is missing")
            if self._inventory is None:
                blockers.append("contract inventory is missing")
            for obligation in self._obligations.values():
                if obligation.required and obligation.last_result != "PASSED":
                    blockers.append(f"target test {obligation.command!r} is {obligation.last_result}")
            for regression in self._regression.values():
                baseline, post = regression.get("baseline"), regression.get("post")
                if baseline is None:
                    blockers.append(f"regression baseline missing for {regression['command']!r}")
                elif self._edit_sequence > 0 and post is None:
                    blockers.append(f"post-edit regression result missing for {regression['command']!r}")
                elif baseline and post:
                    if not baseline.get("comparable") or not post.get("comparable"):
                        blockers.append(f"regression result incomparable for {regression['command']!r}")
                    elif post.get("collection_error") and not baseline.get("collection_error"):
                        blockers.append(f"new collection error in {regression['command']!r}")
                    elif set(post.get("failures", [])) - set(baseline.get("failures", [])):
                        blockers.append(f"new regression in {regression['command']!r}")
            if self._pending_checkpoints:
                blockers.append(f"request checkpoint(s) pending: {sorted(self._pending_checkpoints)}")
            if self._request_ordinal >= 36 and self._request_36_choice is None:
                blockers.append("request 36 outcome is missing")
            if not blockers:
                decision = CompletionDecision(CompletionStatus.VALIDATED_COMPLETE, True, True)
            else:
                self._invalid_completion_attempts += 1
                terminal = self._invalid_completion_attempts >= self.profile.max_invalid_completion_attempts
                status = CompletionStatus.UNRESOLVED if terminal else CompletionStatus.INVALID_COMPLETION_ATTEMPT
                decision = CompletionDecision(status, False, terminal, tuple(blockers))
            self._emit("validation_finalized" if decision.terminal else "validation_blocked",
                       agent_id=agent_id, parent_agent_id=parent_agent_id,
                       status=decision.status.value, blockers=list(decision.blockers),
                       invalid_completion_attempts=self._invalid_completion_attempts)
            return decision

    def drain_events(self) -> list[ValidationEvent]:
        with self._lock:
            events, self._events = self._events, []
            return events

    def summary(self) -> dict[str, Any]:
        regressions: list[dict[str, Any]] = []
        for item in self._regression.values():
            baseline, post = item.get("baseline"), item.get("post")
            new_failures = sorted(set((post or {}).get("failures", [])) - set((baseline or {}).get("failures", [])))
            regressions.append({**item, "new_failures": new_failures})
        return {
            "schema_version": SCHEMA_VERSION,
            "validation_enabled": self.enabled,
            "validation_session_id": self.session_id,
            "trial_id": self.trial_id,
            "event_sequence": self._sequence,
            "event_count": self._sequence,
            "edit_sequence": self._edit_sequence,
            "request_ordinal": self._request_ordinal,
            "reproduction": self._reproduction,
            "reproduction_exception": self._exception,
            "contract_inventory": self._inventory,
            "contract_inventory_revision": self._inventory_revision,
            "target_obligations": [asdict(item) for item in self._obligations.values()],
            "observations": [asdict(item) for item in self._observations.values()],
            "last_observed_tool_call_id": self._last_observation_id,
            "regression_comparisons": regressions,
            "regression_state": list(self._regression.values()),
            "pending_checkpoints": sorted(self._pending_checkpoints),
            "acknowledged_checkpoints": sorted(self._acknowledged_checkpoints),
            "request_36_choice": self._request_36_choice,
            "invalid_completion_attempts": self._invalid_completion_attempts,
        }


def replay_events(events: Iterable[dict[str, Any]], controller: ValidationController) -> dict[str, Any]:
    """Replay sanitized deterministic trace events without constructing a client."""
    for event in events:
        event_type = event.get("type")
        if event_type == "runtime_manifest":
            controller.observe_request_completed(agent_id=str(event.get("agent_id", "replay")))
        elif event_type == "validation":
            inner = event.get("event_type")
            inner_payload = event.get("payload")
            if inner == "validation_declaration" and isinstance(inner_payload, dict):
                controller.declare(str(inner_payload.get("action", "")), inner_payload, agent_id="replay")
        elif event_type == "validation_declaration":
            controller.declare(str(event.get("action", "")), dict(event.get("payload", {})), agent_id="replay")
        elif event_type == "tool_result":
            controller.observe_tool_result(
                agent_id="replay", parent_agent_id=None, workspace_id=str(event.get("workspace_id", "replay")),
                tool_call_id=str(event.get("tool_id", event.get("tool_call_id", uuid.uuid4().hex))),
                tool_name=str(event.get("tool_name", "Bash")), tool_category=str(event.get("tool_category", "write" if event.get("tool_name") in {"EditFile", "WriteFile"} else "command")),
                tool_module=str(event.get("tool_module", "")), arguments=dict(event.get("arguments", event.get("args", {}))),
                is_error=bool(event.get("is_error", False)), output=str(event.get("output", "")),
                exit_code=event.get("exit_code"), timed_out=bool(event.get("timed_out", False)),
            )
    return controller.summary()

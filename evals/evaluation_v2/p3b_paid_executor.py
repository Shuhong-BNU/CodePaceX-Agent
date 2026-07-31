"""Fail-closed P3-B paid executor.

This is the only P3-B path permitted to cross the Provider boundary.  It is
not invoked by import, by pull-request CI, or without all explicit workflow
identities.  The test seam accepts a recording fake task executor, but the
production default delegates to the shared full-replay task executor.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from codepacex.capability_v3 import CapabilityV3Flag
from evals.evaluation_v2 import control_canary, full_replay, p3b_post_merge_rebind as p3b
from evals.paid_gate import (
    BudgetAuthorization, BudgetLedger, PaidRunGate, StageCBudgetAllocation,
    TaskRunBudgetIdentity, allocation_hash, authorization_hash,
)


PAID_SUMMARY_NAME = "p3b-paid-execution-summary.json"
PAIRED_RESULTS_NAME = "p3b-paired-results.json"
PAID_INPUT_PREFLIGHT_NAME = "paid-input-preflight.json"
PREFLIGHT_FAILURE_NAME = "preflight-failure.json"
REQUIRED_ACKNOWLEDGEMENT_PREFIX = "P3B_PAID_AUTHORIZATION:"
INPUT_BUNDLE_SCHEMA_VERSION = 1
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_ACKNOWLEDGEMENT_SUFFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,255}$")
_SAFE_RUN_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
_INPUT_BUNDLE_FIELDS = (
    "expected_main_sha", "expected_freeze_sha256", "expected_allocation_hash",
    "approved_parent_cap_cny", "authorization_acknowledgement", "dispatch_token",
    "run_id", "provider_secret_present",
)
_HASHED_INPUT_FIELDS = (
    "expected_main_sha", "expected_freeze_sha256", "expected_allocation_hash",
    "approved_parent_cap_cny", "authorization_acknowledgement", "dispatch_token", "run_id",
)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_paid_input_bundle(
    *, expected_main_sha: str, expected_freeze_sha256: str, expected_allocation_hash: str,
    approved_parent_cap_cny: str, authorization_acknowledgement: str, dispatch_token: str,
    run_id: str, provider_secret_present: bool,
) -> dict[str, Any]:
    """Return the single machine-readable contract used by preflight and paid execution."""
    return {
        "schema_version": INPUT_BUNDLE_SCHEMA_VERSION,
        "expected_main_sha": expected_main_sha,
        "expected_freeze_sha256": expected_freeze_sha256,
        "expected_allocation_hash": expected_allocation_hash,
        "approved_parent_cap_cny": approved_parent_cap_cny,
        "authorization_acknowledgement": authorization_acknowledgement,
        "dispatch_token": dispatch_token,
        "run_id": run_id,
        "provider_secret_present": provider_secret_present,
    }


def _load_paid_input_bundle(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != INPUT_BUNDLE_SCHEMA_VERSION:
        raise ValueError("P3-B paid input bundle schema is invalid")
    if set(payload) != {"schema_version", *_INPUT_BUNDLE_FIELDS}:
        raise ValueError("P3-B paid input bundle fields are invalid")
    if not isinstance(payload["provider_secret_present"], bool):
        raise ValueError("P3-B paid input bundle Secret presence is invalid")
    if any(not isinstance(payload[field], str) for field in _INPUT_BUNDLE_FIELDS[:-1]):
        raise ValueError("P3-B paid input bundle identities are invalid")
    return {field: payload[field] for field in _INPUT_BUNDLE_FIELDS}


def _input_hashes(inputs: Mapping[str, Any]) -> dict[str, str]:
    return {
        field: hashlib.sha256(value.encode("utf-8")).hexdigest()
        for field in _HASHED_INPUT_FIELDS
        if isinstance((value := inputs.get(field)), str)
    }


def _content_identities(root: Path) -> dict[str, str]:
    return {
        "workflow_content_sha256": _sha256(root / p3b.WORKFLOW_PATH),
        "paid_executor_content_sha256": _sha256(root / "evals/evaluation_v2/p3b_paid_executor.py"),
        "paid_gate_content_sha256": _sha256(root / "evals/paid_gate.py"),
        "freeze_base_commit": p3b.BOUND_MAIN_COMMIT,
    }


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], text=True, capture_output=True, check=False)
    if result.returncode:
        raise ValueError("P3-B paid executor cannot verify Git state")
    return result.stdout.strip()


def _require_safe_run_identity(value: str, field: str, minimum: int = 8) -> None:
    if len(value) < minimum or not _SAFE_RUN_IDENTITY.fullmatch(value):
        raise ValueError(f"P3-B paid executor requires a safe nonempty {field}")


def _require_main_checkout(root: Path, expected_main_sha: str) -> str:
    if not _GIT_SHA.fullmatch(expected_main_sha):
        raise ValueError("P3-B paid execution requires an exact expected main SHA")
    github_ref = str(os.environ.get("GITHUB_REF", ""))
    if github_ref and github_ref != "refs/heads/main":
        raise ValueError("P3-B paid execution is restricted to main")
    if not github_ref and _git(root, "branch", "--show-current") != "main":
        raise ValueError("P3-B paid execution is restricted to main")
    head = _git(root, "rev-parse", "HEAD")
    if head != expected_main_sha:
        raise ValueError("P3-B paid execution HEAD does not match expected main SHA")
    github_sha = str(os.environ.get("GITHUB_SHA", ""))
    if github_sha and github_sha != expected_main_sha:
        raise ValueError("P3-B paid execution workflow SHA does not match expected main SHA")
    ancestor = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", p3b.BOUND_MAIN_COMMIT, head],
        text=True, capture_output=True, check=False,
    )
    if ancestor.returncode:
        raise ValueError("P3-B frozen main is not an ancestor of current main")
    if _git(root, "status", "--porcelain"):
        raise ValueError("P3-B paid execution requires a clean main worktree")
    return head


def _committed_freeze(root: Path) -> tuple[dict[str, Any], str]:
    path = root / p3b.ARTIFACT_DIRECTORY / p3b.FREEZE_NAME
    frozen = json.loads(path.read_text(encoding="utf-8"))
    p3b.validate_freeze(root, frozen)
    return frozen, _sha256(path)


def _validate_acknowledgement(value: str) -> None:
    if not value.startswith(REQUIRED_ACKNOWLEDGEMENT_PREFIX):
        raise ValueError("paid execution requires the explicit P3-B authorization acknowledgement")
    suffix = value[len(REQUIRED_ACKNOWLEDGEMENT_PREFIX):]
    if not _ACKNOWLEDGEMENT_SUFFIX.fullmatch(suffix):
        raise ValueError("P3-B authorization acknowledgement format is invalid")


def _validate_expected_main_sha(value: str) -> None:
    if not _GIT_SHA.fullmatch(value):
        raise ValueError("P3-B paid execution requires an exact expected main SHA")


def validate_paid_inputs(
    root: Path,
    *, expected_main_sha: str, expected_freeze_sha256: str,
    expected_allocation_hash: str,
    approved_parent_cap_cny: str,
    authorization_acknowledgement: str,
    dispatch_token: str,
    run_id: str,
    provider_secret_present: bool,
    require_main: bool = True,
) -> dict[str, Any]:
    """Validate all externally supplied paid identities before any workspace."""
    root = root.resolve()
    _validate_expected_main_sha(expected_main_sha)
    frozen, actual_freeze_sha = _committed_freeze(root)
    if not _SHA256.fullmatch(expected_freeze_sha256) or expected_freeze_sha256 != actual_freeze_sha:
        raise ValueError("expected freeze SHA-256 does not match the committed P3-B freeze")
    actual_allocation_hash = str(frozen["formal_stage_c_allocation"]["allocation_hash"])
    if not _SHA256.fullmatch(expected_allocation_hash) or expected_allocation_hash != actual_allocation_hash:
        raise ValueError("expected allocation hash does not match the committed P3-B allocation")
    if Decimal(approved_parent_cap_cny) != p3b.PARENT_CAP:
        raise ValueError("approved P3-B parent cap does not match the frozen proposal")
    _validate_acknowledgement(authorization_acknowledgement)
    _require_safe_run_identity(dispatch_token, "dispatch token", minimum=12)
    _require_safe_run_identity(run_id, "run ID")
    if not provider_secret_present:
        raise ValueError("P3-B paid execution requires Secret presence; the value is never read here")
    return {
        "freeze": frozen,
        "freeze_sha256": actual_freeze_sha,
        "allocation_hash": actual_allocation_hash,
        "execution_main_head": _require_main_checkout(root, expected_main_sha) if require_main else None,
    }


def _preflight_failure_classification(error: Exception) -> str:
    message = str(error)
    if "acknowledgement" in message:
        return "authorization_acknowledgement_protocol_prefix_or_syntax_mismatch"
    if "main SHA" in message or "main" in message:
        return "expected_main_sha_mismatch"
    if "freeze" in message:
        return "freeze_identity_mismatch"
    if "allocation" in message:
        return "allocation_identity_mismatch"
    if "parent cap" in message:
        return "budget_cap_mismatch"
    if "dispatch token" in message or "run ID" in message:
        return "unsafe_dispatch_identity"
    return "paid_input_validation_failed"


def _preflight_failure_payload(inputs: Mapping[str, Any], error: Exception) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "phase": "validate_paid_inputs",
        "input_hashes": _input_hashes(inputs),
        "expected_main_sha": inputs.get("expected_main_sha"),
        "expected_freeze_sha256": inputs.get("expected_freeze_sha256"),
        "expected_allocation_hash": inputs.get("expected_allocation_hash"),
        "exception_classification": _preflight_failure_classification(error),
        "provider_reached": False,
        "provider_secret_read": False,
        "provider_requests": 0,
        "usage": 0,
        "charge_cny": "0",
        "active_reservation": None,
    }


def _write_preflight_failure(
    artifact_root: Path, inputs: Mapping[str, Any], error: Exception, *, artifact_created: bool,
) -> None:
    if not artifact_created:
        artifact_root.mkdir(parents=True, exist_ok=False)
    _write_json(artifact_root / PREFLIGHT_FAILURE_NAME, _preflight_failure_payload(inputs, error))


def validate_inputs_only(
    root: Path, artifact_root: Path, *, require_main: bool = True, **inputs: Any,
) -> dict[str, Any]:
    """Run the exact paid input gate without workspace, Provider, or ledger creation."""
    root, artifact_root = root.resolve(), artifact_root.resolve()
    artifact_root.mkdir(parents=True, exist_ok=False)
    safe_inputs = dict(inputs)
    try:
        checked = validate_paid_inputs(root, require_main=require_main, **safe_inputs)
    except Exception as error:
        _write_preflight_failure(artifact_root, safe_inputs, error, artifact_created=True)
        raise
    payload = {
        "schema_version": 1,
        "status": "passed_validate_paid_inputs",
        "phase": "validate_paid_inputs",
        "required_acknowledgement_prefix": REQUIRED_ACKNOWLEDGEMENT_PREFIX,
        "input_hashes": _input_hashes(safe_inputs),
        "expected_main_sha": safe_inputs["expected_main_sha"],
        "expected_freeze_sha256": checked["freeze_sha256"],
        "expected_allocation_hash": checked["allocation_hash"],
        "execution_main_head": checked["execution_main_head"],
        **_content_identities(root),
        "provider_reached": False,
        "provider_secret_read": False,
        "provider_requests": 0,
        "usage": 0,
        "charge_cny": "0",
        "active_reservation": None,
    }
    _write_json(artifact_root / PAID_INPUT_PREFLIGHT_NAME, payload)
    return payload


def _formal_bindings(frozen: Mapping[str, Any]) -> tuple[BudgetAuthorization, StageCBudgetAllocation]:
    authorization = BudgetAuthorization.model_validate({
        key: value for key, value in frozen["formal_stage_c_parent_authorization"].items()
        if key not in {"status", "authorization_hash"}
    })
    allocation = StageCBudgetAllocation.model_validate({
        key: value for key, value in frozen["formal_stage_c_allocation"].items()
        if key not in {"status", "allocation_hash"}
    })
    if authorization.authorized_total_cny != p3b.PARENT_CAP:
        raise ValueError("P3-B parent authorization cap changed")
    if allocation_hash(allocation) != frozen["formal_stage_c_allocation"]["allocation_hash"]:
        raise ValueError("P3-B formal allocation hash is invalid")
    if len(allocation.task_run_allocations) != 8:
        raise ValueError("P3-B requires eight formal child allocations")
    return authorization, allocation


def _execution_freeze(root: Path, frozen: Mapping[str, Any], treatment: str) -> dict[str, Any]:
    """Project only the existing shared executor fields; treatment is the sole delta."""
    runtime = full_replay.freeze_payload(root)
    provider = dict(frozen["frozen_identities"]["provider"])
    if provider["retry"] != 0 or provider["fallback_enabled"] or not provider["strict_serial"]:
        raise ValueError("P3-B Provider contract is not fail-closed")
    payload = {
        "runtime_contract": {**runtime["runtime_contract"], "capability_v3_feature_flag": treatment},
        "provider_contract": provider,
        "official_evaluator": dict(frozen["frozen_identities"]["official_evaluator"]),
    }
    if payload["provider_contract"]["model_id"] != frozen["frozen_identities"]["model"]["model_id"]:
        raise ValueError("P3-B model identity differs from freeze")
    return payload


TaskExecutor = Callable[[Mapping[str, Any], Mapping[str, Any], PaidRunGate, Path, str, Mapping[str, Any], TaskRunBudgetIdentity], control_canary.PaidTaskResult]


def _real_task_executor(
    frozen: Mapping[str, Any], execution_freeze: Mapping[str, Any], gate: PaidRunGate,
    run_root: Path, execution_run_id: str, task: Mapping[str, Any], identity: TaskRunBudgetIdentity,
) -> control_canary.PaidTaskResult:
    environments = full_replay._task_environment_contract(Path(gate.root))
    instance_id = str(task["instance_id"])
    if instance_id not in environments:
        raise ValueError(f"P3-B task environment contract missing instance: {instance_id}")
    return full_replay._full_task_executor(
        Path(gate.root), dict(execution_freeze), environments, gate, run_root, execution_run_id,
        dict(task), task_run_identity=identity,
    )


def recording_fake_task_executor(
    frozen: Mapping[str, Any], execution_freeze: Mapping[str, Any], gate: PaidRunGate,
    run_root: Path, execution_run_id: str, task: Mapping[str, Any], identity: TaskRunBudgetIdentity,
) -> control_canary.PaidTaskResult:
    """Zero-provider stand-in for tests of the exact paid coordinator.

    It writes the same task-root raw Artifact shape and settles recording
    transport Usage through the production budget gate.  No Secret or network
    transport is used.
    """
    run = next(item for item in frozen["task_runs"] if item["task_run_id"] == identity.task_run_id)
    task_root = run_root / "tasks" / str(task["instance_id"])
    workspace = task_root / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "tracked.py").write_text("value = 'p3b1-recording-fake'\n", encoding="utf-8")
    patch = "diff --git a/tracked.py b/tracked.py\n--- a/tracked.py\n+++ b/tracked.py\n@@ -1 +1 @@\n-value = 'base'\n+value = 'p3b1-recording-fake'\n"
    (task_root / "candidate.patch").write_text(patch, encoding="utf-8")
    _write_json(task_root / "agent-request-record.json", {
        "task_run_id": identity.task_run_id, "recording_fake_transport": True,
        "request_count": 4, "treatment": run["treatment"],
    })
    _write_json(task_root / "predictions.json", [{
        "instance_id": task["instance_id"], "model_name_or_path": frozen["frozen_identities"]["model"]["model_id"], "model_patch": patch,
    }])
    _write_json(task_root / "official-report.json", {str(task["instance_id"]): {"resolved": False, "fake": True}})
    if run["treatment"] == CapabilityV3Flag.V3_CORE.value:
        v3 = task_root / "capability-v3"; v3.mkdir()
        _write_json(v3 / "summary.json", {"activation_schema": "capability-v3-v1", "treatment": "V3_CORE"})
        (v3 / "events.jsonl").write_text('{"event":"recording_fake"}\n', encoding="utf-8")
        (v3 / "final.patch").write_text(patch, encoding="utf-8")
    # The same gate used in production records each simulated transport call.
    reservations = []
    for _ in range(4):
        reservation = gate.reserve(
            f"swe/v2-full-20/{execution_run_id}/{task['instance_id']}", maximum_requests=1,
            maximum_input_tokens_per_request=full_replay.MAX_INPUT_TOKENS,
            maximum_output_tokens_per_request=full_replay.MAX_OUTPUT_TOKENS,
            task_run_identity=identity,
        )
        gate.settle(reservation, request_usages=[(100, 100)])
        reservations.append(reservation.reservation_id)
    ledger = BudgetLedger.model_validate_json(gate.ledger_path.read_text(encoding="utf-8"))
    accounting = gate.trial_accounting(f"swe/v2-full-20/{execution_run_id}/{task['instance_id']}")
    _write_json(task_root / "task-result.json", {
        "task_run_id": identity.task_run_id, "terminal_status": "unresolved", "recording_fake_transport": True,
        "reservation_ids": reservations, "provider_requests": accounting["request_count"],
        "usage": 800, "charge_cny": accounting["actual_cny"], "active_reservation": ledger.active_reservation,
    })
    return control_canary.PaidTaskResult(
        str(task["instance_id"]), "completed_with_candidate", "exported_nonempty", "executed", "completed",
        "completed", "completed", terminal_status="unresolved", candidate_sha256=_sha256(task_root / "candidate.patch"),
        workspace_diff_sha256=_sha256(task_root / "candidate.patch"), candidate_diff_identity=True,
        evaluator_report_sha256=_sha256(task_root / "official-report.json"), resolved=False,
        provider_requests=accounting["request_count"], charge_cny=accounting["actual_cny"],
        settlement_count=accounting["settlement_count"], active_reservation=None,
        agent_dispatch_started=True, provider_client_initialized=True, model_response_observed=True,
        live_executor_invoked=True, trial_id=f"swe/v2-full-20/{execution_run_id}/{task['instance_id']}",
    )


def _terminal_record(run: Mapping[str, Any], result: control_canary.PaidTaskResult, artifact_root: Path) -> dict[str, Any]:
    task_root = artifact_root / str(run["expected_artifact_path"])
    required = p3b.paired_artifact_schema()["required_raw_artifacts"]
    if not all((task_root / name).is_file() for name in required):
        raise RuntimeError("P3-B paid executor lacks required raw Artifact")
    treatment = str(run["treatment"])
    v3 = task_root / "capability-v3"
    if treatment == "V2_CONTROL" and v3.exists():
        raise RuntimeError("P3-B V2_CONTROL cannot carry V3 Advice or activation Artifact")
    if treatment == "V3_CORE" and not all((v3 / name).is_file() for name in ("summary.json", "events.jsonl", "final.patch")):
        raise RuntimeError("P3-B V3_CORE requires the frozen activation Artifact")
    return {
        **dict(run), "artifact_path": str(run["expected_artifact_path"]), "terminal": asdict(result),
        "v3_advice_present": treatment == "V3_CORE", "v3_activation_schema_present": treatment == "V3_CORE",
    }


def _continues(result: control_canary.PaidTaskResult, ledger: BudgetLedger) -> bool:
    return result.active_reservation is None and ledger.active_reservation is None and result.terminal_status not in {
        "budget_blocked", "provider_authentication_error", "provider_access_denied", "provider_usage_contract_violation",
        "provider_transport_error", "host_runtime_contaminated", "evaluator_execution_error", "evaluator_report_selection_error",
    }


def run_paid_executor(
    root: Path, artifact_root: Path, *, expected_main_sha: str, expected_freeze_sha256: str, expected_allocation_hash: str,
    approved_parent_cap_cny: str, authorization_acknowledgement: str, dispatch_token: str,
    run_id: str, provider_secret_present: bool, task_executor: TaskExecutor | None = None,
    require_main: bool = True,
) -> dict[str, Any]:
    """Execute exactly one strict-serial P3-B dispatch after all fail-closed gates."""
    root, artifact_root = root.resolve(), artifact_root.resolve()
    checked = validate_paid_inputs(
        root, expected_main_sha=expected_main_sha, expected_freeze_sha256=expected_freeze_sha256,
        expected_allocation_hash=expected_allocation_hash,
        approved_parent_cap_cny=approved_parent_cap_cny, authorization_acknowledgement=authorization_acknowledgement,
        dispatch_token=dispatch_token, run_id=run_id, provider_secret_present=provider_secret_present,
        require_main=require_main,
    )
    if artifact_root.exists():
        raise ValueError("P3-B paid executor refuses to overwrite an Artifact")
    frozen = checked["freeze"]
    authorization, allocation = _formal_bindings(frozen)
    artifact_root.mkdir(parents=True)
    guard = p3b.DispatchGuard(artifact_root / "dispatch-guard.json")
    guard.claim(dispatch_token=dispatch_token, run_id=run_id)
    _write_json(artifact_root / p3b.FREEZE_NAME, frozen)
    _write_json(artifact_root / "authorization.json", authorization.model_dump(mode="json"))
    _write_json(artifact_root / "stage-c-allocation.json", allocation.model_dump(mode="json"))
    _write_json(artifact_root / "authorization-acknowledgement.json", {"acknowledgement": authorization_acknowledgement})
    ledger_path = artifact_root / "ledger.json"
    _write_json(ledger_path, BudgetLedger(authorization_hash=authorization_hash(authorization), updated_at="p3b-paid-start").model_dump(mode="json"))
    pricing_path = root / str(frozen["frozen_identities"]["pricing"]["path"])
    gate = PaidRunGate(
        root=root, authorization_path=artifact_root / "authorization.json", ledger_path=ledger_path,
        allocation_path=artifact_root / "stage-c-allocation.json", pricing_path=pricing_path,
        pricing=full_replay.load_pricing(pricing_path), stage="C", allow_descendant_head=True,
    )
    tasks = {item["instance_id"]: item for item in full_replay.load_tasks(root)}
    child_by_id = {item.task_run_id: item for item in allocation.task_run_allocations}
    executor = task_executor or _real_task_executor
    records: list[dict[str, Any]] = []
    stop_reason: str | None = None
    for ordinal, run in enumerate(frozen["task_runs"], start=1):
        if run["ordinal"] != ordinal:
            raise ValueError("P3-B strict serial manifest order is invalid")
        child = child_by_id.get(str(run["task_run_id"]))
        if child is None:
            raise ValueError("P3-B task-run has no child allocation")
        identity = TaskRunBudgetIdentity.model_validate(child.model_dump(
            mode="json", exclude={"execution_run_id", "theoretical_ceiling_cny"},
        ))
        execution_run_id = child.execution_run_id
        run_root = artifact_root / "runs" / f"{run['ordinal']:02d}-{run['treatment']}"
        result = executor(frozen, _execution_freeze(root, frozen, str(run["treatment"])), gate, run_root, execution_run_id, tasks[str(run["instance_id"])], identity)
        records.append(_terminal_record(run, result, artifact_root))
        ledger = BudgetLedger.model_validate_json(ledger_path.read_text(encoding="utf-8"))
        if not _continues(result, ledger):
            stop_reason = f"fail_closed:{result.terminal_status}"
            break
    ledger = BudgetLedger.model_validate_json(ledger_path.read_text(encoding="utf-8"))
    paired: list[dict[str, Any]] = []
    if len(records) == 8 and ledger.active_reservation is None:
        paired = p3b.merge_paired_results(frozen, records)
        _write_json(artifact_root / PAIRED_RESULTS_NAME, paired)
    summary = {
        "schema_version": 1, "paid_execution": task_executor is None, "run_id": run_id,
        "dispatch_token_sha256": hashlib.sha256(dispatch_token.encode()).hexdigest(),
        "freeze_sha256": checked["freeze_sha256"], "allocation_hash": checked["allocation_hash"],
        "freeze_base_commit": p3b.BOUND_MAIN_COMMIT, "execution_main_head": checked["execution_main_head"],
        **_content_identities(root),
        "strict_serial": True, "request_ceiling_per_run": p3b.REQUEST_CEILING, "retry": 0, "fallback": False,
        "records": records, "paired_result_merge_count": len(paired), "stop_reason": stop_reason,
        "provider_requests": len(ledger.request_charges),
        "usage": sum(item.input_tokens + item.output_tokens for item in ledger.request_charges),
        "charge_cny": str(ledger.spent_cny), "ledger_closed": ledger.active_reservation is None,
        "active_reservation": None if ledger.active_reservation is None else ledger.active_reservation.model_dump(mode="json"),
        "provider_secret_read": False, "completed": len(records) == 8 and len(paired) == 4 and ledger.active_reservation is None,
        "artifact_integrity": {"required_raw_artifacts": p3b.paired_artifact_schema()["required_raw_artifacts"], "valid": len(records) == 8},
    }
    _write_json(artifact_root / PAID_SUMMARY_NAME, summary)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P3-B strict serial paid executor")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--input-bundle", type=Path)
    parser.add_argument("--expected-main-sha")
    parser.add_argument("--expected-freeze-sha256")
    parser.add_argument("--expected-allocation-hash")
    parser.add_argument("--approved-parent-cap-cny")
    parser.add_argument("--authorization-acknowledgement")
    parser.add_argument("--dispatch-token")
    parser.add_argument("--run-id")
    parser.add_argument("--provider-secret-present", choices=("true", "false"))
    parser.add_argument("--validate-inputs-only", action="store_true")
    parser.add_argument("--confirm-paid-execution", action="store_true")
    args = parser.parse_args(argv)
    direct = {
        "expected_main_sha": args.expected_main_sha,
        "expected_freeze_sha256": args.expected_freeze_sha256,
        "expected_allocation_hash": args.expected_allocation_hash,
        "approved_parent_cap_cny": args.approved_parent_cap_cny,
        "authorization_acknowledgement": args.authorization_acknowledgement,
        "dispatch_token": args.dispatch_token,
        "run_id": args.run_id,
        "provider_secret_present": args.provider_secret_present == "true" if args.provider_secret_present else None,
    }
    inputs: dict[str, Any] = direct
    try:
        if args.input_bundle:
            if any(value is not None for value in direct.values()):
                raise ValueError("P3-B input bundle cannot be mixed with direct paid inputs")
            inputs = _load_paid_input_bundle(args.input_bundle)
        elif any(value is None for value in direct.values()):
            raise ValueError("P3-B paid executor requires a complete canonical input bundle")
        if args.validate_inputs_only:
            if args.confirm_paid_execution:
                raise ValueError("P3-B validate-only mode cannot confirm paid execution")
            payload = validate_inputs_only(args.root, args.artifact_root, **inputs)
        else:
            payload = None
    except Exception as error:
        if args.validate_inputs_only and not args.artifact_root.exists():
            _write_preflight_failure(args.artifact_root, inputs, error, artifact_created=False)
            return 2
        if args.validate_inputs_only:
            return 2
        raise
    if args.validate_inputs_only:
        assert payload is not None
        print(json.dumps(payload, sort_keys=True))
        return 0
    if not args.confirm_paid_execution:
        raise ValueError("P3-B paid execution requires --confirm-paid-execution")
    summary = run_paid_executor(
        args.root, args.artifact_root, **inputs,
    )
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

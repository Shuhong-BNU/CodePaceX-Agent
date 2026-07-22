"""Explicitly authorized Stage C paid execution.

This module is intentionally separate from :mod:`evals.stage_c`: the latter is
the immutable, zero-provider Freeze contract.  Nothing in this module is
imported by the normal Agent runtime.  A caller must explicitly create a
phase-bound authorization and pass ``--confirm-paid-run`` before this module
can start an Agent subprocess.

The implementation deliberately consumes only the sanitized task bundle (the
seven Agent-visible SWE fields).  Published Goal 4 outcomes, traces, costs,
taxonomy, evaluator reports, and gold patches are neither read nor copied into
an Agent workspace or prompt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import venv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Sequence

import yaml

from codepacex.experiments import ExperimentProfile
from evals.benchmark import RunManifest, RunRecorder, canonical_hash, current_git_commit, sanitize_origin
from evals.costing import load_pricing, pricing_snapshot_hash
from evals.goal3_swe import (
    ENVIRONMENT,
    _child_environment,
    _goal3_extract_patch,
    _goal3_inference_prompt,
    _goal3_materialize_instance,
    _ingest_trace,
    _provider_payload,
    _runtime_secrets,
    collect_goal3_official_outcome,
    require_native_preflight,
)
from evals.paid_gate import (
    BudgetAuthorization,
    BudgetLedger,
    PaidRunGate,
    StageCBudgetAllocation,
    _money,
    allocation_hash,
    authorization_hash,
    ledger_fingerprint,
    provider_request_budget_environment,
    rebind_ledger_authorization,
)
from evals.permission_study import trace_usage
from evals.pilot import PilotConfig
from evals.secret_scan import scan_artifact_roots
from evals.swe_bench_live import official_evaluator_report_path, run_official_evaluator
from evals import stage_c


Phase = Literal["phase_1", "phase_2"]
TERMINAL = frozenset({"resolved", "unresolved", "budget_blocked", "infrastructure_error", "not_run"})
SCORABLE = frozenset({"resolved", "unresolved"})
AGENT_TASK_FIELDS = frozenset({
    "instance_id", "repo", "base_commit", "problem_statement", "platform",
    "version", "environment_setup_commit",
})
_CORE_AGENT_TASK_FIELDS = frozenset({"instance_id", "repo", "base_commit", "problem_statement"})
_NULLABLE_AGENT_TASK_FIELDS = AGENT_TASK_FIELDS - _CORE_AGENT_TASK_FIELDS
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path.name}")
    return value


def _write_new(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        raise ValueError(f"refusing to overwrite Stage C paid evidence: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def phase_ids(phase: Phase) -> tuple[str, ...]:
    return stage_c.PHASE_1_IDS if phase == "phase_1" else stage_c.PHASE_2_IDS


def phase_cap(*, phase: Phase, phase_1_conservative_consumption: Decimal = Decimal("0")) -> Decimal:
    if phase == "phase_1":
        return stage_c.PHASE_1_CAP
    available = stage_c.CUMULATIVE_CAP - phase_1_conservative_consumption
    if available <= 0:
        raise ValueError("Phase 1 conservative consumption exhausts the Stage C cap")
    return _money(available)


def _validated_agent_task(value: Any) -> dict[str, Any]:
    """Validate the exact, Agent-safe schema without changing source semantics."""
    if not isinstance(value, dict) or set(value) != AGENT_TASK_FIELDS:
        raise ValueError("Stage C task bundle must contain only the seven Agent-visible fields")
    if not all(isinstance(value.get(key), str) and value[key] for key in _CORE_AGENT_TASK_FIELDS):
        raise ValueError("Stage C task bundle has an incomplete core Agent-visible task")
    if not all(value.get(key) is None or isinstance(value.get(key), str) for key in _NULLABLE_AGENT_TASK_FIELDS):
        raise ValueError("Stage C task bundle has invalid nullable Agent-visible metadata")
    return value


def load_agent_task_bundle(path: Path, *, phase: Phase) -> list[dict[str, Any]]:
    """Load only the Agent-safe frozen task data; reject an overbroad bundle."""
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        rows.append(_validated_agent_task(value))
    expected = phase_ids(phase)
    actual = tuple(str(item["instance_id"]) for item in rows)
    if actual != expected or len(set(actual)) != len(expected):
        raise ValueError("Stage C task bundle does not match the frozen phase order")
    rendered = json.dumps(rows, sort_keys=True)
    for forbidden in ("patch", "goal4_status", "goal4_requests", "taxonomy", "recommend", "evaluator"):
        if forbidden in rendered.lower():
            raise ValueError("Stage C task bundle contains forbidden historical or gold information")
    return rows


def build_agent_task_bundle(*, source_dataset: Path, output: Path, phase: Phase) -> dict[str, Any]:
    """Write a fresh Agent-safe bundle from an immutable formal Dataset.

    The source is validated before selection so no historical results or
    overbroad fields can silently cross the workflow boundary.  This function
    never writes a synthetic metadata value: nullable source fields remain
    ``null`` in the resulting task bundle.
    """
    if output.exists():
        raise ValueError(f"refusing to overwrite Stage C task bundle: {output.name}")
    selected: dict[str, dict[str, Any]] = {}
    for line in source_dataset.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        task = _validated_agent_task(json.loads(line))
        instance_id = task["instance_id"]
        if instance_id in phase_ids(phase):
            if instance_id in selected:
                raise ValueError("formal Dataset contains a duplicate frozen instance")
            selected[instance_id] = task
    expected = phase_ids(phase)
    if tuple(selected) != expected:
        raise ValueError("formal Dataset does not contain the frozen phase order exactly once")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(selected[item], ensure_ascii=False, sort_keys=True) + "\n" for item in expected), encoding="utf-8")
    load_agent_task_bundle(output, phase=phase)
    return {
        "phase": phase,
        "task_ids": list(expected),
        "schema": sorted(AGENT_TASK_FIELDS),
        "tasks_sha256": _sha256(output),
    }


def frozen_identities(root: Path, freeze_dir: Path) -> dict[str, str]:
    """Validate the committed Freeze and return the immutable identities to bind."""
    root, freeze_dir = root.resolve(), freeze_dir.resolve()
    stage_c.validate_frozen_bundle(root, freeze_dir)
    freeze = _json(freeze_dir / "stage_c_freeze.json")
    matrix = _json(freeze_dir / "stage_c_matrix.json")
    pricing = _json(freeze_dir / "stage_c_pricing_reference.json")
    return {
        "freeze_sha256": canonical_hash(freeze),
        "matrix_sha256": str(matrix["stage_c_matrix_sha256"]),
        "pricing_snapshot_hash": str(pricing["pricing_snapshot_hash"]),
        "experiment_profile_hash": str(freeze["experiment_profile_hash"]),
        "runtime_contract_hash": str(freeze["runtime_contract_hash"]),
        "official_evaluator_commit": str(freeze["official_evaluator_commit"]),
    }


def _phase_paths(evidence_root: Path) -> dict[str, Path]:
    return {
        "binding": evidence_root / "phase-authorization.json",
        "budget_authorization": evidence_root / "budget-authorization.json",
        "ledger": evidence_root / "terminal-ledger.json",
        "allocation": evidence_root / "budget-allocation.json",
        "manifest": evidence_root / "phase-artifact-manifest.json",
        "report": evidence_root / "phase-report.json",
    }


def prepare_phase(
    *, root: Path, freeze_dir: Path, evidence_root: Path, phase: Phase,
    approved_commit: str, authorization_identity: str, supplied_freeze_sha256: str,
    supplied_pricing_hash: str, phase_1_conservative_consumption: Decimal = Decimal("0"),
    phase_1_artifact: Path | None = None, phase_1_artifact_id: str | None = None,
    phase_1_archive_sha256: str | None = None,
) -> dict[str, Any]:
    """Create immutable, zero-provider accounting inputs for exactly one phase.

    The one micro-CNY legacy allocation safety margin is representational only:
    ``spendable_total_cny`` remains the exact user cap, and every reservation is
    checked against it by ``PaidRunGate`` before Provider transport.
    """
    if not _COMMIT.fullmatch(approved_commit) or current_git_commit(root.resolve()) != approved_commit:
        raise ValueError("paid execution must run at the user-approved immutable commit")
    if not authorization_identity.strip():
        raise ValueError("a non-secret phase authorization identity is required")
    identities = frozen_identities(root, freeze_dir)
    if supplied_freeze_sha256 != identities["freeze_sha256"]:
        raise ValueError("authorization freeze hash does not match the committed Freeze")
    if supplied_pricing_hash != identities["pricing_snapshot_hash"]:
        raise ValueError("authorization pricing hash does not match the committed Freeze")
    if not all(_SHA256.fullmatch(value) for value in (supplied_freeze_sha256, supplied_pricing_hash)):
        raise ValueError("authorization identity hashes must be SHA-256")
    cap = phase_cap(phase=phase, phase_1_conservative_consumption=phase_1_conservative_consumption)
    phase_1_binding: dict[str, Any] | None = None
    if phase == "phase_2":
        if phase_1_artifact is None or not phase_1_artifact_id:
            raise ValueError("Phase 2 requires a separately identified Phase 1 Artifact")
        phase_1_binding = validate_phase_1_artifact(phase_1_artifact)
        if phase_1_binding.get("artifact_id") != phase_1_artifact_id:
            raise ValueError("Phase 2 authorization artifact ID differs from Phase 1 evidence")
        if phase_1_archive_sha256 is None or not _SHA256.fullmatch(phase_1_archive_sha256):
            raise ValueError("Phase 2 requires the immutable Phase 1 Artifact archive SHA-256")
        if Decimal(str(phase_1_binding["combined_conservative_consumption_cny"])) != _money(phase_1_conservative_consumption):
            raise ValueError("Phase 2 budget must deduct the verified Phase 1 conservative consumption")
        phase_1_root = phase_1_artifact.resolve().parent
        if _sha256(phase_1_root / "terminal-ledger.json") != phase_1_binding["ledger_sha256"]:
            raise ValueError("Phase 1 terminal ledger SHA-256 does not match its Artifact manifest")
        if _sha256(phase_1_root / "phase-report.json") != phase_1_binding["report_sha256"]:
            raise ValueError("Phase 1 report SHA-256 does not match its Artifact manifest")
    maximum = Decimal(stage_c.budget_contract(root.resolve())["per_request_maximum_reservation_cny"])
    if maximum > cap:
        raise ValueError("the next maximum Provider request exceeds the phase authorization cap")
    paths = _phase_paths(evidence_root.resolve())
    if any(path.exists() for path in paths.values()):
        raise ValueError("Stage C phase evidence root is already initialized")
    # BudgetAuthorization requires a positive safety reserve for Stage C's
    # existing allocation schema.  Its allocation still enforces the exact cap.
    envelope = cap + Decimal("0.000001")
    authorization = BudgetAuthorization(
        authorized_total_cny=envelope,
        stage_limits_cny={"A": envelope, "B": envelope, "C": envelope},
        pricing_snapshot_hash=identities["pricing_snapshot_hash"],
        experiment_commit=approved_commit, authorized_at="user-approved-stage-c-phase",
        authorized_by="user",
    )
    auth_hash = authorization_hash(authorization)
    ledger = BudgetLedger(authorization_hash=auth_hash, updated_at="prepared-zero-provider")
    allocation = StageCBudgetAllocation(
        experiment_commit=approved_commit,
        pricing_snapshot_hash=identities["pricing_snapshot_hash"],
        baseline_ledger_sha256=ledger_fingerprint(ledger),
        baseline_authorization_hash=auth_hash, baseline_spent_cny=Decimal("0"),
        baseline_request_charge_count=0, baseline_settlement_count=0,
        baseline_budget_block_count=0, baseline_rebind_count=0,
        safety_reserve_cny=Decimal("0.000001"), spendable_total_cny=cap,
        category_limits_cny={
            "swe": cap, "mcp": Decimal("0"), "retention": Decimal("0"),
            "permission": Decimal("0"), "multi_agent": Decimal("0"), "long_session": Decimal("0"),
        },
    )
    binding = {
        "schema_version": 1, "phase": phase, "paid_execution": True,
        "authorization_identity": authorization_identity,
        "approved_commit": approved_commit, "freeze_sha256": identities["freeze_sha256"],
        "matrix_sha256": identities["matrix_sha256"], "pricing_snapshot_hash": identities["pricing_snapshot_hash"],
        "experiment_profile_hash": identities["experiment_profile_hash"],
        "runtime_contract_hash": identities["runtime_contract_hash"],
        "official_evaluator_commit": identities["official_evaluator_commit"],
        "authorization_cap_cny": str(cap),
        "phase_1_combined_conservative_consumption_cny": str(_money(phase_1_conservative_consumption)),
        "next_request_maximum_cny": str(maximum), "task_ids": list(phase_ids(phase)),
        "budget_authorization_sha256": auth_hash,
    }
    if phase_1_binding is not None:
        binding["phase_1_artifact_manifest"] = str(phase_1_artifact.resolve())
        binding["phase_1_artifact_id"] = phase_1_artifact_id
        binding["phase_1_artifact_archive_sha256"] = phase_1_archive_sha256
        binding["phase_1_report_sha256"] = phase_1_binding["report_sha256"]
        binding["phase_1_ledger_sha256"] = phase_1_binding["ledger_sha256"]
    _write_new(paths["binding"], binding)
    _write_new(paths["budget_authorization"], authorization.model_dump(mode="json"))
    _write_new(paths["ledger"], ledger.model_dump(mode="json"))
    _write_new(paths["allocation"], allocation.model_dump(mode="json"))
    return {"valid": True, "provider_requests": 0, "paid_workflow_dispatched": False, **binding}


def prepare_phase_one_continuation(
    *, root: Path, freeze_dir: Path, evidence_root: Path, source_root: Path,
    source_artifact_id: str, source_archive_sha256: str, authorization_identity: str,
) -> dict[str, Any]:
    """Bind one recovered first Candidate and continue exactly the remaining five tasks.

    This copies settled accounting (never a Candidate) into a new immutable root,
    rebinds it to the current approved commit, and leaves the first task outside
    the later Agent loop.
    """
    root, evidence_root, source_root = root.resolve(), evidence_root.resolve(), source_root.resolve()
    if not _SHA256.fullmatch(source_archive_sha256) or not source_artifact_id:
        raise ValueError("continuation requires an immutable source Artifact identity")
    source_binding = _json(source_root / "phase-authorization.json")
    source_ledger_path = source_root / "terminal-ledger.json"
    source_ledger = BudgetLedger.model_validate_json(source_ledger_path.read_text(encoding="utf-8"))
    if source_ledger.active_reservation is not None or len(source_ledger.request_charges) != len(source_ledger.settlements):
        raise ValueError("source Phase 1 accounting is not terminal")
    run = next((p for p in source_root.iterdir() if p.is_dir() and (p / "result.json").is_file()), None)
    if run is None:
        raise ValueError("source Candidate evidence is missing")
    first = stage_c.PHASE_1_IDS[0]
    prediction = run / f"{hashlib.sha256(first.encode()).hexdigest()}-prediction.json"
    report = next(run.glob("qwen3.7-max-*.json"), None)
    if not prediction.is_file() or report is None:
        raise ValueError("source Candidate or frozen evaluator report is missing")
    evaluator_id = f"{json.loads((run/'manifest.json').read_text())['run_id']}-{first}"
    report_path = official_evaluator_report_path(cwd=run, run_id=evaluator_id, model_id="qwen3.7-max-2026-06-08", instance_id=first)
    first_status = "resolved" if collect_goal3_official_outcome(report_path, first) else "unresolved"
    if any(path.exists() for path in _phase_paths(evidence_root).values()):
        raise ValueError("continuation evidence root is already initialized")
    pricing = load_pricing(root / stage_c.PRICING_PATH)
    identities = frozen_identities(root, freeze_dir)
    cap = stage_c.PHASE_1_CAP
    envelope = cap + Decimal("0.000001")
    replacement = BudgetAuthorization(authorized_total_cny=envelope, stage_limits_cny={"A": envelope, "B": envelope, "C": envelope}, pricing_snapshot_hash=identities["pricing_snapshot_hash"], experiment_commit=current_git_commit(root), authorized_at="user-approved-stage-c-continuation", authorized_by="user")
    previous = BudgetAuthorization.model_validate_json((source_root / "budget-authorization.json").read_text())
    paths = _phase_paths(evidence_root)
    evidence_root.mkdir(parents=True)
    shutil.copyfile(source_ledger_path, paths["ledger"])
    ledger = rebind_ledger_authorization(paths["ledger"], previous=previous, replacement=replacement)
    baseline = ledger_fingerprint(ledger)
    allocation = StageCBudgetAllocation(experiment_commit=current_git_commit(root), pricing_snapshot_hash=identities["pricing_snapshot_hash"], baseline_ledger_sha256=baseline, baseline_authorization_hash=authorization_hash(replacement), baseline_spent_cny=ledger.spent_cny, baseline_request_charge_count=len(ledger.request_charges), baseline_settlement_count=len(ledger.settlements), baseline_rebind_count=len(ledger.authorization_rebinds), safety_reserve_cny=Decimal("0.000001"), spendable_total_cny=cap, category_limits_cny={"swe": cap-ledger.spent_cny, "mcp":Decimal("0"), "retention":Decimal("0"), "permission":Decimal("0"), "multi_agent":Decimal("0"), "long_session":Decimal("0")})
    binding = {"schema_version":1,"phase":"phase_1","paid_execution":True,"continuation":True,"authorization_identity":authorization_identity,"approved_commit":current_git_commit(root),**identities,"authorization_cap_cny":str(cap),"next_request_maximum_cny":str(Decimal(stage_c.budget_contract(root)["per_request_maximum_reservation_cny"])),"task_ids":list(stage_c.PHASE_1_IDS),"completed_statuses":{first:first_status},"source_artifact_id":source_artifact_id,"source_archive_sha256":source_archive_sha256,"source_candidate_sha256":_sha256(prediction),"source_evaluator_report_sha256":_sha256(report_path),"source_ledger_sha256":_sha256(source_ledger_path),"budget_authorization_sha256":authorization_hash(replacement)}
    _write_new(paths["binding"], binding); _write_new(paths["budget_authorization"], replacement.model_dump(mode="json")); _write_new(paths["allocation"], allocation.model_dump(mode="json")); _write_new(evidence_root / "recovery-evidence.json", {"schema_version":1,"source_artifact_id":source_artifact_id,"source_archive_sha256":source_archive_sha256,"candidate_sha256":_sha256(prediction),"evaluator_report_sha256":_sha256(report_path),"instance_id":first,"status":first_status,"provider_requests_added":0,"usage_added":0,"charge_added_cny":"0","settlements_added":0,"active_reservation":None})
    return binding


def _load_phase_gate(*, root: Path, evidence_root: Path) -> tuple[dict[str, Any], PaidRunGate]:
    paths = _phase_paths(evidence_root.resolve())
    binding = _json(paths["binding"])
    authorization = BudgetAuthorization.model_validate_json(paths["budget_authorization"].read_text(encoding="utf-8"))
    pricing = load_pricing(root.resolve() / stage_c.PRICING_PATH)
    if authorization.pricing_snapshot_hash != pricing_snapshot_hash(pricing):
        raise ValueError("paid phase pricing does not match the frozen price sheet")
    if binding.get("budget_authorization_sha256") != authorization_hash(authorization):
        raise ValueError("paid phase authorization binding mismatch")
    gate = PaidRunGate(
        root=root.resolve(), authorization_path=paths["budget_authorization"],
        ledger_path=paths["ledger"], allocation_path=paths["allocation"],
        pricing_path=root.resolve() / stage_c.PRICING_PATH, pricing=pricing, stage="C",
    )
    return binding, gate


def _pilot_config() -> PilotConfig:
    return PilotConfig.model_validate({
        "schema_version": 2, "experiment_kind": "pilot", "provider": "bailian-qwen37-max",
        "protocol": "openai-compat",
        "base_url": "https://llm-ipge9fy38w648m28.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "BAILIAN_API_KEY", "model_id": "qwen3.7-max-2026-06-08",
        "fallback_enabled": False,
        "model_parameters": {"temperature": None, "top_p": None, "max_output_tokens": None,
                             "max_completion_tokens": stage_c.MAX_OUTPUT_TOKENS,
                             "enable_thinking": True, "thinking_budget": stage_c.MAX_REASONING_TOKENS},
        "retry_budget": 0, "task_ids": [], "repetitions": 1, "feature_flags": {},
        "experiment_profile": stage_c.stage_c_profile().canonical_payload(), "max_iterations": 50,
    })


def _write_child_config(pilot: PilotConfig, home: Path) -> Path:
    """Create and validate the isolated Agent configuration for one paid task.

    The Stage C child runs from a freshly materialized SWE repository, so it
    must not inherit either a developer's project config or their home config.
    Only the frozen provider identity is written here; the credential remains
    exclusively in the child environment under ``BAILIAN_API_KEY``.
    """
    from codepacex.config import load_config as load_codepacex_config

    payload = _provider_payload(pilot)
    payload["sandbox"] = {"enabled": False, "auto_allow": False, "network_enabled": False}
    config_dir = home / ".codepacex"
    config_dir.mkdir(parents=True)
    target = config_dir / "config.yaml"
    target.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    loaded = load_codepacex_config(target)
    if len(loaded.providers) != 1:
        raise ValueError("generated Stage C child configuration must have one Provider")
    provider = loaded.providers[0]
    if (provider.name, provider.protocol, provider.base_url, provider.model) != (
        pilot.provider, pilot.protocol, pilot.base_url, pilot.model_id,
    ) or loaded.fallback:
        raise ValueError("generated Stage C child configuration changed frozen identity")
    if pilot.retry_budget != 0:
        raise ValueError("Stage C child configuration requires retry=0")
    return target


def _manifest(*, root: Path, binding: Mapping[str, Any], run_id: str, phase: Phase) -> RunManifest:
    profile = stage_c.stage_c_profile()
    return RunManifest(
        experiment_kind="stage-c-goal4-paired-rerun", provider="bailian-qwen37-max",
        protocol="openai-compat", base_url_origin="https://llm-ipge9fy38w648m28.cn-beijing.maas.aliyuncs.com",
        api_key_env="BAILIAN_API_KEY", model_id="qwen3.7-max-2026-06-08", run_id=run_id,
        git_commit=current_git_commit(root), prompt_version="swe-bench-live-inference-v1",
        feature_flags={}, swe_evaluator_architecture="native", experiment_profile=profile.canonical_payload(),
        experiment_profile_hash=profile.profile_hash(), runtime_contract_hash=profile.runtime_contract_hash(),
        benchmark_asset_hash=canonical_hash({"matrix_sha256": binding["matrix_sha256"], "phase": phase}),
        task_ids=list(phase_ids(phase)), repetitions=1,
        model_parameters=_pilot_config().model_parameters.model_dump(mode="json"),
        max_output_tokens=stage_c.MAX_OUTPUT_TOKENS, retry_budget=0, fallback_enabled=False,
        max_iterations=stage_c.MAX_REQUESTS, pricing_snapshot_hash=str(binding["pricing_snapshot_hash"]),
        experiment_config_hash=canonical_hash({"binding": dict(binding), "phase": phase}),
    )


@dataclass(frozen=True)
class TaskExecution:
    stdout: str
    stderr: str
    patch: str
    returncode: int
    evaluator_report: str | None = None
    evaluator_resolved: bool | None = None


TaskExecutor = Callable[[Mapping[str, str], Mapping[str, str], Path], TaskExecution]


def _default_execute(task: Mapping[str, str], environment: Mapping[str, str], workspace: Path) -> TaskExecution:
    _goal3_materialize_instance(dict(task), workspace)
    process = subprocess.run(
        [sys.executable, "-m", "codepacex", "-p", _goal3_inference_prompt(dict(task)),
         "--output-format", "stream-json", "--experiment-profile", environment["CODEPACEX_STAGE_C_PROFILE"],
         "--max-iterations", str(stage_c.MAX_REQUESTS)],
        cwd=workspace, env=dict(environment), text=True, capture_output=True, timeout=1800, check=False,
    )
    return TaskExecution(process.stdout or "", process.stderr or "", _goal3_extract_patch(workspace), process.returncode)


def _default_evaluate(task: Mapping[str, str], execution: TaskExecution, recorder: RunRecorder, run_id: str) -> TaskExecution:
    prediction = recorder.path / f"{hashlib.sha256(task['instance_id'].encode()).hexdigest()}-prediction.json"
    evaluator_id = f"{run_id}-{task['instance_id']}"
    result = run_official_evaluator(
        dataset_name=ENVIRONMENT["dataset"], split=ENVIRONMENT["split"], predictions_path=prediction,
        instance_ids=[task["instance_id"]], max_workers=1, run_id=evaluator_id,
        namespace=ENVIRONMENT["evaluator_namespace"], cwd=recorder.path, evaluator_architecture="native",
    )
    recorder.write_task_artifact(task["instance_id"], "evaluator", (result.stdout or "") + "\n" + (result.stderr or ""))
    if result.returncode:
        raise ValueError(f"official evaluator failed with exit status {result.returncode}")
    report = official_evaluator_report_path(cwd=recorder.path, run_id=evaluator_id,
                                            model_id="qwen3.7-max-2026-06-08", instance_id=task["instance_id"])
    return TaskExecution(execution.stdout, execution.stderr, execution.patch, execution.returncode,
                         report.read_text(encoding="utf-8"), collect_goal3_official_outcome(report, task["instance_id"]))


def _task_record_path(recorder: RunRecorder, instance_id: str) -> Path:
    return recorder.path / f"{hashlib.sha256(instance_id.encode()).hexdigest()}-task-manifest.json"


def _terminal_record(
    *, recorder: RunRecorder, instance_id: str, status: str, accounting: Mapping[str, Any],
    transport_started: bool, references: Mapping[str, str] = {}, error: str | None = None,
) -> dict[str, Any]:
    secret_scan_path = recorder.path / f"{hashlib.sha256(instance_id.encode()).hexdigest()}-secret-scan.json"
    secret_scan_passed = not scan_artifact_roots([recorder.path])
    if not secret_scan_path.exists():
        recorder.write_json(secret_scan_path.name, {"instance_id": instance_id, "passed": secret_scan_passed})
    if status not in {"not_run", "budget_blocked"}:
        # A non-scorable terminal has no evaluator result, but still needs a
        # durable, inspectable absence record before the serial phase may stop.
        marker = recorder.path / f"{hashlib.sha256(instance_id.encode()).hexdigest()}-terminal-absence.json"
        if not marker.exists():
            recorder.write_json(marker.name, {"instance_id": instance_id, "status": status, "error": error})
        required = {
            "prediction_reference", "stdout_reference", "trace_reference",
            "validation_events_reference", "validation_summary_reference", "usage_reference",
            "charge_reference", "settlement_reference", "artifact_reference", "evaluator_report_reference",
        }
        references = {key: references.get(key, marker.name) for key in required}
    record = {
        "schema_version": 1, "instance_id": instance_id, "status": status,
        "transport_started": transport_started, "budget_blocked": status == "budget_blocked",
        "provider_requests": accounting.get("request_count", 0),
        "active_reservation": accounting.get("active_reservation"),
        "combined_conservative_consumption_cny": accounting.get("combined_conservative_consumption_cny", accounting.get("actual_cny", "0")),
        "secret_scan_passed": secret_scan_passed,
        "secret_scan_reference": secret_scan_path.name,
        **references,
    }
    if error:
        record["error"] = error
    _write_new(_task_record_path(recorder, instance_id), record)
    return record


def _task_trial_id(run_id: str, phase: Phase, instance_id: str) -> str:
    return f"swe/stage-c/{run_id}/{phase}/1/{instance_id}"


def execute_phase(
    *, root: Path, freeze_dir: Path, evidence_root: Path, phase: Phase, run_id: str,
    task_bundle: Path, confirmed: bool, executor: TaskExecutor | None = None,
    evaluator: Callable[[Mapping[str, str], TaskExecution, RunRecorder, str], TaskExecution] | None = None,
    completed_statuses: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run one authorized phase serially, stopping at the first non-scorable terminal.

    Tests inject deterministic executors/evaluators.  The default path is the
    real CodePaceX subprocess and the frozen official evaluator, and is never
    reached without ``confirmed`` and a present Provider secret.
    """
    if not confirmed:
        raise ValueError("Stage C paid execution requires --confirm-paid-run")
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("invalid Stage C paid run ID")
    root, evidence_root = root.resolve(), evidence_root.resolve()
    formal_stage_c_trial = executor is None
    binding, gate = _load_phase_gate(root=root, evidence_root=evidence_root)
    if binding.get("phase") != phase or current_git_commit(root) != binding.get("approved_commit"):
        raise ValueError("paid phase binding does not match this checkout")
    identities = frozen_identities(root, freeze_dir)
    if any(binding.get(key) != identities[key] for key in identities):
        raise ValueError("paid phase identity differs from the committed Freeze")
    tasks = load_agent_task_bundle(task_bundle.resolve(), phase=phase)
    completed_statuses = dict(completed_statuses or binding.get("completed_statuses", {}))
    if completed_statuses and (phase != "phase_1" or set(completed_statuses) != {stage_c.PHASE_1_IDS[0]} or completed_statuses[stage_c.PHASE_1_IDS[0]] not in SCORABLE):
        raise ValueError("Phase 1 continuation must bind exactly one scorable first-task recovery")
    if phase == "phase_2":
        if not binding.get("phase_1_artifact_manifest"):
            raise ValueError("Phase 2 binding lacks Phase 1 Artifact")
        validate_phase_1_artifact(Path(str(binding["phase_1_artifact_manifest"])))
    if executor is None and not os.environ.get("BAILIAN_API_KEY"):
        raise ValueError("Stage C paid execution requires the configured Provider secret")
    if executor is None:
        require_native_preflight(root=root)
    recorder = RunRecorder(evidence_root, _manifest(root=root, binding=binding, run_id=run_id, phase=phase),
                           run_id=run_id, repo_root=root, secrets=_runtime_secrets(_pilot_config()))
    profile_path: Path | None = None
    with tempfile.TemporaryDirectory(prefix="codepacex-stage-c-home-") as home_text:
        home = Path(home_text)
        profile_path = home / "profile.yaml"
        profile_path.write_text(yaml.safe_dump(stage_c.stage_c_profile().canonical_payload(), sort_keys=True), encoding="utf-8")
        _write_child_config(_pilot_config(), home)
        # Synthetic deterministic executors never enter a Provider transport and
        # therefore must not require, read, or manufacture a Provider secret.
        # The real subprocess path is guarded by the explicit secret-presence
        # check above and alone receives the Provider child environment.
        base_environment = (
            _child_environment(_pilot_config(), home_text, root=root)
            if executor is None else {}
        )
        for task in tasks[len(completed_statuses):]:
            instance_id = task["instance_id"]
            trial_id = _task_trial_id(run_id, phase, instance_id)
            accounting: dict[str, Any] = {"trial_id": trial_id, "request_count": 0, "actual_cny": "0"}
            terminal: dict[str, Any] | None = None
            recorder.event("trial_started", {"task_id": instance_id, "repetition_id": "1", "attempt_id": 1,
                                              "trial_id": trial_id, "phase": phase, "budget_mode": "rolling_per_request"})
            allowed, reason = stage_c.admit_task(
                phase=phase, completed_terminal_ids=(stage_c.PHASE_1_IDS if phase == "phase_2" else ()), phase_conservative_consumption=Decimal(str(gate.summary()["spent_cny"])),
                active_reservation=bool(gate.summary()["active_reservation"]),
                authorization_cap=Decimal(str(binding["authorization_cap_cny"])),
                next_request_maximum=Decimal(str(binding["next_request_maximum_cny"])),
            )
            if not allowed:
                terminal = _terminal_record(recorder=recorder, instance_id=instance_id, status="budget_blocked", accounting=accounting,
                                            transport_started=False, error=reason)
            else:
                try:
                    with tempfile.TemporaryDirectory(prefix=f"codepacex-stage-c-{instance_id}-") as temp_text:
                        workspace = Path(temp_text) / "repo"
                        task_venv = Path(temp_text) / "tool-venv"
                        child_environment = dict(base_environment)
                        child_environment.update(provider_request_budget_environment(
                            gate, trial_id=trial_id, maximum_input_tokens_per_request=stage_c.MAX_INPUT_TOKENS,
                            maximum_output_tokens_per_request=stage_c.MAX_OUTPUT_TOKENS,
                            maximum_reasoning_tokens_per_request=stage_c.MAX_REASONING_TOKENS,
                            maximum_provider_requests_per_trial=stage_c.MAX_REQUESTS,
                        ))
                        child_environment["CODEPACEX_STAGE_C_PROFILE"] = str(profile_path)
                        if executor is None:
                            venv.EnvBuilder(with_pip=True, system_site_packages=True).create(task_venv)
                            bin_dir = task_venv / ("Scripts" if os.name == "nt" else "bin")
                            child_environment["VIRTUAL_ENV"] = str(task_venv)
                            child_environment["PATH"] = str(bin_dir) + os.pathsep + child_environment.get("PATH", "")
                            child_environment["PYTHONNOUSERSITE"] = "1"
                        execution = (executor or _default_execute)(task, child_environment, workspace)
                    stdout = recorder.write_task_artifact(instance_id, "stdout", execution.stdout)
                    recorder.write_task_artifact(instance_id, "stderr", execution.stderr)
                    accounting = gate.trial_accounting(trial_id)
                    if accounting.get("budget_blocked"):
                        terminal = _terminal_record(recorder=recorder, instance_id=instance_id, status="budget_blocked", accounting=accounting,
                                                    transport_started=False, error="budget_blocked_before_transport")
                    elif accounting.get("active_reservation") is not None or accounting.get("provider_usage_contract_violation"):
                        terminal = _terminal_record(recorder=recorder, instance_id=instance_id, status="infrastructure_error", accounting=accounting,
                                                    transport_started=True, error="unclosed_reservation_or_usage_contract")
                    else:
                        trace_requests, _trace_input, _trace_output = trace_usage(execution.stdout)
                        if formal_stage_c_trial and (
                            trace_requests == 0
                            or trace_requests != accounting.get("request_count")
                            or trace_requests > stage_c.MAX_REQUESTS
                        ):
                            terminal = _terminal_record(
                                recorder=recorder, instance_id=instance_id, status="infrastructure_error",
                                accounting=accounting, transport_started=True,
                                error="missing_or_mismatched_trace_usage",
                            )
                        else:
                            with tempfile.NamedTemporaryFile("w", suffix=".ndjson", encoding="utf-8") as trace:
                                trace.write(execution.stdout)
                                trace.flush()
                                _ingest_trace(recorder, Path(trace.name), instance_id, "1", 1)
                            prediction = recorder.path / f"{hashlib.sha256(instance_id.encode()).hexdigest()}-prediction.json"
                            recorder.write_json(prediction.name, [{"instance_id": instance_id, "model_name_or_path": "qwen3.7-max-2026-06-08", "model_patch": execution.patch}])
                            if execution.returncode:
                                terminal = _terminal_record(recorder=recorder, instance_id=instance_id, status="infrastructure_error", accounting=accounting,
                                                            transport_started=True, references={"prediction_reference": prediction.name, "stdout_reference": stdout.name}, error="agent_process_failed")
                            else:
                                evaluated = (evaluator or _default_evaluate)(task, execution, recorder, run_id)
                                if evaluated.evaluator_report is None or evaluated.evaluator_resolved is None:
                                    raise ValueError("official evaluator did not produce a terminal report")
                                report = recorder.write_task_artifact(instance_id, "evaluator_report", evaluated.evaluator_report)
                                terminal = _terminal_record(
                                    recorder=recorder, instance_id=instance_id,
                                    status="resolved" if evaluated.evaluator_resolved else "unresolved", accounting=accounting,
                                    transport_started=True,
                                    references={"prediction_reference": prediction.name, "stdout_reference": stdout.name,
                                                "trace_reference": stdout.name, "validation_events_reference": "validation-events.jsonl",
                                                "validation_summary_reference": "validation-summary.json", "usage_reference": "usage.json",
                                                "charge_reference": "terminal-ledger.json", "settlement_reference": "terminal-ledger.json",
                                                "artifact_reference": "task-artifacts.json", "evaluator_report_reference": report.name},
                                )
                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                    accounting = gate.trial_accounting(trial_id)
                    terminal = _terminal_record(recorder=recorder, instance_id=instance_id, status="infrastructure_error", accounting=accounting,
                                                transport_started=True, error=str(exc))
            assert terminal is not None
            # An unknown-Usage transport failure intentionally retains its
            # reservation for a separately authorized reconciliation.  It is a
            # durable stopping record, not admissible evidence for another task.
            if terminal["active_reservation"] is None:
                stage_c.validate_terminal_evidence(terminal)
            recorder.event("trial_completed", {"task_id": instance_id, "repetition_id": "1", "attempt_id": 1,
                                                "trial_id": trial_id, "status": terminal["status"],
                                                "provider_request_count": terminal["provider_requests"]})
            if terminal["status"] not in SCORABLE:
                break
    statuses = {
        **completed_statuses,
        **{
            task["instance_id"]: _json(_task_record_path(recorder, task["instance_id"]))["status"]
            for task in tasks if _task_record_path(recorder, task["instance_id"]).exists()
        },
    }
    statuses = {**statuses, **{item: "not_run" for item in phase_ids(phase) if item not in statuses}}
    if phase == "phase_2" and formal_stage_c_trial and all(value in SCORABLE for value in statuses.values()):
        report = compile_full_paired_result(
            freeze_dir=freeze_dir, phase_1_artifact=Path(str(binding["phase_1_artifact_manifest"])),
            phase_2_statuses=statuses,
        )
    else:
        report = stage_c.compile_paired_claim(matrix=_json(freeze_dir / "stage_c_matrix.json"), baseline=_json(freeze_dir / "stage_c_baseline.json"), stage_results=statuses)
    _write_new(_phase_paths(evidence_root)["report"], report)
    ledger = BudgetLedger.model_validate_json(_phase_paths(evidence_root)["ledger"].read_text(encoding="utf-8"))
    artifact = {
        "schema_version": 1, "artifact_id": f"stage-c-{phase}-{run_id}", "phase": phase, "run_id": run_id, "task_ids": list(phase_ids(phase)),
        "phase_1_instance_ids": list(stage_c.PHASE_1_IDS), "phase_2_instance_ids": list(stage_c.PHASE_2_IDS),
        "terminal_statuses": statuses, "active_reservation": ledger.active_reservation.model_dump(mode="json") if ledger.active_reservation else None,
        "combined_conservative_consumption_cny": str(ledger.spent_cny),
        "ledger_sha256": _sha256(_phase_paths(evidence_root)["ledger"]), "report_sha256": _sha256(_phase_paths(evidence_root)["report"]),
        "provider_requests": len(ledger.request_charges), "formal_stage_c_trial": formal_stage_c_trial,
    }
    metric_ledgers = [_phase_paths(evidence_root)["ledger"]]
    if phase == "phase_2":
        metric_ledgers.insert(0, Path(str(binding["phase_1_artifact_manifest"])).parent / "terminal-ledger.json")
    artifact["process_metrics"] = process_metrics(ledgers=metric_ledgers)
    _write_new(_phase_paths(evidence_root)["manifest"], artifact)
    result_status = (
        "success" if all(value in SCORABLE for value in statuses.values())
        else "budget_blocked" if any(value == "budget_blocked" for value in statuses.values())
        else "infrastructure_error"
    )
    recorder.finalize({"status": result_status,
                       "phase": phase, "formal_stage_c_trial": formal_stage_c_trial})
    return artifact


def validate_phase_1_artifact(path: Path) -> dict[str, Any]:
    artifact = _json(path)
    if artifact.get("phase") != "phase_1" or tuple(artifact.get("task_ids", ())) != stage_c.PHASE_1_IDS:
        raise ValueError("Phase 1 Artifact does not bind the frozen six-task prefix")
    if artifact.get("formal_stage_c_trial") is not True:
        raise ValueError("Phase 2 requires a formal (not synthetic) Phase 1 Artifact")
    if tuple(artifact.get("phase_1_instance_ids", ())) != stage_c.PHASE_1_IDS or tuple(artifact.get("phase_2_instance_ids", ())) != stage_c.PHASE_2_IDS:
        raise ValueError("Phase 1 Artifact does not bind the frozen 6/14 continuation matrix")
    statuses = artifact.get("terminal_statuses")
    if not isinstance(statuses, dict) or set(statuses) != set(stage_c.PHASE_1_IDS) or any(value not in SCORABLE for value in statuses.values()):
        raise ValueError("Phase 2 requires six scorable Phase 1 outcomes")
    if artifact.get("active_reservation") is not None:
        raise ValueError("Phase 1 Artifact has an active reservation")
    if not _SHA256.fullmatch(str(artifact.get("ledger_sha256", ""))) or not _SHA256.fullmatch(str(artifact.get("report_sha256", ""))):
        raise ValueError("Phase 1 Artifact has incomplete immutable evidence hashes")
    return artifact


def compile_full_paired_result(*, freeze_dir: Path, phase_1_artifact: Path,
                               phase_2_statuses: Mapping[str, str]) -> dict[str, Any]:
    """Compile a paired Claim only from the exact two frozen phase manifests."""
    phase_1 = validate_phase_1_artifact(phase_1_artifact)
    if set(phase_2_statuses) != set(stage_c.PHASE_2_IDS):
        raise ValueError("full paired compiler requires the exact fourteen Phase 2 terminals")
    statuses = {**dict(phase_1["terminal_statuses"]), **dict(phase_2_statuses)}
    report = stage_c.compile_paired_claim(
        matrix=_json(freeze_dir / "stage_c_matrix.json"),
        baseline=_json(freeze_dir / "stage_c_baseline.json"), stage_results=statuses,
    )
    if not report["full_claim"] or report["scorable_denominator"] != 20:
        raise ValueError("a partial Stage C phase cannot produce a full paired Claim")
    return report


def process_metrics(*, ledgers: Sequence[Path]) -> dict[str, Any]:
    """Extract only observed process/accounting totals; never score an outcome."""
    models = [BudgetLedger.model_validate_json(path.read_text(encoding="utf-8")) for path in ledgers]
    if any(ledger.active_reservation is not None for ledger in models):
        raise ValueError("process metrics require closed active reservations")
    return {
        "provider_requests": sum(len(ledger.request_charges) for ledger in models),
        "verified_cost_cny": str(_money(sum((ledger.spent_cny for ledger in models), Decimal("0")))),
        "settlement_count": sum(len(ledger.settlements) for ledger in models),
        "budget_block_count": sum(len(ledger.budget_blocks) for ledger in models),
        "request_ceiling_block_count": sum(len(ledger.provider_request_ceiling_blocks) for ledger in models),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Explicitly authorized Stage C paid execution")
    parser.add_argument("command", choices=["identities", "build-task-bundle", "prepare-phase", "prepare-phase1-continuation", "validate-phase-1", "execute-phase"])
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--freeze-dir", type=Path, default=Path("evals/stage_c"))
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--phase", choices=["phase_1", "phase_2"])
    parser.add_argument("--approved-commit", default="")
    parser.add_argument("--authorization-identity", default="")
    parser.add_argument("--freeze-sha256", default="")
    parser.add_argument("--pricing-sha256", default="")
    parser.add_argument("--phase-1-consumption-cny", default="0")
    parser.add_argument("--phase-1-artifact", type=Path)
    parser.add_argument("--phase-1-artifact-id", default="")
    parser.add_argument("--phase-1-archive-sha256", default="")
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--source-artifact-id", default="")
    parser.add_argument("--source-archive-sha256", default="")
    parser.add_argument("--task-bundle", type=Path)
    parser.add_argument("--source-dataset", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--confirm-paid-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "identities":
            result = frozen_identities(args.root, args.freeze_dir)
        elif args.command == "build-task-bundle":
            if args.source_dataset is None or args.output is None or args.phase is None:
                raise ValueError("--source-dataset, --output, and --phase are required")
            result = build_agent_task_bundle(source_dataset=args.source_dataset, output=args.output, phase=args.phase)
        elif args.command == "validate-phase-1":
            if args.phase_1_artifact is None:
                raise ValueError("--phase-1-artifact is required")
            result = validate_phase_1_artifact(args.phase_1_artifact)
        elif args.command == "prepare-phase":
            if args.evidence_root is None or args.phase is None:
                raise ValueError("--evidence-root and --phase are required")
            result = prepare_phase(root=args.root, freeze_dir=args.freeze_dir, evidence_root=args.evidence_root,
                                   phase=args.phase, approved_commit=args.approved_commit,
                                   authorization_identity=args.authorization_identity,
                                   supplied_freeze_sha256=args.freeze_sha256, supplied_pricing_hash=args.pricing_sha256,
                                   phase_1_conservative_consumption=Decimal(args.phase_1_consumption_cny),
                                   phase_1_artifact=args.phase_1_artifact,
                                   phase_1_artifact_id=args.phase_1_artifact_id or None,
                                   phase_1_archive_sha256=args.phase_1_archive_sha256 or None)
        elif args.command == "prepare-phase1-continuation":
            if args.evidence_root is None or args.source_root is None:
                raise ValueError("--evidence-root and --source-root are required")
            result = prepare_phase_one_continuation(
                root=args.root, freeze_dir=args.freeze_dir, evidence_root=args.evidence_root,
                source_root=args.source_root, source_artifact_id=args.source_artifact_id,
                source_archive_sha256=args.source_archive_sha256,
                authorization_identity=args.authorization_identity,
            )
        else:
            if args.evidence_root is None or args.phase is None or args.task_bundle is None:
                raise ValueError("--evidence-root, --phase, and --task-bundle are required")
            result = execute_phase(root=args.root, freeze_dir=args.freeze_dir, evidence_root=args.evidence_root,
                                   phase=args.phase, run_id=args.run_id, task_bundle=args.task_bundle,
                                   confirmed=args.confirm_paid_run)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Stage C paid execution error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Explicit, isolated paid execution for the frozen Stage D.1 one-task canary.

This module is intentionally not imported by the Agent runtime or by the
zero-provider Freeze.  It creates evidence only under a caller-supplied Stage
D root and accepts exactly the two pre-registered canary instances.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import yaml

from codepacex.agent import Agent
from codepacex.client import create_client
from codepacex.config import load_config
from codepacex.tools import create_default_registry
from evals.benchmark import RunManifest, RunRecorder, canonical_hash, current_git_commit
from evals.costing import load_pricing, pricing_snapshot_hash
from evals.paid_gate import (
    BudgetAuthorization,
    BudgetLedger,
    PaidRunGate,
    STAGE_D1_CANARY_BUDGET_STAGE,
    provider_request_budget_environment,
    worst_case_reservation,
)
from evals.permission_study import trace_usage
from evals import stage_c_paid, stage_d1_freeze


PRICING_PATH = Path("evals/goal2/pricing_bailian_qwen37_max_2026-07-13.json")
MAX_REQUESTS = 40
MAX_INPUT_TOKENS = 128_000
MAX_OUTPUT_TOKENS = 8_192
MAX_REASONING_TOKENS = 6_144
AUTHORIZATION_CAP = Decimal("15")
BUDGET_STAGE_KEY = STAGE_D1_CANARY_BUDGET_STAGE
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_new(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        raise ValueError(f"refusing to overwrite Stage D.1 evidence: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path.name}")
    return value


def _paths(evidence_root: Path) -> dict[str, Path]:
    return {
        "binding": evidence_root / "canary-authorization.json",
        "authorization": evidence_root / "budget-authorization.json",
        "ledger": evidence_root / "terminal-ledger.json",
        "report": evidence_root / "canary-report.json",
        "manifest": evidence_root / "canary-artifact-manifest.json",
    }


def _validate_task(task: Any) -> dict[str, Any]:
    return stage_c_paid._validated_agent_task(task)


def load_task_bundle(path: Path) -> list[dict[str, Any]]:
    rows = [_validate_task(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = tuple(str(row["instance_id"]) for row in rows)
    if ids != stage_d1_freeze.CANARY_INSTANCE_IDS or len(set(ids)) != 1:
        raise ValueError("Stage D.1 canary bundle differs from the frozen one-task order")
    rendered = json.dumps(rows, sort_keys=True).lower()
    if any(marker in rendered for marker in ("\"patch\"", "goal4_status", "taxonomy", "evaluator_report")):
        raise ValueError("Stage D.1 canary bundle contains forbidden historical or gold information")
    return rows


def build_task_bundle(*, source_dataset: Path, output: Path) -> dict[str, Any]:
    if output.exists():
        raise ValueError("refusing to overwrite Stage D.1 task bundle")
    selected: dict[str, dict[str, Any]] = {}
    for line in source_dataset.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        task = _validate_task(json.loads(line))
        instance_id = str(task["instance_id"])
        if instance_id in stage_d1_freeze.CANARY_INSTANCE_IDS:
            if instance_id in selected:
                raise ValueError("source Dataset contains a duplicate Stage D.1 canary task")
            selected[instance_id] = task
    if tuple(selected) != stage_d1_freeze.CANARY_INSTANCE_IDS:
        raise ValueError("source Dataset does not contain the exact frozen Stage D.1 canary")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(selected[item], sort_keys=True) + "\n" for item in stage_d1_freeze.CANARY_INSTANCE_IDS), encoding="utf-8")
    load_task_bundle(output)
    return {"task_ids": list(stage_d1_freeze.CANARY_INSTANCE_IDS), "tasks_sha256": _sha256(output)}


def frozen_identities(root: Path, freeze_path: Path) -> dict[str, str]:
    freeze = stage_d1_freeze.validate_freeze(root.resolve(), freeze_path.resolve())
    pricing = load_pricing(root / PRICING_PATH)
    return {
        # Bind the exact committed Freeze bytes published in the Freeze report,
        # not a second canonicalization of the same JSON payload.
        "freeze_sha256": _sha256(freeze_path),
        "runtime_contract_hash": str(freeze["runtime_contract_hash"]),
        "pricing_snapshot_hash": pricing_snapshot_hash(pricing),
    }


def _authorization(*, pricing_hash: str, approved_commit: str) -> BudgetAuthorization:
    return BudgetAuthorization(
        authorized_total_cny=AUTHORIZATION_CAP,
        stage_limits_cny={BUDGET_STAGE_KEY: AUTHORIZATION_CAP},
        pricing_snapshot_hash=pricing_hash,
        experiment_commit=approved_commit,
        authorized_at=_utc_now(),
    )


def _paid_path_preflight(*, root: Path, pricing: Any, authorization: BudgetAuthorization,
                         evidence_root: Path | None) -> dict[str, Any]:
    """Exercise the first reservation boundary without allowing transport."""
    with tempfile.TemporaryDirectory(prefix="codepacex-stage-d1-paid-path-") as temp_text:
        working_root = Path(temp_text)
        authorization_path = working_root / "budget-authorization.json"
        ledger_path = working_root / "terminal-ledger.json"
        _write_new(authorization_path, authorization.model_dump(mode="json"))
        _write_new(ledger_path, BudgetLedger(
            authorization_hash=stage_c_paid.authorization_hash(authorization), updated_at=_utc_now(),
        ).model_dump(mode="json"))
        gate = PaidRunGate(
            root=root, authorization_path=authorization_path, ledger_path=ledger_path,
            pricing=pricing, pricing_path=root / PRICING_PATH, stage=BUDGET_STAGE_KEY,
        )
        reservation = gate.reserve(
            "stage-d1-paid-path-preflight", maximum_requests=1,
            maximum_input_tokens_per_request=MAX_INPUT_TOKENS,
            maximum_output_tokens_per_request=MAX_OUTPUT_TOKENS,
        )
        settlement = gate.cancel(reservation, reason="provider_confirmed_not_submitted")
        ledger = BudgetLedger.model_validate_json(ledger_path.read_text(encoding="utf-8"))
        if ledger.active_reservation is not None or ledger.request_charges or ledger.spent_cny != 0:
            raise ValueError("Stage D.1 paid-path preflight did not close at zero cost")
        result = {
            "budget_stage_key": BUDGET_STAGE_KEY,
            "preflight_reservation_cny": str(reservation.reserved_cny),
            "preflight_cancellation_status": settlement.status,
            "preflight_cancellation_settlement_cny": str(settlement.actual_cny),
            "preflight_active_reservation": None,
            "preflight_provider_requests": 0,
            "preflight_usage": 0,
            "preflight_charge": "0",
            "preflight_verified_cost_cny": "0",
            "preflight_ledger_sha256": _sha256(ledger_path),
        }
        if evidence_root is not None:
            evidence_root.mkdir(parents=True, exist_ok=True)
            _write_new(evidence_root / "paid-path-preflight-authorization.json", authorization.model_dump(mode="json"))
            _write_new(evidence_root / "paid-path-preflight-ledger.json", ledger.model_dump(mode="json"))
            _write_new(evidence_root / "paid-path-preflight.json", result)
        return result


def preflight(*, root: Path, freeze_path: Path, approved_commit: str,
              supplied_freeze_sha256: str, supplied_runtime_contract_hash: str,
              supplied_pricing_hash: str, require_secret: bool,
              evidence_root: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    if not _COMMIT.fullmatch(approved_commit) or current_git_commit(root) != approved_commit:
        raise ValueError("Stage D.1 paid canary must use the approved immutable checkout")
    identities = frozen_identities(root, freeze_path)
    supplied = (supplied_freeze_sha256, supplied_runtime_contract_hash, supplied_pricing_hash)
    if not all(_SHA256.fullmatch(item) for item in supplied):
        raise ValueError("Stage D.1 immutable identities must be SHA-256")
    if supplied_freeze_sha256 != identities["freeze_sha256"] or supplied_runtime_contract_hash != identities["runtime_contract_hash"] or supplied_pricing_hash != identities["pricing_snapshot_hash"]:
        raise ValueError("Stage D.1 paid canary identities differ from the frozen checkout")
    pricing = load_pricing(root / PRICING_PATH)
    next_request = worst_case_reservation(pricing, maximum_requests=1,
                                          maximum_input_tokens_per_request=MAX_INPUT_TOKENS,
                                          maximum_output_tokens_per_request=MAX_OUTPUT_TOKENS)
    if next_request > AUTHORIZATION_CAP:
        raise ValueError("the next maximum Provider request exceeds the CNY 15 authorization cap")
    if require_secret and "BAILIAN_API_KEY" not in os.environ:
        raise ValueError("Stage D.1 paid canary requires the configured Provider secret")
    paid_path = _paid_path_preflight(
        root=root, pricing=pricing,
        authorization=_authorization(pricing_hash=identities["pricing_snapshot_hash"], approved_commit=approved_commit),
        evidence_root=evidence_root,
    )
    # This loads the generated isolated config without reading a credential or sending transport.
    with tempfile.TemporaryDirectory(prefix="codepacex-stage-d1-preflight-") as home_text:
        home = Path(home_text)
        config = stage_c_paid._write_child_config(stage_c_paid._pilot_config(), home)
        if not config.exists():
            raise ValueError("Stage D.1 isolated Agent configuration was not created")
        # This is the exact startup boundary before Provider transport.  The
        # client is constructed but never asked to stream or complete.
        if "BAILIAN_API_KEY" in os.environ:
            workspace = home / "fresh-workspace"
            workspace.mkdir()
            loaded = load_config(config)
            provider = loaded.providers[0]
            agent = Agent(client=create_client(provider, max_retries=0), registry=create_default_registry(),
                          protocol=provider.protocol, work_dir=str(workspace), max_iterations=MAX_REQUESTS,
                          active_provider=provider, providers=loaded.providers, fallback=loaded.fallback,
                          experiment_profile=stage_d1_freeze.stage_d1_profile())
            if agent.max_iterations != MAX_REQUESTS or agent.active_provider is not provider:
                raise ValueError("Stage D.1 Agent startup differs from the frozen contract")
    return {
        "provider_requests": 0, "usage": 0, "charge": "0", "settlement": "0",
        "active_reservation": None, "approved_commit": approved_commit,
        "budget_stage_key": BUDGET_STAGE_KEY, **identities, **paid_path,
        "next_request_maximum_cny": str(next_request),
        "theoretical_full_path_maximum_cny": str(next_request * MAX_REQUESTS * len(stage_d1_freeze.CANARY_INSTANCE_IDS)),
        "authorization_cap_cny": str(AUTHORIZATION_CAP),
    }


def prepare(*, root: Path, freeze_path: Path, evidence_root: Path, approved_commit: str,
            authorization_identity: str, supplied_freeze_sha256: str,
            supplied_runtime_contract_hash: str, supplied_pricing_hash: str) -> dict[str, Any]:
    check = preflight(root=root, freeze_path=freeze_path, approved_commit=approved_commit,
                      supplied_freeze_sha256=supplied_freeze_sha256,
                      supplied_runtime_contract_hash=supplied_runtime_contract_hash,
                      supplied_pricing_hash=supplied_pricing_hash, require_secret=False)
    if not authorization_identity.strip():
        raise ValueError("a separate Stage D.1 authorization identity is required")
    paths = _paths(evidence_root.resolve())
    if any(path.exists() for path in paths.values()):
        raise ValueError("Stage D.1 evidence root is already initialized")
    authorization = _authorization(pricing_hash=check["pricing_snapshot_hash"], approved_commit=approved_commit)
    binding = {
        "schema_version": 1, "stage": "D.1", "experiment_kind": "stage-d1-live-tool-protocol-canary",
        "authorization_identity": authorization_identity, "approved_commit": approved_commit,
        "task_ids": list(stage_d1_freeze.CANARY_INSTANCE_IDS), "strict_serial": True,
        "maximum_requests_per_instance": MAX_REQUESTS, "fallback_enabled": False,
        "automatic_retry": 0, "one_formal_candidate_per_instance": True,
        "rolling_per_request_reservation": True, "authorization_cap_cny": str(AUTHORIZATION_CAP),
        "budget_stage_key": BUDGET_STAGE_KEY,
        "budget_authorization_sha256": stage_c_paid.authorization_hash(authorization), **check,
    }
    _write_new(paths["authorization"], authorization.model_dump(mode="json"))
    _write_new(paths["binding"], binding)
    _write_new(paths["ledger"], BudgetLedger(
        authorization_hash=stage_c_paid.authorization_hash(authorization), updated_at=_utc_now(),
    ).model_dump(mode="json"))
    return binding


def _gate(*, root: Path, evidence_root: Path) -> tuple[dict[str, Any], PaidRunGate]:
    paths = _paths(evidence_root)
    binding = _json(paths["binding"])
    authorization = BudgetAuthorization.model_validate_json(paths["authorization"].read_text(encoding="utf-8"))
    if binding.get("budget_authorization_sha256") != stage_c_paid.authorization_hash(authorization):
        raise ValueError("Stage D.1 authorization binding mismatch")
    if binding.get("budget_stage_key") != BUDGET_STAGE_KEY or set(authorization.stage_limits_cny) != {BUDGET_STAGE_KEY}:
        raise ValueError("Stage D.1 authorization does not use the registered budget stage key")
    gate = PaidRunGate(root=root.resolve(), authorization_path=paths["authorization"], ledger_path=paths["ledger"],
                       pricing=load_pricing(root / PRICING_PATH), pricing_path=root / PRICING_PATH, stage=BUDGET_STAGE_KEY)
    return binding, gate


@dataclass(frozen=True)
class TaskExecution:
    stdout: str
    stderr: str
    patch: str
    returncode: int
    evaluator_report: str | None = None
    evaluator_resolved: bool | None = None


def _execute(task: Mapping[str, Any], environment: Mapping[str, str], workspace: Path) -> TaskExecution:
    stage_c_paid._goal3_materialize_instance(dict(task), workspace)
    process = subprocess.run([sys.executable, "-m", "codepacex", "-p", stage_c_paid._goal3_inference_prompt(dict(task)),
                              "--output-format", "stream-json", "--experiment-profile", environment["CODEPACEX_STAGE_D_PROFILE"],
                              "--max-iterations", str(MAX_REQUESTS)], cwd=workspace, env=dict(environment), text=True,
                             capture_output=True, timeout=1800, check=False)
    return TaskExecution(process.stdout or "", process.stderr or "", stage_c_paid._goal3_extract_patch(workspace), process.returncode)


def _observations(stdout: str) -> dict[str, Any]:
    names: list[str] = []
    checkpoints: list[int] = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        name = event.get("tool_name") or event.get("name")
        if isinstance(name, str):
            names.append(name)
        if name == "ValidationCheckpoint":
            ordinal = event.get("checkpoint_ordinal")
            if isinstance(ordinal, int):
                checkpoints.append(ordinal)
    return {"tool_names": names, "run_test_executed": "RunTest" in names,
            "edit_or_write_executed": any(name in {"EditFile", "WriteFile"} for name in names),
            "checkpoints": checkpoints}


def _manifest(root: Path, binding: Mapping[str, Any], run_id: str) -> RunManifest:
    profile = stage_d1_freeze.stage_d1_profile()
    return RunManifest(experiment_kind="stage-d1-live-tool-protocol-canary", provider="bailian-qwen37-max",
        protocol="openai-compat", base_url_origin="https://llm-ipge9fy38w648m28.cn-beijing.maas.aliyuncs.com",
        api_key_env="BAILIAN_API_KEY", model_id="qwen3.7-max-2026-06-08", run_id=run_id,
        git_commit=current_git_commit(root), prompt_version="swe-bench-live-inference-v1", feature_flags={},
        swe_evaluator_architecture="native", experiment_profile=profile.canonical_payload(),
        experiment_profile_hash=profile.profile_hash(), runtime_contract_hash=profile.runtime_contract_hash(),
        benchmark_asset_hash=canonical_hash({"freeze_sha256": binding["freeze_sha256"], "tasks": binding["task_ids"]}),
        task_ids=list(stage_d1_freeze.CANARY_INSTANCE_IDS), repetitions=1,
        model_parameters=stage_c_paid._pilot_config().model_parameters.model_dump(mode="json"),
        max_output_tokens=MAX_OUTPUT_TOKENS, retry_budget=0, fallback_enabled=False, max_iterations=MAX_REQUESTS,
        pricing_snapshot_hash=str(binding["pricing_snapshot_hash"]), experiment_config_hash=canonical_hash(dict(binding)))


def execute(*, root: Path, freeze_path: Path, evidence_root: Path, run_id: str, task_bundle: Path,
            confirmed: bool, executor: Callable[[Mapping[str, Any], Mapping[str, str], Path], TaskExecution] | None = None,
            evaluator: Callable[[Mapping[str, Any], TaskExecution, RunRecorder, str], TaskExecution] | None = None) -> dict[str, Any]:
    if not confirmed or not _RUN_ID.fullmatch(run_id):
        raise ValueError("Stage D.1 paid canary requires an explicit valid Run ID")
    root, evidence_root = root.resolve(), evidence_root.resolve()
    binding, gate = _gate(root=root, evidence_root=evidence_root)
    preflight(root=root, freeze_path=freeze_path, approved_commit=str(binding["approved_commit"]),
              supplied_freeze_sha256=str(binding["freeze_sha256"]),
              supplied_runtime_contract_hash=str(binding["runtime_contract_hash"]),
              supplied_pricing_hash=str(binding["pricing_snapshot_hash"]), require_secret=executor is None)
    tasks = load_task_bundle(task_bundle)
    if executor is None and "BAILIAN_API_KEY" not in os.environ:
        raise ValueError("Stage D.1 paid canary requires a configured Provider secret")
    recorder = RunRecorder(evidence_root, _manifest(root, binding, run_id), run_id=run_id, repo_root=root,
                           secrets=stage_c_paid._runtime_secrets(stage_c_paid._pilot_config()))
    statuses: dict[str, str] = {}
    task_records: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="codepacex-stage-d1-home-") as home_text:
        home = Path(home_text)
        profile_path = home / "stage-d1-profile.yaml"
        profile_path.write_text(yaml.safe_dump(stage_d1_freeze.stage_d1_profile().canonical_payload(), sort_keys=True), encoding="utf-8")
        stage_c_paid._write_child_config(stage_c_paid._pilot_config(), home)
        base_environment = stage_c_paid._child_environment(stage_c_paid._pilot_config(), home_text, root=root) if executor is None else {}
        if executor is None:
            # The orchestration checkout may be newer, but the Agent process
            # itself must import the exact immutable Freeze checkout.
            base_environment["PYTHONPATH"] = str(root)
        for task in tasks:
            instance_id = str(task["instance_id"])
            trial_id = f"swe/stage-d1/{run_id}/canary/1/{instance_id}"
            terminal = "infrastructure_error"
            error: str | None = None
            execution: TaskExecution | None = None
            try:
                with tempfile.TemporaryDirectory(prefix="codepacex-stage-d1-task-") as temp_text:
                    environment = dict(base_environment)
                    environment.update(provider_request_budget_environment(gate, trial_id=trial_id,
                        maximum_input_tokens_per_request=MAX_INPUT_TOKENS, maximum_output_tokens_per_request=MAX_OUTPUT_TOKENS,
                        maximum_reasoning_tokens_per_request=MAX_REASONING_TOKENS, maximum_provider_requests_per_trial=MAX_REQUESTS))
                    environment["CODEPACEX_STAGE_D_PROFILE"] = str(profile_path)
                    execution = (executor or _execute)(task, environment, Path(temp_text) / "repo")
                stdout = recorder.write_task_artifact(instance_id, "stdout", execution.stdout)
                recorder.write_task_artifact(instance_id, "stderr", execution.stderr)
                accounting = gate.trial_accounting(trial_id)
                observations = _observations(execution.stdout)
                if accounting.get("active_reservation") is not None:
                    error = "active_reservation_not_closed"
                elif execution.returncode:
                    error = "agent_process_failed"
                elif not execution.patch.strip():
                    error = "candidate_empty"
                else:
                    with tempfile.NamedTemporaryFile("w", suffix=".ndjson", encoding="utf-8") as trace:
                        trace.write(execution.stdout)
                        trace.flush()
                        stage_c_paid._ingest_trace(recorder, Path(trace.name), instance_id, "1", 1)
                    prediction = recorder.path / f"{hashlib.sha256(instance_id.encode()).hexdigest()}-prediction.json"
                    recorder.write_json(prediction.name, [{"instance_id": instance_id, "model_name_or_path": "qwen3.7-max-2026-06-08", "model_patch": execution.patch}])
                    evaluated = (evaluator or stage_c_paid._default_evaluate)(task, stage_c_paid.TaskExecution(execution.stdout, execution.stderr, execution.patch, execution.returncode), recorder, run_id)
                    if evaluated.evaluator_resolved is None or evaluated.evaluator_report is None:
                        error = "official_evaluator_missing_terminal_report"
                    else:
                        report = recorder.write_task_artifact(instance_id, "evaluator_report", evaluated.evaluator_report)
                        terminal = "resolved" if evaluated.evaluator_resolved else "unresolved"
                        task_records.append({"instance_id": instance_id, "status": terminal, "trial_id": trial_id,
                            "provider_requests": accounting.get("request_count", 0), "tool_observations": observations,
                            "candidate_sha256": hashlib.sha256(execution.patch.encode()).hexdigest(), "candidate_nonempty": True,
                            "workspace_diff_sha256": hashlib.sha256(execution.patch.encode()).hexdigest(), "candidate_matches_workspace_diff": True,
                            "evaluator_report_reference": report.name, "stdout_reference": stdout.name,
                            "combined_conservative_consumption_cny": accounting.get("combined_conservative_consumption_cny")})
                if error is not None:
                    task_records.append({"instance_id": instance_id, "status": "infrastructure_error", "trial_id": trial_id,
                        "provider_requests": accounting.get("request_count", 0), "active_reservation": accounting.get("active_reservation"),
                        "candidate_nonempty": bool(execution and execution.patch.strip()), "error": error})
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                accounting = gate.trial_accounting(trial_id)
                error = str(exc)
                task_records.append({"instance_id": instance_id, "status": "infrastructure_error", "trial_id": trial_id,
                    "provider_requests": accounting.get("request_count", 0), "active_reservation": accounting.get("active_reservation"), "error": error})
            statuses[instance_id] = terminal if error is None else "infrastructure_error"
            if statuses[instance_id] not in {"resolved", "unresolved"}:
                break
    for instance_id in stage_d1_freeze.CANARY_INSTANCE_IDS:
        statuses.setdefault(instance_id, "not_run")
    paths = _paths(evidence_root)
    ledger = BudgetLedger.model_validate_json(paths["ledger"].read_text(encoding="utf-8")) if paths["ledger"].exists() else BudgetLedger(authorization_hash="0" * 64, updated_at=_utc_now())
    report = {"schema_version": 1, "stage": "D.1", "budget_stage_key": BUDGET_STAGE_KEY, "run_id": run_id, "task_ids": list(stage_d1_freeze.CANARY_INSTANCE_IDS),
              "terminal_statuses": statuses, "task_records": task_records, "active_reservation": ledger.active_reservation.model_dump(mode="json") if ledger.active_reservation else None,
              "provider_requests": len(ledger.request_charges), "verified_cost_cny": str(ledger.spent_cny),
              "claims": "one_task_protocol_canary_only", "stage_c_evidence_modified": False}
    _write_new(paths["report"], report)
    artifact = {"schema_version": 1, "artifact_id": f"stage-d1-canary-{run_id}", "budget_stage_key": BUDGET_STAGE_KEY, "run_id": run_id,
                "task_ids": list(stage_d1_freeze.CANARY_INSTANCE_IDS), "terminal_statuses": statuses,
                "active_reservation": report["active_reservation"], "provider_requests": report["provider_requests"],
                "ledger_sha256": _sha256(paths["ledger"]), "report_sha256": _sha256(paths["report"]),
                "formal_stage_d_canary": executor is None}
    _write_new(paths["manifest"], artifact)
    recorder.finalize({"status": "success" if all(value in {"resolved", "unresolved"} for value in statuses.values()) else "infrastructure_error", "formal_stage_d_canary": executor is None})
    return artifact


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    bundle = sub.add_parser("build-task-bundle")
    bundle.add_argument("--source-dataset", type=Path, required=True); bundle.add_argument("--output", type=Path, required=True)
    for name in ("preflight", "prepare"):
        command = sub.add_parser(name)
        command.add_argument("--root", type=Path, required=True); command.add_argument("--freeze", type=Path, required=True)
        command.add_argument("--approved-commit", required=True); command.add_argument("--freeze-sha256", required=True)
        command.add_argument("--runtime-contract-hash", required=True); command.add_argument("--pricing-sha256", required=True)
        if name == "preflight":
            command.add_argument("--preflight-evidence-root", type=Path)
        if name == "prepare":
            command.add_argument("--evidence-root", type=Path, required=True); command.add_argument("--authorization-identity", required=True)
    execute_parser = sub.add_parser("execute")
    execute_parser.add_argument("--root", type=Path, required=True); execute_parser.add_argument("--freeze", type=Path, required=True)
    execute_parser.add_argument("--evidence-root", type=Path, required=True); execute_parser.add_argument("--run-id", required=True)
    execute_parser.add_argument("--task-bundle", type=Path, required=True); execute_parser.add_argument("--confirm-paid-run", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "build-task-bundle": result = build_task_bundle(source_dataset=args.source_dataset, output=args.output)
    elif args.command == "preflight": result = preflight(root=args.root, freeze_path=args.freeze, approved_commit=args.approved_commit, supplied_freeze_sha256=args.freeze_sha256, supplied_runtime_contract_hash=args.runtime_contract_hash, supplied_pricing_hash=args.pricing_sha256, require_secret=True, evidence_root=args.preflight_evidence_root)
    elif args.command == "prepare": result = prepare(root=args.root, freeze_path=args.freeze, evidence_root=args.evidence_root, approved_commit=args.approved_commit, authorization_identity=args.authorization_identity, supplied_freeze_sha256=args.freeze_sha256, supplied_runtime_contract_hash=args.runtime_contract_hash, supplied_pricing_hash=args.pricing_sha256)
    else: result = execute(root=args.root, freeze_path=args.freeze, evidence_root=args.evidence_root, run_id=args.run_id, task_bundle=args.task_bundle, confirmed=args.confirm_paid_run)
    print(json.dumps(result, sort_keys=True)); return 0


if __name__ == "__main__":
    raise SystemExit(main())

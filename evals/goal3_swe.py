"""Minimal, zero-cost bootstrap for native Goal 3 SWE-bench-Live controls.

This module deliberately does not implement paid inference.  It isolates Goal 3
artifacts, validates the required native host, and runs only evaluator controls.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml

from evals.benchmark import RunManifest, RunRecorder, canonical_hash, current_git_commit, sanitize_origin
from codepacex.experiments import ExperimentProfile
from evals.costing import load_pricing, pricing_snapshot_hash
from evals.paid_gate import (
    BudgetAuthorization, BudgetLedger, PaidRunGate, StageCBudgetAllocation,
    _money, authorization_hash, ledger_fingerprint, provider_request_budget_environment,
)
from evals.permission_study import trace_usage
from evals.pilot import (
    _child_environment, _ingest_trace, _provider_payload, _runtime_secrets,
    PilotConfig,
)
from evals.swe_bench_live import (
    instance_payload_hash,
    load_jsonl,
    official_evaluator_report_path,
    patch_file_count,
    run_official_evaluator,
    select_pilot_instances,
    size_bucket,
)


DEFAULT_ENVIRONMENT = Path("evals/goal3/swe_official_environment.json")
DEFAULT_PILOT_TEMPLATE = Path("evals/goal3/pilot.template.json")
DEFAULT_RUNS_DIR = Path("evals/.runs/goal3-swe")
DEFAULT_CONTROL_RUNS_DIR = Path("evals/.runs/goal3-control")
GOAL3_BUDGET_AUTHORIZATION = DEFAULT_RUNS_DIR / "budget-authorization.json"
GOAL3_BUDGET_LEDGER = DEFAULT_RUNS_DIR / "budget-ledger.json"
GOAL3_BUDGET_ALLOCATION = DEFAULT_RUNS_DIR / "budget-allocation.json"
GOAL3_PILOT_FREEZE = DEFAULT_RUNS_DIR / "pilot-freeze.json"
GOAL3_PRICING_SNAPSHOT = DEFAULT_RUNS_DIR / "pricing-snapshot.json"
GOAL3_DATASET_JSONL = DEFAULT_RUNS_DIR / "pilot-dataset.jsonl"
NATIVE_ARCHITECTURES = {"x86_64", "amd64"}
QEMU_MARKERS = ("qemu", "tcg", "virtual cpu")
MAXIMUM_REQUESTS_PER_INSTANCE = 50
MAXIMUM_INPUT_TOKENS_PER_REQUEST = 128_000
MAXIMUM_OUTPUT_TOKENS_PER_REQUEST = 8192
GOAL3_ENABLE_THINKING = True
GOAL3_THINKING_BUDGET = 6144
GOAL3_MODEL_PARAMETERS = {
    "temperature": None,
    "top_p": None,
    "max_output_tokens": None,
    "max_completion_tokens": MAXIMUM_OUTPUT_TOKENS_PER_REQUEST,
    "enable_thinking": GOAL3_ENABLE_THINKING,
    "thinking_budget": GOAL3_THINKING_BUDGET,
}
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
EXECUTION_INSTANCE_FIELDS = (
    "instance_id", "repo", "base_commit", "problem_statement", "platform",
    "version", "environment_setup_commit",
)
ENVIRONMENT = {
    "schema_version": 1,
    "repository": "https://github.com/microsoft/SWE-bench-Live",
    "branch": "python-only",
    "commit": "ad79b850f15e33992e96f03f6e97f05ddf9aa0be",
    "dataset": "SWE-bench-Live/SWE-bench-Live",
    "split": "lite",
    "evaluator_namespace": "starryzhang",
    "installation": "isolated-editable-checkout",
    "docker_required": True,
    "native_host_required": True,
    "allowed_architectures": ["x86_64", "amd64"],
}


def load_goal3_environment(path: Path = DEFAULT_ENVIRONMENT) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Goal 3 official environment must be a JSON object")
    for key, value in ENVIRONMENT.items():
        if payload.get(key) != value:
            raise ValueError(f"Goal 3 official environment changed: {key}")
    if set(payload) != {*ENVIRONMENT, "notes"}:
        raise ValueError("Goal 3 official environment has unknown fields")
    if not isinstance(payload["notes"], list) or not all(isinstance(item, str) for item in payload["notes"]):
        raise ValueError("Goal 3 official environment notes must be strings")
    return payload


def load_goal3_pilot_template(path: Path = DEFAULT_PILOT_TEMPLATE) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema_version", "status", "experiment_kind", "provider", "model_id",
        "dataset_revision", "pricing_snapshot", "instance_ids", "instance_count",
        "fallback_enabled", "retry_budget", "serial", "model_parameters",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("Goal 3 Pilot template has an invalid schema")
    if payload["schema_version"] != 1 or payload["status"] != "candidate_unverified":
        raise ValueError("Goal 3 Pilot template must remain an unverified candidate")
    if payload["experiment_kind"] != "goal3-swe-bench-live-pilot":
        raise ValueError("Goal 3 Pilot template has an invalid Run identity")
    if payload["provider"] is not None or payload["model_id"] is not None:
        raise ValueError("Goal 3 Pilot provider and model require explicit later verification")
    if payload["dataset_revision"] is not None or payload["pricing_snapshot"] is not None:
        raise ValueError("Goal 3 Pilot dataset and pricing require explicit later verification")
    if payload["instance_ids"] != [] or payload["instance_count"] != 3:
        raise ValueError("Goal 3 Pilot template must reserve exactly three later-frozen instances")
    if payload["fallback_enabled"] is not False or payload["retry_budget"] != 0 or payload["serial"] is not True:
        raise ValueError("Goal 3 Pilot template requires no fallback, no retry, and serial execution")
    if payload["model_parameters"] != {
        "temperature": None, "top_p": None, "max_output_tokens": None,
        "max_completion_tokens": None, "enable_thinking": None, "thinking_budget": None,
    }:
        raise ValueError("Goal 3 Pilot template has unrecognized model parameters")
    return payload


def goal3_budget_paths() -> dict[str, Path]:
    """Name the future Goal 3 accounting files without creating any of them."""
    return {
        "authorization": GOAL3_BUDGET_AUTHORIZATION,
        "ledger": GOAL3_BUDGET_LEDGER,
        "allocation": GOAL3_BUDGET_ALLOCATION,
    }


def freeze_pilot_config(
    *, dataset_jsonl: Path, output: Path, dataset_revision: str,
    provider: str, model_id: str, model_parameters: dict[str, Any],
    pricing_snapshot_hash: str,
) -> dict[str, Any]:
    """Freeze three official instances only after real execution inputs are known.

    This creates configuration metadata, not an authorization, allocation, ledger,
    prediction, Claim, or Provider request.
    """
    if not all(isinstance(value, str) and value.strip() for value in (
        dataset_revision, provider, model_id,
    )):
        raise ValueError("Goal 3 Pilot freeze requires explicit dataset, Provider, and model identities")
    if not re.fullmatch(r"[0-9a-f]{64}", pricing_snapshot_hash):
        raise ValueError("Goal 3 Pilot freeze requires an explicit pricing snapshot hash")
    if model_parameters != GOAL3_MODEL_PARAMETERS:
        raise ValueError("Goal 3 Pilot freeze has unrecognized model parameters")
    selected = select_pilot_instances(load_jsonl(dataset_jsonl))
    instance_ids = [str(item["instance_id"]) for item in selected]
    if len(instance_ids) != 3 or len(set(instance_ids)) != 3:
        raise ValueError("Goal 3 Pilot freeze requires exactly three unique instances")
    payload = {
        "schema_version": 1,
        "status": "frozen",
        "experiment_kind": "goal3-swe-bench-live-pilot",
        "provider": provider,
        "model_id": model_id,
        "dataset_revision": dataset_revision,
        "pricing_snapshot_hash": pricing_snapshot_hash,
        "instance_ids": instance_ids,
        "instance_payload_hashes": {
            str(instance["instance_id"]): instance_payload_hash(instance)
            for instance in selected
        },
        "fallback_enabled": False,
        "retry_budget": 0,
        "serial": True,
        "model_parameters": model_parameters,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def execution_instance_payload(instance: dict[str, Any]) -> dict[str, Any]:
    """Return the exact Agent-visible subset of a SWE-bench-Live instance.

    Gold patches, tests, hints, and other evaluator-only fields are intentionally
    excluded before a Goal 3 paid artifact is written.
    """
    return {field: instance.get(field) for field in EXECUTION_INSTANCE_FIELDS}


def execution_instance_payload_hash(instance: dict[str, Any]) -> str:
    return canonical_hash(execution_instance_payload(instance))


def freeze_paid_pilot_bundle(
    *, root: Path, dataset_jsonl: Path, pricing_snapshot: Path, output_dir: Path,
    dataset_revision: str,
) -> dict[str, Any]:
    """Write one immutable, Agent-safe Goal 3 Pilot bundle.

    The input may contain official gold data, but only hashes of it enter the
    bundle. The persisted dataset has precisely the fields used to materialize
    the repository and construct the Agent prompt.
    """
    root = root.resolve()
    output_dir = output_dir.resolve()
    _require_goal3_path(output_dir)
    if not re.fullmatch(r"[0-9a-f]{40}", dataset_revision):
        raise ValueError("Goal 3 Pilot freeze requires the exact Dataset revision")
    commit = current_git_commit(root)
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Goal 3 Pilot freeze requires an exact Git commit")
    pricing = load_pricing(pricing_snapshot)
    rows = load_jsonl(dataset_jsonl)
    selected = select_pilot_instances(rows)
    if len(selected) != 3:
        raise ValueError("Goal 3 Pilot freeze requires exactly three selected instances")
    source_hash = canonical_hash(rows)
    pilots = [{
        "instance_id": str(instance["instance_id"]),
        "repo": str(instance["repo"]),
        "size_bucket": size_bucket(instance),
        "gold_file_count": patch_file_count(str(instance.get("patch", ""))),
        "payload_sha256": instance_payload_hash(instance),
        "execution_payload_sha256": execution_instance_payload_hash(instance),
    } for instance in selected]
    matrix = {
        "dataset_revision": dataset_revision,
        "dataset_source_sha256": source_hash,
        "selection_algorithm": "python-lite-size-stratified-v1",
        "pilots": pilots,
    }
    profile = ExperimentProfile(
        tool_loading="deferred", compression_profile="recovery_v1",
        permission_strategy="session_allow", agent_mode="single",
    )
    frozen = {
        "schema_version": 1,
        "status": "frozen_pending_authorization",
        "experiment_kind": "goal3-swe-bench-live-pilot",
        "codepacex_commit": commit,
        "official_evaluator_commit": ENVIRONMENT["commit"],
        "dataset": ENVIRONMENT["dataset"],
        "dataset_split": ENVIRONMENT["split"],
        "dataset_revision": dataset_revision,
        "dataset_source_sha256": source_hash,
        "selection_algorithm": matrix["selection_algorithm"],
        "matrix_sha256": canonical_hash(matrix),
        "provider": "bailian-qwen37-max",
        "protocol": "openai-compat",
        "base_url": "https://llm-ipge9fy38w648m28.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "BAILIAN_API_KEY",
        "model_id": "qwen3.7-max-2026-06-08",
        "pricing_snapshot_hash": pricing_snapshot_hash(pricing),
        "experiment_profile": profile.canonical_payload(),
        "fallback_enabled": False,
        "retry_budget": 0,
        "serial": True,
        "max_provider_requests_per_instance": MAXIMUM_REQUESTS_PER_INSTANCE,
        "maximum_input_tokens_per_request": MAXIMUM_INPUT_TOKENS_PER_REQUEST,
        "maximum_output_tokens_per_request": MAXIMUM_OUTPUT_TOKENS_PER_REQUEST,
        "model_parameters": GOAL3_MODEL_PARAMETERS,
        "pilots": pilots,
    }
    paths = {
        "pilot": output_dir / "pilot-freeze.json",
        "pricing": output_dir / "pricing-snapshot.json",
        "dataset": output_dir / "pilot-dataset.jsonl",
    }
    if any(path.exists() for path in paths.values()):
        raise ValueError("Goal 3 paid Pilot bundle already exists and cannot be rewritten")
    output_dir.mkdir(parents=True, exist_ok=True)
    paths["pilot"].write_text(json.dumps(frozen, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    paths["pricing"].write_bytes(pricing_snapshot.read_bytes())
    paths["dataset"].write_text(
        "".join(json.dumps(execution_instance_payload(instance), ensure_ascii=False, sort_keys=True) + "\n" for instance in selected),
        encoding="utf-8",
    )
    return frozen


def load_frozen_pilot(
    path: Path = GOAL3_PILOT_FREEZE, *, root: Path | None = None,
) -> dict[str, Any]:
    """Load one immutable three-instance Goal 3 execution contract."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema_version", "status", "experiment_kind", "codepacex_commit",
        "official_evaluator_commit", "dataset", "dataset_split", "dataset_revision",
        "dataset_source_sha256", "selection_algorithm", "matrix_sha256", "provider",
        "protocol", "base_url", "api_key_env", "model_id", "pricing_snapshot_hash",
        "experiment_profile", "fallback_enabled", "retry_budget", "serial",
        "max_provider_requests_per_instance", "maximum_input_tokens_per_request",
        "maximum_output_tokens_per_request", "model_parameters", "pilots",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("Goal 3 Pilot freeze has an invalid schema")
    if payload["schema_version"] != 1 or payload["status"] != "frozen_pending_authorization":
        raise ValueError("Goal 3 Pilot freeze is not awaiting explicit authorization")
    if payload["experiment_kind"] != "goal3-swe-bench-live-pilot":
        raise ValueError("Goal 3 Pilot freeze has an invalid experiment identity")
    if root is not None and payload["codepacex_commit"] != current_git_commit(root):
        raise ValueError("Goal 3 Pilot freeze does not match the current commit")
    if payload["official_evaluator_commit"] != ENVIRONMENT["commit"]:
        raise ValueError("Goal 3 Pilot freeze does not match the official evaluator")
    if payload["dataset"] != ENVIRONMENT["dataset"] or payload["dataset_split"] != ENVIRONMENT["split"]:
        raise ValueError("Goal 3 Pilot freeze does not match the official Dataset")
    if not all(isinstance(payload[key], str) and payload[key] for key in (
        "dataset_revision", "dataset_source_sha256", "matrix_sha256", "provider",
        "protocol", "base_url", "api_key_env", "model_id", "pricing_snapshot_hash",
    )):
        raise ValueError("Goal 3 Pilot freeze is missing immutable provenance")
    if not re.fullmatch(r"[0-9a-f]{64}", payload["pricing_snapshot_hash"]):
        raise ValueError("Goal 3 Pilot freeze has an invalid pricing snapshot hash")
    if payload["fallback_enabled"] is not False or payload["retry_budget"] != 0 or payload["serial"] is not True:
        raise ValueError("Goal 3 Pilot freeze requires serial no-fallback no-retry execution")
    if payload["max_provider_requests_per_instance"] != MAXIMUM_REQUESTS_PER_INSTANCE:
        raise ValueError("Goal 3 Pilot freeze changed the request ceiling")
    if payload["maximum_input_tokens_per_request"] != MAXIMUM_INPUT_TOKENS_PER_REQUEST:
        raise ValueError("Goal 3 Pilot freeze changed the input ceiling")
    if payload["maximum_output_tokens_per_request"] != MAXIMUM_OUTPUT_TOKENS_PER_REQUEST:
        raise ValueError("Goal 3 Pilot freeze changed the output ceiling")
    profile = ExperimentProfile.model_validate(payload["experiment_profile"])
    if profile.canonical_payload() != payload["experiment_profile"]:
        raise ValueError("Goal 3 Pilot freeze has an invalid experiment profile")
    if payload["model_parameters"] != GOAL3_MODEL_PARAMETERS:
        raise ValueError("Goal 3 Pilot freeze changed model parameters")
    pilots = payload["pilots"]
    if not isinstance(pilots, list) or len(pilots) != 3:
        raise ValueError("Goal 3 Pilot freeze must contain exactly three Pilots")
    ids: set[str] = set()
    buckets: set[str] = set()
    for pilot in pilots:
        if not isinstance(pilot, dict) or set(pilot) != {
            "instance_id", "repo", "size_bucket", "gold_file_count", "payload_sha256",
            "execution_payload_sha256",
        }:
            raise ValueError("Goal 3 Pilot freeze has an invalid Pilot record")
        instance_id = pilot["instance_id"]
        if not isinstance(instance_id, str) or not instance_id or instance_id in ids:
            raise ValueError("Goal 3 Pilot IDs must be unique")
        if not isinstance(pilot["repo"], str) or not REPOSITORY_RE.fullmatch(pilot["repo"]):
            raise ValueError("Goal 3 Pilot has an invalid repository")
        if pilot["size_bucket"] not in {"one_file", "two_to_four_files", "five_plus_files"}:
            raise ValueError("Goal 3 Pilot has an invalid size bucket")
        if not isinstance(pilot["gold_file_count"], int) or pilot["gold_file_count"] < 1:
            raise ValueError("Goal 3 Pilot has an invalid file count")
        if not isinstance(pilot["payload_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", pilot["payload_sha256"]):
            raise ValueError("Goal 3 Pilot has an invalid payload hash")
        if not isinstance(pilot["execution_payload_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", pilot["execution_payload_sha256"]):
            raise ValueError("Goal 3 Pilot has an invalid execution payload hash")
        ids.add(instance_id)
        buckets.add(pilot["size_bucket"])
    if buckets != {"one_file", "two_to_four_files", "five_plus_files"}:
        raise ValueError("Goal 3 Pilots must cover every frozen size bucket")
    return payload


def load_frozen_instances(
    *, pilot_freeze: dict[str, Any], dataset_jsonl: Path = GOAL3_DATASET_JSONL,
) -> list[dict[str, Any]]:
    """Verify the exact local official rows without exposing or copying gold patches."""
    rows = {str(item.get("instance_id", "")): item for item in load_jsonl(dataset_jsonl)}
    selected: list[dict[str, Any]] = []
    for expected in pilot_freeze["pilots"]:
        instance_id = expected["instance_id"]
        instance = rows.get(instance_id)
        if instance is None:
            raise ValueError(f"Goal 3 frozen Dataset does not contain {instance_id}")
        if set(instance) != set(EXECUTION_INSTANCE_FIELDS):
            raise ValueError(f"Goal 3 frozen Dataset has non-Agent-visible fields: {instance_id}")
        if instance.get("repo") != expected["repo"]:
            raise ValueError(f"Goal 3 frozen repository changed: {instance_id}")
        if execution_instance_payload_hash(instance) != expected["execution_payload_sha256"]:
            raise ValueError(f"Goal 3 frozen execution payload changed: {instance_id}")
        selected.append(instance)
    if len(rows) != 3:
        raise ValueError("Goal 3 Pilot Dataset must contain exactly the three frozen rows")
    return selected


def _goal3_child_config(*, pilot: Any, home: Path) -> None:
    payload = _provider_payload(pilot)
    payload["sandbox"] = {
        "enabled": False, "auto_allow": False, "network_enabled": False,
    }
    config_dir = home / ".codepacex"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=True), encoding="utf-8",
    )


def _goal3_materialize_instance(instance: dict[str, Any], workspace: Path) -> None:
    repository = str(instance.get("repo", ""))
    commit = str(instance.get("base_commit", ""))
    if not REPOSITORY_RE.fullmatch(repository) or not re.fullmatch(r"[0-9a-fA-F]{7,40}", commit):
        raise ValueError("Goal 3 Pilot has an unsafe repository or base commit")
    clone = subprocess.run(
        [
            "git", "clone", "--quiet", "--filter=blob:none", "--no-checkout",
            f"https://github.com/{repository}.git", str(workspace),
        ], text=True, capture_output=True, timeout=600, check=False,
    )
    if clone.returncode != 0:
        raise ValueError(f"failed to clone frozen Goal 3 repository: {repository}")
    switch = subprocess.run(
        ["git", "-C", str(workspace), "switch", "--detach", commit],
        text=True, capture_output=True, timeout=300, check=False,
    )
    if switch.returncode != 0:
        raise ValueError(f"failed to materialize frozen Goal 3 base commit: {commit}")
    head = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        text=True, capture_output=True, check=False,
    ).stdout.strip()
    if not head.lower().startswith(commit.lower()):
        raise ValueError("materialized Goal 3 repository HEAD mismatch")


def _goal3_extract_patch(workspace: Path) -> str:
    result = subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff"], cwd=workspace,
        text=True, capture_output=True, timeout=120, check=False,
    )
    if result.returncode != 0:
        raise ValueError("cannot extract Goal 3 model patch")
    return result.stdout


def _goal3_inference_prompt(instance: dict[str, Any]) -> str:
    problem = str(instance.get("problem_statement", "")).strip()
    if not problem:
        raise ValueError("Goal 3 Pilot has no problem statement")
    return (
        "Solve the following SWE-bench-Live issue in the current repository. "
        "Inspect the code and tests, implement the smallest correct fix, and run relevant tests. "
        "Do not modify tests to hide failures. Do not merely describe a patch; edit the workspace.\n\n"
        + problem
    )


def _goal3_trial_id(*, run_id: str, instance_id: str) -> str:
    return f"swe/{run_id}/pilot/1/{instance_id}"


def _require_goal3_child_contract(pilot: Any, frozen: dict[str, Any]) -> None:
    actual = {
        "provider": pilot.provider, "protocol": pilot.protocol,
        "base_url": pilot.base_url, "api_key_env": pilot.api_key_env,
        "model_id": pilot.model_id,
    }
    expected = {key: frozen[key] for key in actual}
    if actual != expected:
        raise ValueError("configured Provider identity does not match the frozen Goal 3 Pilot")
    if pilot.fallback_enabled or pilot.retry_budget != 0:
        raise ValueError("Goal 3 Pilot requires fallback=false and retry=0")
    if pilot.model_parameters.model_dump(mode="json") != frozen["model_parameters"]:
        raise ValueError("configured model parameters do not match the frozen Goal 3 Pilot")
    profile = ExperimentProfile.model_validate(frozen["experiment_profile"])
    if pilot.experiment_profile.canonical_payload() != profile.canonical_payload():
        raise ValueError("configured experiment profile does not match the frozen Goal 3 Pilot")


def _goal3_pilot_config(frozen: dict[str, Any]) -> PilotConfig:
    """Build an isolated child configuration from the Goal 3 freeze only."""
    return PilotConfig.model_validate({
        "schema_version": 2,
        "experiment_kind": "pilot",
        "provider": frozen["provider"],
        "protocol": frozen["protocol"],
        "base_url": frozen["base_url"],
        "api_key_env": frozen["api_key_env"],
        "model_id": frozen["model_id"],
        "fallback_enabled": frozen["fallback_enabled"],
        "model_parameters": frozen["model_parameters"],
        "retry_budget": frozen["retry_budget"],
        "task_ids": [],
        "repetitions": 1,
        "feature_flags": {},
        "experiment_profile": frozen["experiment_profile"],
        "max_iterations": frozen["max_provider_requests_per_instance"],
    })


def _goal3_manifest(*, root: Path, frozen: dict[str, Any], run_id: str) -> RunManifest:
    profile = ExperimentProfile.model_validate(frozen["experiment_profile"])
    return RunManifest(
        experiment_kind="goal3-swe-bench-live-pilot",
        provider=frozen["provider"], protocol=frozen["protocol"],
        base_url_origin=sanitize_origin(frozen["base_url"]), api_key_env=frozen["api_key_env"],
        model_id=frozen["model_id"], run_id=run_id, git_commit=current_git_commit(root),
        prompt_version="swe-bench-live-inference-v1", feature_flags={},
        swe_evaluator_architecture="native", experiment_profile=profile.canonical_payload(),
        experiment_profile_hash=profile.profile_hash(),
        runtime_contract_hash=profile.runtime_contract_hash(),
        benchmark_asset_hash=canonical_hash({
            "pilot_matrix_sha256": frozen["matrix_sha256"],
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }),
        task_ids=[item["instance_id"] for item in frozen["pilots"]], repetitions=1,
        model_parameters=frozen["model_parameters"], max_output_tokens=MAXIMUM_OUTPUT_TOKENS_PER_REQUEST,
        retry_budget=0, fallback_enabled=False, max_iterations=MAXIMUM_REQUESTS_PER_INSTANCE,
        pricing_snapshot_hash=frozen["pricing_snapshot_hash"],
        experiment_config_hash=canonical_hash({
            "pilot_freeze": frozen, "profile": profile.canonical_payload(),
        }),
    )


def _goal3_terminal(
    recorder: RunRecorder, *, instance_id: str, trial_id: str, status: str, started: float,
    accounting: dict[str, Any], **extra: Any,
) -> None:
    if not trial_id or accounting.get("trial_id") != trial_id:
        raise ValueError("Goal 3 terminal accounting does not match the Trial ID")
    payload = {
        "task_id": instance_id, "repetition_id": "1", "attempt_id": 1,
        "status": status, "duration_seconds": time.monotonic() - started,
        "trial_id": trial_id,
        "provider_request_count": accounting.get("request_count", 0),
        "actual_cny": accounting.get("actual_cny", 0),
        **extra,
    }
    recorder.event("trial_completed", payload)


def execute_pilot(
    *, root: Path, dataset_jsonl: Path, runs_dir: Path, run_id: str,
    pilot_freeze_path: Path, pricing_snapshot: Path, budget_authorization: Path,
    budget_ledger: Path, budget_allocation: Path, confirmed: bool,
) -> RunRecorder:
    """Run the three frozen Goal 3 Pilots serially after explicit authorization.

    This function deliberately shares no Run, ledger, authorization, allocation,
    matrix, or output path with Goal 2. Each model request receives a fresh
    durable reservation through the child budget bridge.
    """
    root = root.resolve()
    runs_dir = runs_dir.resolve()
    _require_goal3_path(runs_dir)
    _require_goal3_path(dataset_jsonl.resolve())
    for path in (pilot_freeze_path, pricing_snapshot, budget_authorization, budget_ledger, budget_allocation):
        if path.resolve().parent != runs_dir:
            raise ValueError("Goal 3 paid artifacts must share the isolated Goal 3 runs directory")
    if not confirmed:
        raise ValueError("Goal 3 paid execution requires --confirm-paid-run")
    preflight = require_native_preflight(root=root)
    frozen = load_frozen_pilot(pilot_freeze_path, root=root)
    instances = load_frozen_instances(pilot_freeze=frozen, dataset_jsonl=dataset_jsonl)
    pricing = load_pricing(pricing_snapshot)
    if pricing_snapshot_hash(pricing) != frozen["pricing_snapshot_hash"]:
        raise ValueError("Goal 3 pricing snapshot does not match the frozen Pilot")
    pilot = _goal3_pilot_config(frozen)
    _require_goal3_child_contract(pilot, frozen)
    if not os.environ.get(pilot.api_key_env):
        raise ValueError("Goal 3 paid execution requires the configured API key")
    ensure_new_paid_pilot_run(runs_dir=runs_dir, run_id=run_id, ledger_path=budget_ledger)
    gate = PaidRunGate(
        root=root, authorization_path=budget_authorization, ledger_path=budget_ledger,
        allocation_path=budget_allocation, pricing_path=pricing_snapshot,
        pricing=pricing, stage="C",
    )
    recorder = RunRecorder(
        runs_dir, _goal3_manifest(root=root, frozen=frozen, run_id=run_id), run_id=run_id,
        repo_root=root, secrets=_runtime_secrets(pilot),
    )
    profile = ExperimentProfile.model_validate(frozen["experiment_profile"])
    resolved_count = 0
    with tempfile.TemporaryDirectory(prefix="codepacex-goal3-home-") as home_text:
        home = Path(home_text)
        _goal3_child_config(pilot=pilot, home=home)
        profile_path = home / "profile.yaml"
        profile_path.write_text(yaml.safe_dump(profile.canonical_payload(), sort_keys=True), encoding="utf-8")
        environment = _child_environment(pilot, home_text, root=root)
        for instance in instances:
            instance_id = str(instance["instance_id"])
            trial_id = _goal3_trial_id(run_id=run_id, instance_id=instance_id)
            started = time.monotonic()
            accounting: dict[str, Any] = {"trial_id": trial_id, "request_count": 0, "actual_cny": 0}
            recorder.event("trial_started", {
                "task_id": instance_id, "repetition_id": "1", "attempt_id": 1,
                "trial_id": trial_id, "budget_mode": "per_provider_request",
            })
            with tempfile.TemporaryDirectory(prefix=f"codepacex-goal3-{instance_id}-") as text:
                workspace = Path(text) / "repo"
                try:
                    _goal3_materialize_instance(instance, workspace)
                    child_environment = dict(environment)
                    child_environment.update(provider_request_budget_environment(
                        gate, trial_id=trial_id,
                        maximum_input_tokens_per_request=MAXIMUM_INPUT_TOKENS_PER_REQUEST,
                        maximum_output_tokens_per_request=MAXIMUM_OUTPUT_TOKENS_PER_REQUEST,
                        maximum_reasoning_tokens_per_request=GOAL3_THINKING_BUDGET,
                    ))
                    process = subprocess.run(
                        [sys.executable, "-m", "codepacex", "-p", _goal3_inference_prompt(instance),
                         "--output-format", "stream-json", "--experiment-profile", str(profile_path)],
                        cwd=workspace, env=child_environment, text=True, capture_output=True,
                        timeout=1800, check=False,
                    )
                    recorder.write_task_artifact(instance_id, "stdout", process.stdout or "")
                    recorder.write_task_artifact(instance_id, "stderr", process.stderr or "")
                except subprocess.TimeoutExpired as exc:
                    recorder.write_task_artifact(instance_id, "stdout", exc.stdout or "")
                    recorder.write_task_artifact(instance_id, "stderr", exc.stderr or "")
                    accounting = gate.trial_accounting(trial_id)
                    _goal3_terminal(recorder, instance_id=instance_id, trial_id=trial_id, status="timeout", started=started,
                                    accounting=accounting, budget_reconciliation_required=True)
                    recorder.finalize({"status": "timeout", "execution_mode": "live", "official_evaluator_completed": False})
                    return recorder
                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                    accounting = gate.trial_accounting(trial_id)
                    _goal3_terminal(recorder, instance_id=instance_id, trial_id=trial_id, status="infrastructure_error", started=started,
                                    accounting=accounting, error=str(exc), budget_reconciliation_required=accounting.get("active_reservation") is not None)
                    recorder.finalize({"status": "infrastructure_error", "execution_mode": "live", "official_evaluator_completed": False})
                    return recorder
                accounting = gate.trial_accounting(trial_id)
                requests, _input_tokens, _output_tokens = trace_usage(process.stdout or "")
                violation = accounting.get("provider_usage_contract_violation")
                if violation is not None:
                    _goal3_terminal(
                        recorder, instance_id=instance_id, trial_id=trial_id,
                        status="infrastructure_error", started=started, accounting=accounting,
                        reason="provider_usage_contract_violation",
                        provider_usage_contract_violation=violation,
                        budget_reconciliation_required=False,
                    )
                    recorder.finalize({
                        "status": "infrastructure_error", "execution_mode": "live",
                        "official_evaluator_completed": False,
                    })
                    return recorder
                if accounting["budget_blocked"]:
                    _goal3_terminal(recorder, instance_id=instance_id, trial_id=trial_id, status="budget_blocked", started=started,
                                    accounting=accounting, budget_block_reasons=accounting["budget_block_reasons"])
                    recorder.finalize({"status": "budget_blocked", "execution_mode": "live", "official_evaluator_completed": False})
                    return recorder
                if accounting["active_reservation"] is not None or requests == 0 or accounting["request_count"] != requests:
                    reason = "active_reservation" if accounting["active_reservation"] is not None else (
                        "missing_trace_usage" if requests == 0 else "request_count_mismatch"
                    )
                    _goal3_terminal(recorder, instance_id=instance_id, trial_id=trial_id, status="infrastructure_error", started=started,
                                    accounting=accounting, reconciliation_reason=reason,
                                    budget_reconciliation_required=accounting["active_reservation"] is not None)
                    recorder.finalize({"status": "infrastructure_error", "execution_mode": "live", "official_evaluator_completed": False})
                    return recorder
                with tempfile.NamedTemporaryFile("w", suffix=".ndjson", encoding="utf-8") as trace:
                    trace.write(process.stdout or "")
                    trace.flush()
                    _ingest_trace(recorder, Path(trace.name), instance_id, "1", 1)
                patch = _goal3_extract_patch(workspace)
                recorder.write_json(f"{instance_id}.prediction.json", [{
                    "instance_id": instance_id, "model_name_or_path": pilot.model_id, "model_patch": patch,
                }])
                if process.returncode != 0 or not patch.strip():
                    _goal3_terminal(recorder, instance_id=instance_id, trial_id=trial_id, status="task_failure", started=started,
                                    accounting=accounting, process_returncode=process.returncode,
                                    empty_patch=not bool(patch.strip()), official_evaluator_completed=False)
                    recorder.finalize({"status": "task_failure", "execution_mode": "live", "official_evaluator_completed": False})
                    return recorder
                try:
                    evaluator_run_id = f"{run_id}-{instance_id}"
                    evaluator = run_official_evaluator(
                        dataset_name=ENVIRONMENT["dataset"], split=ENVIRONMENT["split"],
                        predictions_path=recorder.path / f"{instance_id}.prediction.json",
                        instance_ids=[instance_id], max_workers=1, run_id=evaluator_run_id,
                        namespace=ENVIRONMENT["evaluator_namespace"],
                        cwd=recorder.path, evaluator_architecture="native",
                    )
                    recorder.write_task_artifact(
                        instance_id, "evaluator", (evaluator.stdout or "") + "\n" + (evaluator.stderr or ""),
                    )
                    if evaluator.returncode != 0:
                        raise ValueError(f"official evaluator failed with exit status {evaluator.returncode}")
                    report_path = official_evaluator_report_path(
                        cwd=recorder.path, run_id=evaluator_run_id,
                        model_id=pilot.model_id, instance_id=instance_id,
                    )
                    resolved = collect_goal3_official_outcome(report_path, instance_id)
                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                    _goal3_terminal(recorder, instance_id=instance_id, trial_id=trial_id, status="infrastructure_error", started=started,
                                    accounting=accounting, error=str(exc), official_evaluator_completed=False)
                    recorder.finalize({"status": "infrastructure_error", "execution_mode": "live", "official_evaluator_completed": False})
                    return recorder
                _goal3_terminal(recorder, instance_id=instance_id, trial_id=trial_id, status="resolved" if resolved else "unresolved", started=started,
                                accounting=accounting, process_returncode=process.returncode,
                                empty_patch=False, official_evaluator_completed=True,
                                official_outcome=resolved, numerator=int(resolved), denominator=1)
                resolved_count += int(resolved)
    recorder.finalize({
        "status": "success" if resolved_count == len(instances) else "task_failure",
        "execution_mode": "live", "official_evaluator_completed": True,
        "resolved_count": resolved_count, "evaluated_count": len(instances),
        "native_linux_x86_64": preflight["native_linux_x86_64"],
        "evaluator_commit": preflight["installed_evaluator_commit"],
    })
    return recorder


def paid_preflight(
    *, root: Path, dataset_jsonl: Path, pilot_freeze_path: Path,
    pricing_snapshot: Path, budget_authorization: Path, budget_ledger: Path,
    budget_allocation: Path,
) -> dict[str, Any]:
    """Validate a paid Pilot contract without importing or calling a Provider."""
    root = root.resolve()
    _require_goal3_path(dataset_jsonl.resolve())
    frozen = load_frozen_pilot(pilot_freeze_path, root=root)
    instances = load_frozen_instances(pilot_freeze=frozen, dataset_jsonl=dataset_jsonl)
    if pricing_snapshot_hash(load_pricing(pricing_snapshot)) != frozen["pricing_snapshot_hash"]:
        raise ValueError("Goal 3 pricing snapshot does not match the frozen Pilot")
    _require_goal3_path(budget_authorization.resolve())
    _require_goal3_path(budget_ledger.resolve())
    _require_goal3_path(budget_allocation.resolve())
    return {
        "valid": True,
        "paid_execution_enabled": False,
        "instance_ids": [str(instance["instance_id"]) for instance in instances],
        "pricing_snapshot_hash": frozen["pricing_snapshot_hash"],
        "model_parameters": frozen["model_parameters"],
        "authorization_exists": budget_authorization.exists(),
        "ledger_exists": budget_ledger.exists(),
        "allocation_exists": budget_allocation.exists(),
    }


def create_paid_artifacts(
    *, root: Path, pilot_freeze_path: Path, pricing_snapshot: Path,
    budget_authorization: Path, budget_ledger: Path, budget_allocation: Path,
    authorized_total_cny: Decimal,
) -> dict[str, Any]:
    """Create Goal 3-only authorization and zero-baseline accounting artifacts.

    This records the user's already-granted ceiling but does not reserve or submit
    a Provider request. Existing files are refused so an operator cannot rewrite
    a paid execution contract after the fact.
    """
    root = root.resolve()
    _require_goal3_path(pilot_freeze_path.resolve())
    _require_goal3_path(pricing_snapshot.resolve())
    frozen = load_frozen_pilot(pilot_freeze_path, root=root)
    pricing = load_pricing(pricing_snapshot)
    if pricing_snapshot_hash(pricing) != frozen["pricing_snapshot_hash"]:
        raise ValueError("Goal 3 pricing snapshot does not match the frozen Pilot")
    paths = (budget_authorization, budget_ledger, budget_allocation)
    if any(path.exists() for path in paths):
        raise ValueError("Goal 3 paid authorization, ledger, or allocation already exists")
    for path in paths:
        _require_goal3_path(path.resolve())
        path.parent.mkdir(parents=True, exist_ok=True)
    per_request = Decimal(str(pricing.input_price)) * Decimal(MAXIMUM_INPUT_TOKENS_PER_REQUEST) / Decimal(pricing.unit_tokens)
    per_request += Decimal(str(pricing.output_price)) * Decimal(MAXIMUM_OUTPUT_TOKENS_PER_REQUEST) / Decimal(pricing.unit_tokens)
    execution_ceiling = _money(per_request * Decimal(MAXIMUM_REQUESTS_PER_INSTANCE * 3))
    if authorized_total_cny < execution_ceiling:
        raise ValueError("Goal 3 authorization is below the frozen worst-case Pilot ceiling")
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    authorization = BudgetAuthorization(
        authorized_total_cny=authorized_total_cny,
        stage_limits_cny={"A": execution_ceiling, "B": execution_ceiling, "C": authorized_total_cny},
        pricing_snapshot_hash=frozen["pricing_snapshot_hash"],
        experiment_commit=frozen["codepacex_commit"], authorized_at=timestamp, authorized_by="user",
    )
    baseline = BudgetLedger(authorization_hash=authorization_hash(authorization), updated_at=timestamp)
    allocation = StageCBudgetAllocation(
        experiment_commit=frozen["codepacex_commit"], pricing_snapshot_hash=frozen["pricing_snapshot_hash"],
        baseline_ledger_sha256=ledger_fingerprint(baseline),
        baseline_authorization_hash=baseline.authorization_hash, baseline_spent_cny=Decimal("0"),
        baseline_request_charge_count=0, baseline_settlement_count=0,
        baseline_budget_block_count=0, baseline_rebind_count=0,
        safety_reserve_cny=_money(authorized_total_cny - execution_ceiling),
        spendable_total_cny=execution_ceiling,
        category_limits_cny={
            "swe": execution_ceiling, "mcp": Decimal("0"), "retention": Decimal("0"),
            "permission": Decimal("0"), "multi_agent": Decimal("0"), "long_session": Decimal("0"),
        },
    )
    for path, payload in (
        (budget_authorization, authorization.model_dump(mode="json")),
        (budget_ledger, baseline.model_dump(mode="json")),
        (budget_allocation, allocation.model_dump(mode="json")),
    ):
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "valid": True, "authorized_total_cny": str(authorized_total_cny),
        "execution_ceiling_cny": str(execution_ceiling),
        "safety_reserve_cny": str(authorized_total_cny - execution_ceiling),
        "authorization": str(budget_authorization), "ledger": str(budget_ledger),
        "allocation": str(budget_allocation),
    }


def _run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def _installed_evaluator_commit(module_origin: str | None) -> str | None:
    if not module_origin:
        return None
    origin = Path(module_origin).resolve()
    for checkout in (origin.parent, *origin.parents):
        if (checkout / ".git").exists():
            result = _run(["git", "-C", str(checkout), "rev-parse", "HEAD"])
            return result.stdout.strip() if result is not None and result.returncode == 0 else None
    return None


def _cpuinfo() -> str:
    try:
        return Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return ""


def native_preflight(
    *, root: Path, environment_path: Path = DEFAULT_ENVIRONMENT,
) -> dict[str, Any]:
    """Report, but do not soften, the native Linux x86_64 requirements."""
    environment = load_goal3_environment(environment_path)
    reported_system = platform.system()
    reported_machine = platform.machine().lower()
    uname = os.uname()
    kernel_system = uname.sysname
    kernel_machine = uname.machine.lower()
    host_is_linux = reported_system == "Linux" and kernel_system == "Linux"
    architecture_matches_kernel = reported_machine == kernel_machine
    architecture_is_native = (
        reported_machine in NATIVE_ARCHITECTURES
        and kernel_machine in NATIVE_ARCHITECTURES
        and architecture_matches_kernel
    )
    cpuinfo = _cpuinfo()
    qemu_detected = any(marker in cpuinfo for marker in QEMU_MARKERS)
    docker_cli_present = shutil.which("docker") is not None
    docker = _run(["docker", "info", "--format", "{{.ServerVersion}}"])
    docker_daemon_available = docker is not None and docker.returncode == 0
    try:
        module = importlib.util.find_spec("swebench.harness.run_evaluation")
    except ModuleNotFoundError:
        module = None
    installed_commit = _installed_evaluator_commit(
        str(module.origin) if module is not None and module.origin else None,
    )
    evaluator_commit_matches = installed_commit == environment["commit"]
    git_status = _run(["git", "-C", str(root), "status", "--porcelain"])
    git_revision = _run(["git", "-C", str(root), "rev-parse", "HEAD"])
    worktree_clean = git_status is not None and git_status.returncode == 0 and not git_status.stdout.strip()
    commit = (
        git_revision.stdout.strip()
        if git_revision is not None and git_revision.returncode == 0
        and re.fullmatch(r"[0-9a-f]{40}", git_revision.stdout.strip())
        else None
    )
    valid = all((
        host_is_linux, architecture_is_native, not qemu_detected,
        docker_cli_present, docker_daemon_available, module is not None,
        evaluator_commit_matches, worktree_clean, commit is not None,
    ))
    return {
        "valid": valid,
        "host_system": reported_system,
        "kernel_system": kernel_system,
        "host_architecture": reported_machine,
        "kernel_architecture": kernel_machine,
        "architecture_matches_kernel": architecture_matches_kernel,
        "native_linux_x86_64": host_is_linux and architecture_is_native and not qemu_detected,
        "qemu_detected": qemu_detected,
        "docker_cli_present": docker_cli_present,
        "docker_daemon_available": docker_daemon_available,
        "docker_server_version": docker.stdout.strip() if docker_daemon_available and docker else None,
        "official_evaluator_module_available": module is not None,
        "expected_evaluator_commit": environment["commit"],
        "installed_evaluator_commit": installed_commit,
        "evaluator_commit_matches": evaluator_commit_matches,
        "worktree_clean": worktree_clean,
        "git_commit": commit,
    }


def require_native_preflight(*, root: Path, environment_path: Path = DEFAULT_ENVIRONMENT) -> dict[str, Any]:
    payload = native_preflight(root=root, environment_path=environment_path)
    if not payload["valid"]:
        raise ValueError("Goal 3 requires a clean native Linux x86_64 host, Docker, and the frozen official evaluator")
    return payload


def _require_goal3_path(path: Path) -> None:
    parts = path.resolve().parts
    if any(part.startswith("goal2") for part in parts):
        raise ValueError("Goal 3 may not write into a Goal 2 path")
    if not any(part.startswith("goal3") for part in parts):
        raise ValueError("Goal 3 paid artifacts require a Goal 3-specific path")


def collect_official_outcomes(report_root: Path, required_ids: set[str]) -> dict[str, bool]:
    """Read the official evaluator reports without inferring an outcome from text."""
    outcomes: dict[str, bool] = {}
    for path in report_root.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        for instance_id in required_ids:
            value = payload.get(instance_id)
            if isinstance(value, dict) and isinstance(value.get("resolved"), bool):
                previous = outcomes.setdefault(instance_id, value["resolved"])
                if previous != value["resolved"]:
                    raise ValueError(f"conflicting official outcomes for {instance_id}")
        for key, resolved in (
            ("resolved_ids", True), ("resolved_instances", True),
            ("unresolved_ids", False), ("unresolved_instances", False),
            ("empty_patch_ids", False),
        ):
            values = payload.get(key)
            if isinstance(values, list):
                for instance_id in set(map(str, values)) & required_ids:
                    previous = outcomes.setdefault(instance_id, resolved)
                    if previous != resolved:
                        raise ValueError(f"conflicting official outcomes for {instance_id}")
    if set(outcomes) != required_ids:
        raise ValueError(f"official evaluator reports are incomplete: {sorted(required_ids - set(outcomes))}")
    return outcomes


def collect_goal3_official_outcome(report_path: Path, instance_id: str) -> bool:
    """Validate one exact frozen-evaluator report for one Goal 3 Trial."""
    if not report_path.is_file():
        raise ValueError("official evaluator report is missing")
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("official evaluator report is unreadable") from exc
    if report_path.name != "report.json":
        if not isinstance(payload, dict) or payload.get("schema_version") != 2:
            raise ValueError("official evaluator summary report has an invalid schema")
        outcomes = collect_official_outcomes(report_path.parent, {instance_id})
        return outcomes[instance_id]
    candidates = sorted(report_path.parent.glob("report*.json"))
    if candidates != [report_path]:
        raise ValueError("official evaluator report candidates are ambiguous")
    required = {"patch_is_None", "patch_exists", "patch_successfully_applied", "resolved", "tests_status"}
    if not isinstance(payload, dict) or set(payload) != {instance_id}:
        raise ValueError("official evaluator report does not match the current instance")
    outcome = payload[instance_id]
    if not isinstance(outcome, dict) or not required.issubset(outcome):
        raise ValueError("official evaluator report has an incomplete schema")
    if not isinstance(outcome["resolved"], bool) or not isinstance(outcome["tests_status"], dict):
        raise ValueError("official evaluator report has an invalid outcome schema")
    return outcome["resolved"]


def ensure_new_paid_pilot_run(*, runs_dir: Path, run_id: str, ledger_path: Path) -> None:
    """Fail closed before a future paid Pilot invocation can repeat evidence."""
    _require_goal3_path(runs_dir)
    run_path = RunRecorder._run_path(runs_dir, run_id)
    if run_path.exists():
        if (run_path / "result.json").exists():
            raise ValueError("a terminal Goal 3 Run already exists; Provider execution is forbidden")
        raise ValueError("existing Goal 3 Run has ambiguous Provider state; manual verification is required")
    if ledger_path.exists():
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Goal 3 ledger cannot be read; manual verification is required") from exc
        if not isinstance(ledger, dict) or "active_reservation" not in ledger:
            raise ValueError("Goal 3 ledger is ambiguous; manual verification is required")
        if ledger["active_reservation"] is not None:
            raise ValueError("Goal 3 ledger has an active reservation; Provider execution is forbidden")


def build_pilot_manifest(*, root: Path, template_path: Path = DEFAULT_PILOT_TEMPLATE) -> RunManifest:
    template = load_goal3_pilot_template(template_path)
    return RunManifest(
        experiment_kind="goal3-swe-bench-live-pilot",
        provider="unverified", model_id="unverified", protocol="unverified",
        git_commit=current_git_commit(root), prompt_version="swe-bench-live-inference-v1",
        feature_flags={}, swe_evaluator_architecture="native", task_ids=[],
        retry_budget=0, fallback_enabled=False, max_iterations=50,
        model_parameters=template["model_parameters"],
        experiment_config_hash=canonical_hash(template),
        benchmark_asset_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    )


def dry_run(*, root: Path, runs_dir: Path = DEFAULT_RUNS_DIR, run_id: str = "goal3-dry") -> RunRecorder:
    _require_goal3_path(runs_dir)
    recorder = RunRecorder(runs_dir, build_pilot_manifest(root=root), run_id=run_id, repo_root=root)
    recorder.event("dry_run", {
        "model_called": False, "network_called": False,
        "provider_network_called": False, "paid_execution_enabled": False,
    })
    recorder.finalize({"status": "dry_run", "execution_mode": "dry_run", "scorable": False})
    return recorder


def _control_manifest(*, root: Path, control: Literal["empty", "gold"], instance_id: str) -> RunManifest:
    return RunManifest(
        experiment_kind=f"goal3-swe-bench-live-control-{control}",
        provider="none", model_id="none", protocol="none", git_commit=current_git_commit(root),
        prompt_version="swe-bench-live-control-v1", feature_flags={},
        swe_evaluator_architecture="native", task_ids=[instance_id], repetitions=1,
        retry_budget=0, fallback_enabled=False,
    )


def _instance(dataset_jsonl: Path, instance_id: str) -> dict[str, Any]:
    matches = [item for item in load_jsonl(dataset_jsonl) if str(item.get("instance_id")) == instance_id]
    if len(matches) != 1:
        raise ValueError("Goal 3 control requires one exact official instance")
    return matches[0]


def write_control_instance(*, output: Path, instance: dict[str, Any], instance_id: str) -> None:
    """Write one official dataset row for controls without changing its task data."""
    if instance.get("instance_id") != instance_id:
        raise ValueError("official control instance identity mismatch")
    if not isinstance(instance.get("patch"), str):
        raise ValueError("official control instance has no gold patch")
    output.write_text(json.dumps(instance, default=str) + "\n", encoding="utf-8")


def run_control(
    *, root: Path, dataset_jsonl: Path, instance_id: str,
    control: Literal["empty", "gold"], runs_dir: Path = DEFAULT_CONTROL_RUNS_DIR,
    run_id: str, environment_path: Path = DEFAULT_ENVIRONMENT,
) -> RunRecorder:
    """Run one evaluator-only control; no model or Provider path is imported."""
    _require_goal3_path(runs_dir)
    preflight = require_native_preflight(root=root, environment_path=environment_path)
    instance = _instance(dataset_jsonl, instance_id)
    gold_patch = instance.get("patch")
    if control == "gold" and (not isinstance(gold_patch, str) or not gold_patch.strip()):
        raise ValueError("Goal 3 gold control requires the official gold patch")
    patch = "" if control == "empty" else gold_patch
    expected_resolved = control == "gold"
    recorder = RunRecorder(
        runs_dir, _control_manifest(root=root, control=control, instance_id=instance_id),
        run_id=run_id, repo_root=root,
    )
    prediction = [{
        "instance_id": instance_id,
        "model_name_or_path": f"goal3-control-{control}",
        "model_patch": patch,
    }]
    recorder.write_json("predictions.json", prediction)
    recorder.event("control_started", {
        "control": control, "instance_id": instance_id, "model_called": False,
        "network_called": False, "provider_network_called": False,
        "expected_resolved": expected_resolved, "evaluator_commit": preflight["installed_evaluator_commit"],
    })
    report_dir = recorder.path / "evaluation_results"
    report_dir.mkdir()
    try:
        result = run_official_evaluator(
            dataset_name="SWE-bench-Live/SWE-bench-Live", split="lite",
            predictions_path=recorder.path / "predictions.json", instance_ids=[instance_id],
            max_workers=1, run_id=run_id, namespace="starryzhang", report_dir=report_dir,
            cwd=recorder.path, evaluator_architecture="native",
        )
        recorder.write_artifact("test-output.txt", (result.stdout or "") + "\n" + (result.stderr or ""))
        if result.returncode != 0:
            raise ValueError(f"official evaluator failed with exit status {result.returncode}")
        resolved = collect_official_outcomes(recorder.path, {instance_id})[instance_id]
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        recorder.event("control_completed", {
            "control": control, "instance_id": instance_id, "evaluator_completed": False,
            "expected_resolved": expected_resolved, "error": str(exc),
        })
        recorder.finalize({"status": "infrastructure_error", "execution_mode": "control", "scorable": False})
        return recorder
    expectation_met = resolved == expected_resolved
    recorder.event("trial_completed", {
        "task_id": instance_id, "repetition_id": "1", "attempt_id": 1,
        "status": "success" if resolved else "task_failure", "official_outcome": resolved,
        "control": control, "control_expectation_met": expectation_met,
    })
    recorder.event("control_completed", {
        "control": control, "instance_id": instance_id, "evaluator_completed": True,
        "expected_resolved": expected_resolved, "resolved": resolved,
        "model_called": False, "network_called": False, "provider_network_called": False,
        "host_os": preflight["host_system"], "host_architecture": preflight["host_architecture"],
        "docker_version": preflight["docker_server_version"],
        "evaluator_commit": preflight["installed_evaluator_commit"],
    })
    recorder.finalize({
        "status": "success" if expectation_met else "task_failure", "execution_mode": "control",
        "scorable": False, "official_evaluator_completed": True, "resolved": resolved,
        "expected_resolved": expected_resolved,
    })
    return recorder


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Goal 3 native SWE-bench-Live bootstrap")
    parser.add_argument(
        "command",
        choices=[
            "preflight", "validate", "dry-run", "control-empty", "control-gold",
            "paid-preflight", "prepare-paid-artifacts", "execute-pilot",
            "freeze-paid-pilot-bundle",
        ],
    )
    parser.add_argument("--environment", type=Path, default=DEFAULT_ENVIRONMENT)
    parser.add_argument("--pilot-template", type=Path, default=DEFAULT_PILOT_TEMPLATE)
    parser.add_argument("--dataset-jsonl", type=Path)
    parser.add_argument("--instance-id")
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    parser.add_argument("--control-runs-dir", type=Path, default=DEFAULT_CONTROL_RUNS_DIR)
    parser.add_argument("--run-id", default="goal3-dry")
    parser.add_argument("--pilot-freeze", type=Path, default=GOAL3_PILOT_FREEZE)
    parser.add_argument("--pricing-snapshot", type=Path, default=GOAL3_PRICING_SNAPSHOT)
    parser.add_argument("--budget-authorization", type=Path, default=GOAL3_BUDGET_AUTHORIZATION)
    parser.add_argument("--budget-ledger", type=Path, default=GOAL3_BUDGET_LEDGER)
    parser.add_argument("--budget-allocation", type=Path, default=GOAL3_BUDGET_ALLOCATION)
    parser.add_argument("--authorized-total-cny", type=Decimal)
    parser.add_argument("--freeze-output-dir", type=Path)
    parser.add_argument("--dataset-revision")
    parser.add_argument("--confirm-paid-run", action="store_true")
    args = parser.parse_args(argv)
    root = Path.cwd()
    try:
        if args.command == "preflight":
            payload = require_native_preflight(root=root, environment_path=args.environment)
        elif args.command == "validate":
            environment = load_goal3_environment(args.environment)
            template = load_goal3_pilot_template(args.pilot_template)
            payload = {"valid": True, "environment_commit": environment["commit"], "pilot_status": template["status"]}
        elif args.command == "dry-run":
            payload = {"valid": True, "run_path": str(dry_run(root=root, runs_dir=args.runs_dir, run_id=args.run_id).path)}
        elif args.command == "paid-preflight":
            if args.dataset_jsonl is None:
                raise ValueError("paid preflight requires --dataset-jsonl")
            payload = paid_preflight(
                root=root, dataset_jsonl=args.dataset_jsonl, pilot_freeze_path=args.pilot_freeze,
                pricing_snapshot=args.pricing_snapshot, budget_authorization=args.budget_authorization,
                budget_ledger=args.budget_ledger, budget_allocation=args.budget_allocation,
            )
        elif args.command == "execute-pilot":
            if args.dataset_jsonl is None:
                raise ValueError("paid execution requires --dataset-jsonl")
            payload = {"valid": True, "run_path": str(execute_pilot(
                root=root, dataset_jsonl=args.dataset_jsonl, runs_dir=args.runs_dir,
                run_id=args.run_id, pilot_freeze_path=args.pilot_freeze,
                pricing_snapshot=args.pricing_snapshot,
                budget_authorization=args.budget_authorization,
                budget_ledger=args.budget_ledger, budget_allocation=args.budget_allocation,
                confirmed=args.confirm_paid_run,
            ).path)}
        elif args.command == "prepare-paid-artifacts":
            if args.authorized_total_cny is None:
                raise ValueError("paid artifact preparation requires --authorized-total-cny")
            payload = create_paid_artifacts(
                root=root, pilot_freeze_path=args.pilot_freeze,
                pricing_snapshot=args.pricing_snapshot,
                budget_authorization=args.budget_authorization,
                budget_ledger=args.budget_ledger,
                budget_allocation=args.budget_allocation,
                authorized_total_cny=args.authorized_total_cny,
            )
        elif args.command == "freeze-paid-pilot-bundle":
            if args.dataset_jsonl is None or args.freeze_output_dir is None or args.dataset_revision is None:
                raise ValueError("paid Pilot freeze requires --dataset-jsonl, --dataset-revision, and --freeze-output-dir")
            payload = freeze_paid_pilot_bundle(
                root=root, dataset_jsonl=args.dataset_jsonl,
                pricing_snapshot=args.pricing_snapshot,
                output_dir=args.freeze_output_dir, dataset_revision=args.dataset_revision,
            )
        else:
            if args.dataset_jsonl is None or not args.instance_id:
                raise ValueError("control requires --dataset-jsonl and --instance-id")
            control = "empty" if args.command == "control-empty" else "gold"
            payload = {"valid": True, "run_path": str(run_control(
                root=root, dataset_jsonl=args.dataset_jsonl, instance_id=args.instance_id,
                control=control, runs_dir=args.control_runs_dir, run_id=args.run_id,
                environment_path=args.environment,
            ).path)}
        print(json.dumps(payload, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"Goal 3 SWE error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

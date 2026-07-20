"""Goal 4 formal SWE-bench-Live execution with independent evidence and budgets.

This module deliberately leaves Goal 2 and Goal 3 artifacts and runners untouched.
It freezes a twenty-instance matrix once, executes the two registered batches
serially, and keeps one parent authorization with non-transferable child budgets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml

from codepacex.experiments import ExperimentProfile
from codepacex.tools import create_default_registry
from evals.benchmark import RunManifest, RunRecorder, canonical_hash, current_git_commit, sanitize_origin
from evals.costing import PricingSnapshot, load_pricing, pricing_snapshot_hash
from evals.goal3_swe import (
    ENVIRONMENT,
    GOAL3_MODEL_PARAMETERS,
    _child_environment,
    _goal3_extract_patch,
    _goal3_inference_prompt,
    _goal3_materialize_instance,
    _ingest_trace,
    _provider_payload,
    _runtime_secrets,
    collect_official_outcomes as collect_goal3_control_outcomes,
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
    worst_case_reservation,
)
from evals.permission_study import trace_usage
from evals.pilot import PilotConfig
from evals.swe_inference import collect_official_outcomes
from evals.swe_bench_live import (
    FORMAL_SIZE_TARGETS,
    instance_payload_hash,
    load_jsonl,
    official_evaluator_report_path,
    patch_file_count,
    run_official_evaluator,
    size_bucket,
)


DEFAULT_RUNS_DIR = Path("evals/.runs/goal4-swe")
DEFAULT_CONTROL_RUNS_DIR = Path("evals/.runs/goal4-swe-control")
DEFAULT_ENVIRONMENT = Path("evals/goal4/swe_official_environment.json")
DEFAULT_FORMAL_TEMPLATE = Path("evals/goal4/formal.template.json")
NATIVE_ARCHITECTURES = {"x86_64", "amd64"}
GOAL3_PILOT_IDS = {
    "aiogram__aiogram-1594",
    "amoffat__sh-744",
    "arviz-devs__arviz-2413",
}
MAXIMUM_REQUESTS_PER_INSTANCE = 40
MAXIMUM_INPUT_TOKENS_PER_REQUEST = 128_000
MAXIMUM_OUTPUT_TOKENS_PER_REQUEST = 8192
MAXIMUM_REASONING_TOKENS_PER_REQUEST = 6144
BATCH_TARGETS = {
    "A": {"one_file": 2, "two_to_four_files": 2, "five_plus_files": 1},
    "B": {"one_file": 6, "two_to_four_files": 6, "five_plus_files": 3},
}
BATCH_AUTHORIZATION = {
    "A": Decimal("421.109760"),
    "B": Decimal("1263.329280"),
}
PARENT_AUTHORIZATION = Decimal("1684.439040")
SAFETY_RATIO = Decimal("0.15")
FORMAL_SELECTION_ALGORITHM = "goal4-python-lite-size-stratified-v1"
FORMAL_MODEL_PARAMETERS = {
    "temperature": None,
    "top_p": None,
    "max_output_tokens": None,
    "max_completion_tokens": MAXIMUM_OUTPUT_TOKENS_PER_REQUEST,
    "enable_thinking": True,
    "thinking_budget": MAXIMUM_REASONING_TOKENS_PER_REQUEST,
}
EXECUTION_INSTANCE_FIELDS = (
    "instance_id", "repo", "base_commit", "problem_statement", "platform",
    "version", "environment_setup_commit",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_hash(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD^{tree}"],
        text=True, capture_output=True, check=False,
    )
    value = result.stdout.strip()
    if result.returncode or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("Goal 4 requires an exact Git tree hash")
    return value


def _require_goal4_path(path: Path) -> None:
    if not any(part.startswith("goal4") for part in path.resolve().parts):
        raise ValueError("Goal 4 artifacts require a Goal 4-specific path")
    if any(part.startswith("goal2") or part.startswith("goal3") for part in path.resolve().parts):
        raise ValueError("Goal 4 may not write into Goal 2 or Goal 3 paths")


def _execution_payload(instance: dict[str, Any]) -> dict[str, Any]:
    return {field: instance.get(field) for field in EXECUTION_INSTANCE_FIELDS}


def _execution_payload_hash(instance: dict[str, Any]) -> str:
    return canonical_hash(_execution_payload(instance))


def _goal4_profile() -> ExperimentProfile:
    return ExperimentProfile(
        tool_loading="deferred", compression_profile="recovery_v1",
        permission_strategy="session_allow", agent_mode="single",
    )


def _tool_schema_hash() -> str:
    """Hash the exact built-in schemas sent by the frozen openai-compat profile."""
    return canonical_hash(create_default_registry().get_all_schemas("openai-compat"))


def _task_record(instance: dict[str, Any], batch: Literal["A", "B"]) -> dict[str, Any]:
    return {
        "instance_id": str(instance["instance_id"]),
        "repo": str(instance["repo"]),
        "batch": batch,
        "size_bucket": size_bucket(instance),
        "gold_file_count": patch_file_count(str(instance.get("patch", ""))),
        "payload_sha256": instance_payload_hash(instance),
        "execution_payload_sha256": _execution_payload_hash(instance),
    }


def select_formal_matrix(instances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the fully pre-registered twenty task matrix without discretion."""
    selected: list[dict[str, Any]] = []
    counts = {bucket: 0 for bucket in FORMAL_SIZE_TARGETS}
    repositories: dict[str, int] = {}
    for instance in sorted(instances, key=lambda item: str(item.get("instance_id", ""))):
        instance_id = str(instance.get("instance_id", ""))
        repo = str(instance.get("repo", ""))
        if not instance_id or not repo or instance_id in GOAL3_PILOT_IDS:
            continue
        if instance.get("platform", "linux") != "linux" or repositories.get(repo, 0) >= 2:
            continue
        bucket = size_bucket(instance)
        if counts[bucket] >= FORMAL_SIZE_TARGETS[bucket]:
            continue
        selected.append(instance)
        counts[bucket] += 1
        repositories[repo] = repositories.get(repo, 0) + 1
    if counts != FORMAL_SIZE_TARGETS:
        raise ValueError(f"Goal 4 dataset cannot satisfy the frozen 8/8/4 matrix: {counts}")
    if len(selected) != 20 or len({str(item["instance_id"]) for item in selected}) != 20:
        raise ValueError("Goal 4 formal matrix is not uniquely twenty instances")
    return selected


def assign_batches(formal: list[dict[str, Any]]) -> list[tuple[dict[str, Any], Literal["A", "B"]]]:
    """Assign the frozen 2/2/1 and 6/6/3 batches deterministically."""
    by_bucket = {bucket: [] for bucket in FORMAL_SIZE_TARGETS}
    for item in formal:
        by_bucket[size_bucket(item)].append(item)
    batch_a_ids: set[str] = set()
    for bucket, count in BATCH_TARGETS["A"].items():
        choices = sorted(by_bucket[bucket], key=lambda item: str(item["instance_id"]))
        if len(choices) < count:
            raise ValueError(f"Goal 4 cannot assign Batch A bucket: {bucket}")
        batch_a_ids.update(str(item["instance_id"]) for item in choices[:count])
    assigned = [
        (item, "A" if str(item["instance_id"]) in batch_a_ids else "B")
        for item in formal
    ]
    for batch, targets in BATCH_TARGETS.items():
        actual = {bucket: 0 for bucket in targets}
        for item, assigned_batch in assigned:
            if assigned_batch == batch:
                actual[size_bucket(item)] += 1
        if actual != targets:
            raise ValueError(f"Goal 4 batch distribution changed for {batch}: {actual}")
    return sorted(assigned, key=lambda pair: (pair[1], str(pair[0]["instance_id"])))


def _formal_required_fields() -> set[str]:
    return {
        "schema_version", "status", "experiment_kind", "codepacex_commit", "codepacex_tree",
        "official_evaluator_commit", "dataset", "dataset_split", "dataset_revision",
        "dataset_source_sha256", "selection_algorithm", "matrix_sha256", "provider",
        "protocol", "base_url", "api_key_env", "model_id", "pricing_snapshot_hash",
        "experiment_profile", "fallback_enabled", "retry_budget", "serial",
        "max_provider_requests_per_instance", "maximum_input_tokens_per_request",
        "maximum_output_tokens_per_request", "maximum_reasoning_tokens_per_request",
        "model_parameters", "tasks", "prompt_sha256", "tool_schema_sha256",
    }


def freeze_formal_bundle(
    *, root: Path, dataset_jsonl: Path, pricing_snapshot: Path, output_dir: Path,
    dataset_revision: str,
) -> dict[str, Any]:
    """Write one immutable, Agent-safe Goal 4 formal contract and matrix."""
    root, output_dir = root.resolve(), output_dir.resolve()
    _require_goal4_path(output_dir)
    if not re.fullmatch(r"[0-9a-f]{40}", dataset_revision):
        raise ValueError("Goal 4 requires the exact dataset revision")
    rows = load_jsonl(dataset_jsonl)
    formal = select_formal_matrix(rows)
    assignments = assign_batches(formal)
    pricing = load_pricing(pricing_snapshot)
    profile = _goal4_profile()
    tasks = [_task_record(instance, batch) for instance, batch in assignments]
    matrix = {
        "dataset_revision": dataset_revision,
        "dataset_source_sha256": canonical_hash(rows),
        "selection_algorithm": FORMAL_SELECTION_ALGORITHM,
        "excluded_goal3_pilot_ids": sorted(GOAL3_PILOT_IDS),
        "tasks": tasks,
    }
    prompt = _goal3_inference_prompt({"problem_statement": "Goal 4 frozen task"}).split("\n\n", 1)[0]
    frozen = {
        "schema_version": 1,
        "status": "frozen_pending_authorization",
        "experiment_kind": "goal4-swe-bench-live-formal",
        "codepacex_commit": current_git_commit(root),
        "codepacex_tree": _tree_hash(root),
        "official_evaluator_commit": ENVIRONMENT["commit"],
        "dataset": ENVIRONMENT["dataset"],
        "dataset_split": ENVIRONMENT["split"],
        "dataset_revision": dataset_revision,
        "dataset_source_sha256": canonical_hash(rows),
        "selection_algorithm": FORMAL_SELECTION_ALGORITHM,
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
        "maximum_reasoning_tokens_per_request": MAXIMUM_REASONING_TOKENS_PER_REQUEST,
        "model_parameters": FORMAL_MODEL_PARAMETERS,
        "tasks": tasks,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "tool_schema_sha256": _tool_schema_hash(),
    }
    if set(frozen) != _formal_required_fields():
        raise ValueError("Goal 4 formal freeze schema drift")
    paths = {
        "freeze": output_dir / "formal-freeze.json",
        "pricing": output_dir / "pricing-snapshot.json",
        "dataset": output_dir / "formal-dataset.jsonl",
        "matrix": output_dir / "formal-matrix.json",
    }
    if any(path.exists() for path in paths.values()):
        raise ValueError("Goal 4 formal freeze bundle already exists")
    output_dir.mkdir(parents=True, exist_ok=True)
    paths["freeze"].write_text(json.dumps(frozen, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    paths["pricing"].write_bytes(pricing_snapshot.read_bytes())
    paths["matrix"].write_text(json.dumps(matrix, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    rows_by_id = {str(item["instance_id"]): item for item in rows}
    paths["dataset"].write_text("".join(
        json.dumps(_execution_payload(rows_by_id[task["instance_id"]]), ensure_ascii=False, sort_keys=True) + "\n"
        for task in tasks
    ), encoding="utf-8")
    return frozen


def load_formal_freeze(path: Path, *, root: Path | None = None) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != _formal_required_fields():
        raise ValueError("Goal 4 freeze has an invalid schema")
    if payload["schema_version"] != 1 or payload["status"] != "frozen_pending_authorization":
        raise ValueError("Goal 4 freeze is not pending authorization")
    if payload["experiment_kind"] != "goal4-swe-bench-live-formal":
        raise ValueError("Goal 4 freeze experiment identity changed")
    if root is not None and payload["codepacex_commit"] != current_git_commit(root):
        raise ValueError("Goal 4 freeze does not match the current commit")
    if payload["official_evaluator_commit"] != ENVIRONMENT["commit"]:
        raise ValueError("Goal 4 evaluator identity changed")
    if payload["dataset"] != ENVIRONMENT["dataset"] or payload["dataset_split"] != ENVIRONMENT["split"]:
        raise ValueError("Goal 4 dataset identity changed")
    if payload["fallback_enabled"] or payload["retry_budget"] != 0 or not payload["serial"]:
        raise ValueError("Goal 4 requires serial no-fallback no-retry execution")
    if payload["max_provider_requests_per_instance"] != MAXIMUM_REQUESTS_PER_INSTANCE:
        raise ValueError("Goal 4 request ceiling changed")
    if payload["maximum_input_tokens_per_request"] != MAXIMUM_INPUT_TOKENS_PER_REQUEST:
        raise ValueError("Goal 4 input ceiling changed")
    if payload["maximum_output_tokens_per_request"] != MAXIMUM_OUTPUT_TOKENS_PER_REQUEST:
        raise ValueError("Goal 4 completion ceiling changed")
    if payload["maximum_reasoning_tokens_per_request"] != MAXIMUM_REASONING_TOKENS_PER_REQUEST:
        raise ValueError("Goal 4 reasoning ceiling changed")
    if payload["model_parameters"] != FORMAL_MODEL_PARAMETERS:
        raise ValueError("Goal 4 model parameters changed")
    profile = ExperimentProfile.model_validate(payload["experiment_profile"])
    if profile.canonical_payload() != payload["experiment_profile"]:
        raise ValueError("Goal 4 experiment profile changed")
    tasks = payload["tasks"]
    if not isinstance(tasks, list) or len(tasks) != 20:
        raise ValueError("Goal 4 requires exactly twenty frozen tasks")
    seen: set[str] = set()
    distributions = {batch: {bucket: 0 for bucket in targets} for batch, targets in BATCH_TARGETS.items()}
    repos: dict[str, int] = {}
    for task in tasks:
        if not isinstance(task, dict) or set(task) != {
            "instance_id", "repo", "batch", "size_bucket", "gold_file_count", "payload_sha256", "execution_payload_sha256",
        }:
            raise ValueError("Goal 4 task record is invalid")
        instance_id, batch, bucket = task["instance_id"], task["batch"], task["size_bucket"]
        if not isinstance(instance_id, str) or not instance_id or instance_id in seen or instance_id in GOAL3_PILOT_IDS:
            raise ValueError("Goal 4 task IDs are invalid or overlap Goal 3")
        if batch not in BATCH_TARGETS or bucket not in FORMAL_SIZE_TARGETS:
            raise ValueError("Goal 4 task batch or size bucket is invalid")
        if not isinstance(task["repo"], str) or not task["repo"]:
            raise ValueError("Goal 4 task repository is invalid")
        repos[task["repo"]] = repos.get(task["repo"], 0) + 1
        if repos[task["repo"]] > 2:
            raise ValueError("Goal 4 matrix exceeds the repository ceiling")
        if not all(isinstance(task[key], str) and re.fullmatch(r"[0-9a-f]{64}", task[key]) for key in ("payload_sha256", "execution_payload_sha256")):
            raise ValueError("Goal 4 task hashes are invalid")
        distributions[batch][bucket] += 1
        seen.add(instance_id)
    if distributions != BATCH_TARGETS:
        raise ValueError(f"Goal 4 batch distribution changed: {distributions}")
    return payload


def load_formal_instances(*, frozen: dict[str, Any], dataset_jsonl: Path) -> list[dict[str, Any]]:
    rows = {str(item.get("instance_id", "")): item for item in load_jsonl(dataset_jsonl)}
    if len(rows) != 20:
        raise ValueError("Goal 4 agent dataset must contain exactly twenty rows")
    selected: list[dict[str, Any]] = []
    for task in frozen["tasks"]:
        row = rows.get(task["instance_id"])
        if row is None or set(row) != set(EXECUTION_INSTANCE_FIELDS):
            raise ValueError(f"Goal 4 agent dataset is invalid: {task['instance_id']}")
        if row.get("repo") != task["repo"] or _execution_payload_hash(row) != task["execution_payload_sha256"]:
            raise ValueError(f"Goal 4 agent payload changed: {task['instance_id']}")
        selected.append(row)
    return selected


def _accounts_dir(root: Path) -> Path:
    return root / "accounts"


def _batch_paths(root: Path, batch: Literal["A", "B"]) -> dict[str, Path]:
    name = batch.lower()
    accounts = _accounts_dir(root)
    return {
        "authorization": accounts / f"batch-{name}-authorization.json",
        "ledger": accounts / f"batch-{name}-ledger.json",
        "allocation": accounts / f"batch-{name}-allocation.json",
    }


def _parent_paths(root: Path) -> dict[str, Path]:
    accounts = _accounts_dir(root)
    return {
        "authorization": accounts / "parent-authorization.json",
        "ledger": accounts / "parent-ledger.json",
    }


def _batch_execution_ceiling(pricing: PricingSnapshot, batch: Literal["A", "B"]) -> Decimal:
    task_count = sum(BATCH_TARGETS[batch].values())
    return worst_case_reservation(
        pricing, maximum_requests=task_count * MAXIMUM_REQUESTS_PER_INSTANCE,
        maximum_input_tokens_per_request=MAXIMUM_INPUT_TOKENS_PER_REQUEST,
        maximum_output_tokens_per_request=MAXIMUM_OUTPUT_TOKENS_PER_REQUEST,
    )


def _write_json_new(path: Path, payload: Any) -> None:
    if path.exists():
        raise ValueError(f"Goal 4 immutable artifact already exists: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _child_authorization(*, root: Path, pricing: PricingSnapshot, batch: Literal["A", "B"]) -> BudgetAuthorization:
    total = BATCH_AUTHORIZATION[batch]
    return BudgetAuthorization(
        authorized_total_cny=total,
        stage_limits_cny={"A": total, "B": total, "C": total},
        pricing_snapshot_hash=pricing_snapshot_hash(pricing),
        experiment_commit=current_git_commit(root),
        authorized_at="authorized-by-goal4-user-instruction",
        authorized_by="user",
    )


def prepare_paid_artifacts(*, root: Path, freeze_path: Path, pricing_path: Path, evidence_root: Path) -> dict[str, Any]:
    """Create immutable Goal 4 parent and child accounting files without a call."""
    root, evidence_root = root.resolve(), evidence_root.resolve()
    _require_goal4_path(evidence_root)
    frozen = load_formal_freeze(freeze_path, root=root)
    pricing = load_pricing(pricing_path)
    if pricing_snapshot_hash(pricing) != frozen["pricing_snapshot_hash"]:
        raise ValueError("Goal 4 pricing snapshot does not match freeze")
    parent_paths = _parent_paths(evidence_root)
    child_paths = {batch: _batch_paths(evidence_root, batch) for batch in ("A", "B")}
    all_paths = [*parent_paths.values(), *(path for paths in child_paths.values() for path in paths.values())]
    if any(path.exists() for path in all_paths):
        raise ValueError("Goal 4 paid accounting artifacts already exist")
    execution = {batch: _batch_execution_ceiling(pricing, batch) for batch in ("A", "B")}
    for batch in ("A", "B"):
        expected = _money(execution[batch] * (Decimal("1") + SAFETY_RATIO))
        if expected != BATCH_AUTHORIZATION[batch]:
            raise ValueError(f"Goal 4 {batch} authorization no longer covers frozen worst case")
    if sum(BATCH_AUTHORIZATION.values(), Decimal("0")) != PARENT_AUTHORIZATION:
        raise ValueError("Goal 4 parent authorization does not equal child ceilings")
    parent = {
        "schema_version": 1,
        "currency": "CNY",
        "authorized_total_cny": str(PARENT_AUTHORIZATION),
        "experiment_commit": frozen["codepacex_commit"],
        "pricing_snapshot_hash": frozen["pricing_snapshot_hash"],
        "children": {},
        "active_reservation": None,
        "spent_cny": "0.000000",
        "status": "prepared_zero_provider",
    }
    for batch in ("A", "B"):
        authorization = _child_authorization(root=root, pricing=pricing, batch=batch)
        auth_hash = authorization_hash(authorization)
        ledger = BudgetLedger(authorization_hash=auth_hash, updated_at="prepared-zero-provider")
        allocation = StageCBudgetAllocation(
            experiment_commit=frozen["codepacex_commit"],
            pricing_snapshot_hash=frozen["pricing_snapshot_hash"],
            baseline_ledger_sha256=ledger_fingerprint(ledger),
            baseline_authorization_hash=auth_hash,
            baseline_spent_cny=Decimal("0"),
            baseline_request_charge_count=0,
            baseline_settlement_count=0,
            baseline_budget_block_count=0,
            baseline_rebind_count=0,
            safety_reserve_cny=_money(BATCH_AUTHORIZATION[batch] - execution[batch]),
            spendable_total_cny=execution[batch],
            category_limits_cny={
                "swe": execution[batch], "mcp": Decimal("0"), "retention": Decimal("0"),
                "permission": Decimal("0"), "multi_agent": Decimal("0"), "long_session": Decimal("0"),
            },
        )
        paths = child_paths[batch]
        _write_json_new(paths["authorization"], authorization.model_dump(mode="json"))
        _write_json_new(paths["ledger"], ledger.model_dump(mode="json"))
        _write_json_new(paths["allocation"], allocation.model_dump(mode="json"))
        parent["children"][batch] = {
            "authorized_total_cny": str(BATCH_AUTHORIZATION[batch]),
            "spendable_total_cny": str(execution[batch]),
            "safety_reserve_cny": str(allocation.safety_reserve_cny),
            "authorization_sha256": auth_hash,
            "allocation_sha256": allocation_hash(allocation),
            "ledger_path": str(paths["ledger"].relative_to(evidence_root)),
        }
    _write_json_new(parent_paths["authorization"], parent)
    update_parent_ledger(evidence_root)
    return {"valid": True, "parent_authorization_cny": str(PARENT_AUTHORIZATION), "batches": parent["children"]}


def _load_child_ledger(path: Path) -> BudgetLedger:
    return BudgetLedger.model_validate_json(path.read_text(encoding="utf-8"))


def update_parent_ledger(evidence_root: Path) -> dict[str, Any]:
    evidence_root = evidence_root.resolve()
    parent = json.loads(_parent_paths(evidence_root)["authorization"].read_text(encoding="utf-8"))
    totals = {"spent_cny": Decimal("0"), "charges": 0, "settlements": 0, "budget_blocks": 0}
    children: dict[str, Any] = {}
    active = None
    for batch in ("A", "B"):
        ledger = _load_child_ledger(_batch_paths(evidence_root, batch)["ledger"])
        totals["spent_cny"] += ledger.spent_cny
        totals["charges"] += len(ledger.request_charges)
        totals["settlements"] += len(ledger.settlements)
        totals["budget_blocks"] += len(ledger.budget_blocks)
        if ledger.active_reservation is not None:
            active = {"batch": batch, **ledger.active_reservation.model_dump(mode="json")}
        children[batch] = {
            "spent_cny": str(ledger.spent_cny),
            "request_charge_count": len(ledger.request_charges),
            "settlement_count": len(ledger.settlements),
            "budget_block_count": len(ledger.budget_blocks),
            "active_reservation": ledger.active_reservation.model_dump(mode="json") if ledger.active_reservation else None,
            "ledger_sha256": _sha256(_batch_paths(evidence_root, batch)["ledger"]),
        }
    if totals["spent_cny"] > PARENT_AUTHORIZATION:
        raise ValueError("Goal 4 child ledgers exceed the parent authorization")
    payload = {
        "schema_version": 1,
        "currency": "CNY",
        "parent_authorization_sha256": _sha256(_parent_paths(evidence_root)["authorization"]),
        "authorized_total_cny": str(PARENT_AUTHORIZATION),
        "spent_cny": str(_money(totals["spent_cny"])),
        "remaining_cny": str(_money(PARENT_AUTHORIZATION - totals["spent_cny"])),
        "active_reservation": active,
        "request_charge_count": totals["charges"],
        "settlement_count": totals["settlements"],
        "budget_block_count": totals["budget_blocks"],
        "children": children,
    }
    target = _parent_paths(evidence_root)["ledger"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def zero_provider_check(*, root: Path, freeze_path: Path, pricing_path: Path, evidence_root: Path) -> dict[str, Any]:
    """Validate prepared accounting and freeze state without materializing a Trial."""
    frozen = load_formal_freeze(freeze_path, root=root.resolve())
    pricing = load_pricing(pricing_path)
    if pricing_snapshot_hash(pricing) != frozen["pricing_snapshot_hash"]:
        raise ValueError("Goal 4 pricing snapshot mismatch during zero-provider check")
    parent = update_parent_ledger(evidence_root)
    if parent["spent_cny"] != "0.000000" or parent["request_charge_count"] or parent["settlement_count"]:
        raise ValueError("zero-provider check found paid evidence")
    if parent["active_reservation"] is not None or parent["budget_block_count"]:
        raise ValueError("zero-provider accounting is not clean")
    return {
        "valid": True, "provider_requests": 0, "usage": 0, "cost_cny": "0.000000",
        "trials": 0, "predictions": 0, "evaluator_executions": 0,
        "active_reservation": None, "matrix_sha256": frozen["matrix_sha256"],
    }


def _pilot_config(frozen: dict[str, Any]) -> PilotConfig:
    # PilotConfig v2 fixes this compatibility field at 50. The actual formal
    # Agent limit is passed explicitly to the CLI and remains frozen at 40.
    return PilotConfig.model_validate({
        "schema_version": 2, "experiment_kind": "pilot", "provider": frozen["provider"],
        "protocol": frozen["protocol"], "base_url": frozen["base_url"],
        "api_key_env": frozen["api_key_env"], "model_id": frozen["model_id"],
        "fallback_enabled": False, "model_parameters": frozen["model_parameters"],
        "retry_budget": 0, "task_ids": [], "repetitions": 1, "feature_flags": {},
        "experiment_profile": frozen["experiment_profile"],
        "max_iterations": 50,
    })


def _write_child_config(pilot: PilotConfig, home: Path) -> None:
    payload = _provider_payload(pilot)
    payload["sandbox"] = {"enabled": False, "auto_allow": False, "network_enabled": False}
    config_dir = home / ".codepacex"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")


def _trial_id(run_id: str, batch: str, instance_id: str) -> str:
    return f"swe/goal4/{run_id}/batch-{batch.lower()}/1/{instance_id}"


def _manifest(*, root: Path, frozen: dict[str, Any], run_id: str, batch: str) -> RunManifest:
    profile = ExperimentProfile.model_validate(frozen["experiment_profile"])
    tasks = [task["instance_id"] for task in frozen["tasks"] if task["batch"] == batch]
    return RunManifest(
        experiment_kind="goal4-swe-bench-live-formal", provider=frozen["provider"],
        protocol=frozen["protocol"], base_url_origin=sanitize_origin(frozen["base_url"]),
        api_key_env=frozen["api_key_env"], model_id=frozen["model_id"], run_id=run_id,
        git_commit=current_git_commit(root), prompt_version="swe-bench-live-inference-v1",
        feature_flags={}, swe_evaluator_architecture="native", experiment_profile=profile.canonical_payload(),
        experiment_profile_hash=profile.profile_hash(), runtime_contract_hash=profile.runtime_contract_hash(),
        benchmark_asset_hash=canonical_hash({"matrix_sha256": frozen["matrix_sha256"], "batch": batch}),
        task_ids=tasks, repetitions=1, model_parameters=frozen["model_parameters"],
        max_output_tokens=MAXIMUM_OUTPUT_TOKENS_PER_REQUEST, retry_budget=0,
        fallback_enabled=False, max_iterations=MAXIMUM_REQUESTS_PER_INSTANCE,
        pricing_snapshot_hash=frozen["pricing_snapshot_hash"],
        experiment_config_hash=canonical_hash({"freeze": frozen, "batch": batch}),
    )


def _terminal(
    recorder: RunRecorder, *, instance_id: str, trial_id: str, status: str,
    started: float, accounting: dict[str, Any], **extra: Any,
) -> None:
    if accounting.get("trial_id") != trial_id:
        raise ValueError("Goal 4 terminal accounting does not match its Trial ID")
    recorder.event("trial_completed", {
        "task_id": instance_id, "repetition_id": "1", "attempt_id": 1,
        "trial_id": trial_id, "status": status,
        "duration_seconds": time.monotonic() - started,
        "provider_request_count": accounting.get("request_count", 0),
        "actual_cny": accounting.get("actual_cny", "0"), **extra,
    })


def _gate(*, root: Path, evidence_root: Path, pricing: PricingSnapshot, batch: Literal["A", "B"]) -> PaidRunGate:
    paths = _batch_paths(evidence_root, batch)
    return PaidRunGate(
        root=root, authorization_path=paths["authorization"], ledger_path=paths["ledger"],
        allocation_path=paths["allocation"], pricing_path=evidence_root / "pricing-snapshot.json",
        pricing=pricing, stage="C",
    )


def paid_preflight(*, root: Path, freeze_path: Path, dataset_jsonl: Path, pricing_path: Path, evidence_root: Path) -> dict[str, Any]:
    root, evidence_root = root.resolve(), evidence_root.resolve()
    _require_goal4_path(evidence_root)
    frozen = load_formal_freeze(freeze_path, root=root)
    instances = load_formal_instances(frozen=frozen, dataset_jsonl=dataset_jsonl)
    pricing = load_pricing(pricing_path)
    if pricing_snapshot_hash(pricing) != frozen["pricing_snapshot_hash"]:
        raise ValueError("Goal 4 pricing snapshot does not match freeze")
    parent = update_parent_ledger(evidence_root)
    for batch in ("A", "B"):
        paths = _batch_paths(evidence_root, batch)
        if not all(path.exists() for path in paths.values()):
            raise ValueError(f"Goal 4 {batch} accounting is incomplete")
        _gate(root=root, evidence_root=evidence_root, pricing=pricing, batch=batch)
    return {
        "valid": True, "paid_execution_enabled": False,
        "instance_ids": [str(item["instance_id"]) for item in instances],
        "matrix_sha256": frozen["matrix_sha256"], "parent": parent,
    }


def execute_batch(
    *, root: Path, freeze_path: Path, dataset_jsonl: Path, pricing_path: Path,
    evidence_root: Path, batch: Literal["A", "B"], run_id: str, confirmed: bool,
) -> RunRecorder:
    """Execute one pre-registered batch strictly serially, stopping on hard errors."""
    root, evidence_root = root.resolve(), evidence_root.resolve()
    _require_goal4_path(evidence_root)
    if not confirmed:
        raise ValueError("Goal 4 paid execution requires --confirm-paid-run")
    preflight = require_native_preflight(root=root)
    frozen = load_formal_freeze(freeze_path, root=root)
    instances = load_formal_instances(frozen=frozen, dataset_jsonl=dataset_jsonl)
    selected = [
        (instance, task) for instance, task in zip(instances, frozen["tasks"], strict=True)
        if task["batch"] == batch
    ]
    if len(selected) != sum(BATCH_TARGETS[batch].values()):
        raise ValueError(f"Goal 4 Batch {batch} has an invalid task count")
    pricing = load_pricing(pricing_path)
    if pricing_snapshot_hash(pricing) != frozen["pricing_snapshot_hash"]:
        raise ValueError("Goal 4 pricing snapshot does not match freeze")
    pilot = _pilot_config(frozen)
    if not os.environ.get(pilot.api_key_env):
        raise ValueError("Goal 4 paid execution requires BAILIAN_API_KEY")
    gate = _gate(root=root, evidence_root=evidence_root, pricing=pricing, batch=batch)
    recorder = RunRecorder(
        evidence_root, _manifest(root=root, frozen=frozen, run_id=run_id, batch=batch),
        run_id=run_id, repo_root=root, secrets=_runtime_secrets(pilot),
    )
    profile = ExperimentProfile.model_validate(frozen["experiment_profile"])
    resolved_count = 0
    with tempfile.TemporaryDirectory(prefix="codepacex-goal4-home-") as home_text:
        home = Path(home_text)
        _write_child_config(pilot, home)
        profile_path = home / "profile.yaml"
        profile_path.write_text(yaml.safe_dump(profile.canonical_payload(), sort_keys=True), encoding="utf-8")
        environment = _child_environment(pilot, home_text, root=root)
        for instance, task in selected:
            instance_id = str(instance["instance_id"])
            trial_id = _trial_id(run_id, batch, instance_id)
            started = time.monotonic()
            accounting: dict[str, Any] = {"trial_id": trial_id, "request_count": 0, "actual_cny": "0"}
            recorder.event("trial_started", {
                "task_id": instance_id, "repetition_id": "1", "attempt_id": 1,
                "trial_id": trial_id, "batch": batch, "budget_mode": "per_provider_request",
                "matrix_task_sha256": task["execution_payload_sha256"],
            })
            with tempfile.TemporaryDirectory(prefix=f"codepacex-goal4-{instance_id}-") as temp_text:
                workspace = Path(temp_text) / "repo"
                try:
                    _goal3_materialize_instance(instance, workspace)
                    child_environment = dict(environment)
                    child_environment.update(provider_request_budget_environment(
                        gate, trial_id=trial_id,
                        maximum_input_tokens_per_request=MAXIMUM_INPUT_TOKENS_PER_REQUEST,
                        maximum_output_tokens_per_request=MAXIMUM_OUTPUT_TOKENS_PER_REQUEST,
                        maximum_reasoning_tokens_per_request=MAXIMUM_REASONING_TOKENS_PER_REQUEST,
                    ))
                    process = subprocess.run(
                        [sys.executable, "-m", "codepacex", "-p", _goal3_inference_prompt(instance),
                         "--output-format", "stream-json", "--experiment-profile", str(profile_path),
                         "--max-iterations", str(MAXIMUM_REQUESTS_PER_INSTANCE)],
                        cwd=workspace, env=child_environment, text=True, capture_output=True,
                        timeout=1800, check=False,
                    )
                    recorder.write_task_artifact(instance_id, "stdout", process.stdout or "")
                    recorder.write_task_artifact(instance_id, "stderr", process.stderr or "")
                except subprocess.TimeoutExpired as exc:
                    recorder.write_task_artifact(instance_id, "stdout", exc.stdout or "")
                    recorder.write_task_artifact(instance_id, "stderr", exc.stderr or "")
                    accounting = gate.trial_accounting(trial_id)
                    _terminal(recorder, instance_id=instance_id, trial_id=trial_id, status="agent_error", started=started,
                              accounting=accounting, reason="timeout", official_evaluator_completed=False)
                    recorder.finalize({"status": "infrastructure_error", "execution_mode": "live", "batch": batch})
                    update_parent_ledger(evidence_root)
                    return recorder
                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                    accounting = gate.trial_accounting(trial_id)
                    _terminal(recorder, instance_id=instance_id, trial_id=trial_id, status="infrastructure_error", started=started,
                              accounting=accounting, error=str(exc), official_evaluator_completed=False)
                    recorder.finalize({"status": "infrastructure_error", "execution_mode": "live", "batch": batch})
                    update_parent_ledger(evidence_root)
                    return recorder
                accounting = gate.trial_accounting(trial_id)
                requests, _input_tokens, _output_tokens = trace_usage(process.stdout or "")
                violation = accounting.get("provider_usage_contract_violation")
                if violation is not None or accounting["budget_blocked"] or accounting["active_reservation"] is not None:
                    reason = "provider_usage_contract_violation" if violation else (
                        "budget_blocked" if accounting["budget_blocked"] else "active_reservation"
                    )
                    _terminal(recorder, instance_id=instance_id, trial_id=trial_id, status="infrastructure_error", started=started,
                              accounting=accounting, reason=reason, provider_usage_contract_violation=violation,
                              budget_block_reasons=accounting.get("budget_block_reasons", []), official_evaluator_completed=False)
                    recorder.finalize({"status": "infrastructure_error", "execution_mode": "live", "batch": batch})
                    update_parent_ledger(evidence_root)
                    return recorder
                if (
                    requests == 0
                    or accounting["request_count"] != requests
                    or requests > MAXIMUM_REQUESTS_PER_INSTANCE
                ):
                    _terminal(recorder, instance_id=instance_id, trial_id=trial_id, status="infrastructure_error", started=started,
                              accounting=accounting,
                              reason=("request_ceiling_contract_violation" if requests > MAXIMUM_REQUESTS_PER_INSTANCE
                                      else "missing_or_mismatched_trace_usage"),
                              official_evaluator_completed=False)
                    recorder.finalize({"status": "infrastructure_error", "execution_mode": "live", "batch": batch})
                    update_parent_ledger(evidence_root)
                    return recorder
                with tempfile.NamedTemporaryFile("w", suffix=".ndjson", encoding="utf-8") as trace:
                    trace.write(process.stdout or "")
                    trace.flush()
                    _ingest_trace(recorder, Path(trace.name), instance_id, "1", 1)
                patch = _goal3_extract_patch(workspace)
                prediction_name = f"{instance_id}.prediction.json"
                recorder.write_json(prediction_name, [{
                    "instance_id": instance_id, "model_name_or_path": pilot.model_id, "model_patch": patch,
                }])
                patch_hash = hashlib.sha256(patch.encode()).hexdigest()
                if process.returncode != 0:
                    _terminal(recorder, instance_id=instance_id, trial_id=trial_id, status="agent_error", started=started,
                              accounting=accounting, process_returncode=process.returncode,
                              patch_sha256=patch_hash, official_evaluator_completed=False)
                    recorder.finalize({"status": "infrastructure_error", "execution_mode": "live", "batch": batch})
                    update_parent_ledger(evidence_root)
                    return recorder
                try:
                    evaluator_run_id = f"{run_id}-{instance_id}"
                    evaluator = run_official_evaluator(
                        dataset_name=ENVIRONMENT["dataset"], split=ENVIRONMENT["split"],
                        predictions_path=recorder.path / prediction_name, instance_ids=[instance_id],
                        max_workers=1, run_id=evaluator_run_id, namespace=ENVIRONMENT["evaluator_namespace"],
                        cwd=recorder.path, evaluator_architecture="native",
                    )
                    recorder.write_task_artifact(instance_id, "evaluator", (evaluator.stdout or "") + "\n" + (evaluator.stderr or ""))
                    if evaluator.returncode != 0:
                        raise ValueError(f"official evaluator failed with exit status {evaluator.returncode}")
                    report_path = official_evaluator_report_path(
                        cwd=recorder.path, run_id=evaluator_run_id, model_id=pilot.model_id, instance_id=instance_id,
                    )
                    resolved = collect_goal3_official_outcome(report_path, instance_id)
                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                    _terminal(recorder, instance_id=instance_id, trial_id=trial_id, status="infrastructure_error", started=started,
                              accounting=accounting, error=str(exc), patch_sha256=patch_hash,
                              prediction_file=prediction_name, evaluator_failure_only=True,
                              official_evaluator_completed=False)
                    recorder.finalize({"status": "infrastructure_error", "execution_mode": "live", "batch": batch})
                    update_parent_ledger(evidence_root)
                    return recorder
                _terminal(recorder, instance_id=instance_id, trial_id=trial_id,
                          status="resolved" if resolved else "unresolved", started=started, accounting=accounting,
                          process_returncode=process.returncode, empty_patch=not bool(patch.strip()),
                          patch_sha256=patch_hash, prediction_file=prediction_name,
                          official_evaluator_completed=True, official_outcome=resolved,
                          evaluator_report_sha256=_sha256(report_path), numerator=int(resolved), denominator=1)
                resolved_count += int(resolved)
    update_parent_ledger(evidence_root)
    recorder.finalize({
        "status": "success", "execution_mode": "live", "batch": batch,
        "registered_count": len(selected), "completed_count": len(selected), "scorable_count": len(selected),
        "resolved_count": resolved_count, "evaluator_commit": preflight["installed_evaluator_commit"],
        "native_linux_x86_64": preflight["native_linux_x86_64"],
    })
    return recorder


def run_control(
    *, root: Path, source_dataset_jsonl: Path, instance_id: str,
    control: Literal["empty", "gold"], runs_dir: Path, run_id: str,
) -> RunRecorder:
    """Run one Goal 4 evaluator-only control without importing a Provider client."""
    root, runs_dir = root.resolve(), runs_dir.resolve()
    _require_goal4_path(runs_dir)
    preflight = require_native_preflight(root=root)
    instances = [item for item in load_jsonl(source_dataset_jsonl) if item.get("instance_id") == instance_id]
    if len(instances) != 1:
        raise ValueError("Goal 4 control instance is not unique in frozen source data")
    instance = instances[0]
    gold_patch = instance.get("patch")
    if control == "gold" and (not isinstance(gold_patch, str) or not gold_patch.strip()):
        raise ValueError("Goal 4 gold control requires the official gold patch")
    patch = "" if control == "empty" else str(gold_patch)
    patch_sha256 = hashlib.sha256(patch.encode()).hexdigest()
    expected = control == "gold"
    manifest = RunManifest(
        experiment_kind="goal4-swe-bench-live-control", provider="none", model_id=f"goal4-control-{control}",
        protocol="none", run_id=run_id, git_commit=current_git_commit(root), prompt_version="none",
        swe_evaluator_architecture="native", task_ids=[instance_id], retry_budget=0, fallback_enabled=False,
    )
    recorder = RunRecorder(runs_dir, manifest, run_id=run_id, repo_root=root)
    recorder.write_json("predictions.json", [{
        "instance_id": instance_id, "model_name_or_path": f"goal4-control-{control}", "model_patch": patch,
    }])
    recorder.event("control_started", {
        "control": control, "instance_id": instance_id, "expected_resolved": expected,
        "patch_sha256": patch_sha256,
        "model_called": False, "network_called": False, "provider_network_called": False,
        "evaluator_commit": preflight["installed_evaluator_commit"],
    })
    try:
        result = run_official_evaluator(
            dataset_name=ENVIRONMENT["dataset"], split=ENVIRONMENT["split"],
            predictions_path=recorder.path / "predictions.json", instance_ids=[instance_id], max_workers=1,
            run_id=run_id, namespace=ENVIRONMENT["evaluator_namespace"], cwd=recorder.path,
            evaluator_architecture="native",
        )
        recorder.write_task_artifact(instance_id, "evaluator", (result.stdout or "") + "\n" + (result.stderr or ""))
        if result.returncode:
            raise ValueError(f"official evaluator failed with exit status {result.returncode}")
        if control == "empty":
            # The official evaluator deliberately skips per-instance reports for
            # empty patches, but emits its own summary with empty_patch_ids.
            # Treat that documented zero-patch outcome as completed and unresolved.
            output = (result.stdout or "") + "\n" + (result.stderr or "")
            if "No instances to run." not in output or "Instances with empty patches: 1" not in output:
                raise ValueError("official evaluator did not confirm empty-patch handling")
            resolved = collect_goal3_control_outcomes(recorder.path, {instance_id})[instance_id]
            if resolved:
                raise ValueError("official evaluator accepted an empty-patch control")
        else:
            # Paid Trials still require one exact report path. Gold controls may
            # use tolerant report discovery because they are evaluator-only.
            resolved = collect_official_outcomes(recorder.path, {instance_id})[instance_id]
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        recorder.event("control_completed", {"control": control, "instance_id": instance_id, "error": str(exc), "evaluator_completed": False})
        recorder.finalize({"status": "infrastructure_error", "execution_mode": "control", "scorable": False})
        return recorder
    finally:
        # The evaluator's transient checkout contains upstream source, test logs,
        # and (for gold controls) the Gold patch. It is not control evidence.
        shutil.rmtree(recorder.path / "logs", ignore_errors=True)
        (recorder.path / "predictions.json").unlink(missing_ok=True)
    recorder.event("control_completed", {
        "control": control, "instance_id": instance_id, "expected_resolved": expected, "resolved": resolved,
        "evaluator_completed": True, "model_called": False, "network_called": False,
        "provider_network_called": False,
        "empty_patch_rejected_by_evaluator": control == "empty",
    })
    recorder.finalize({
        "status": "success" if resolved == expected else "task_failure", "execution_mode": "control",
        "scorable": False, "official_evaluator_completed": True, "resolved": resolved,
        "expected_resolved": expected,
        "empty_patch_rejected_by_evaluator": control == "empty",
    })
    return recorder


def evaluator_recovery(
    *, root: Path, evidence_root: Path, run_id: str, instance_id: str,
) -> dict[str, Any]:
    """Re-evaluate an immutable persisted patch after evaluator-only failure."""
    evidence_root = evidence_root.resolve()
    _require_goal4_path(evidence_root)
    run = evidence_root / run_id
    events_path = run / "events.jsonl"
    if not events_path.exists():
        raise ValueError("Goal 4 recovery run does not exist")
    terminals = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    terminal = next((event for event in terminals if event.get("type") == "trial_completed" and event.get("task_id") == instance_id), None)
    if terminal is None or terminal.get("status") != "infrastructure_error" or not terminal.get("evaluator_failure_only"):
        raise ValueError("Goal 4 recovery requires an evaluator-only infrastructure failure")
    prediction_file = terminal.get("prediction_file")
    if not isinstance(prediction_file, str) or not re.fullmatch(r"[^/]+\.json", prediction_file):
        raise ValueError("Goal 4 recovery has no immutable prediction")
    prediction_path = run / prediction_file
    if not prediction_path.exists():
        raise ValueError("Goal 4 recovery prediction is missing")
    expected_patch = terminal.get("patch_sha256")
    prediction = json.loads(prediction_path.read_text(encoding="utf-8"))
    if not isinstance(prediction, list) or len(prediction) != 1 or prediction[0].get("instance_id") != instance_id:
        raise ValueError("Goal 4 recovery prediction is invalid")
    if hashlib.sha256(str(prediction[0].get("model_patch", "")).encode()).hexdigest() != expected_patch:
        raise ValueError("Goal 4 recovery patch identity changed")
    recovery_id = f"{run_id}-recovery-{instance_id}"
    result = run_official_evaluator(
        dataset_name=ENVIRONMENT["dataset"], split=ENVIRONMENT["split"], predictions_path=prediction_path,
        instance_ids=[instance_id], max_workers=1, run_id=recovery_id,
        namespace=ENVIRONMENT["evaluator_namespace"], cwd=run, evaluator_architecture="native",
    )
    recovery = {
        "schema_version": 1, "recovery_id": recovery_id, "run_id": run_id, "instance_id": instance_id,
        "provider_called": False, "prediction_sha256": _sha256(prediction_path), "patch_sha256": expected_patch,
        "evaluator_returncode": result.returncode,
    }
    if result.returncode == 0:
        report = official_evaluator_report_path(cwd=run, run_id=recovery_id, model_id=prediction[0]["model_name_or_path"], instance_id=instance_id)
        recovery["resolved"] = collect_goal3_official_outcome(report, instance_id)
        recovery["report_sha256"] = _sha256(report)
    target = run / f"{instance_id}.evaluator-recovery.json"
    if target.exists():
        raise ValueError("Goal 4 evaluator recovery already exists")
    target.write_text(json.dumps(recovery, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return recovery


def _trial_events(evidence_root: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for path in sorted(evidence_root.glob("*/events.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "trial_completed":
                event["_run_path"] = str(path.parent.relative_to(evidence_root))
                events.append(event)
    return events


def evidence_summary(*, root: Path, freeze_path: Path, evidence_root: Path) -> dict[str, Any]:
    frozen = load_formal_freeze(freeze_path, root=root.resolve())
    events = _trial_events(evidence_root)
    expected = {task["instance_id"]: task for task in frozen["tasks"]}
    by_id: dict[str, list[dict[str, Any]]] = {instance_id: [] for instance_id in expected}
    for event in events:
        task_id = event.get("task_id")
        if task_id in by_id:
            by_id[task_id].append(event)
    invalid = [key for key, values in by_id.items() if len(values) != 1]
    terminals = [values[0] for values in by_id.values() if len(values) == 1]
    statuses = {"resolved": 0, "unresolved": 0, "infrastructure_error": 0, "provider_error": 0, "agent_error": 0}
    for event in terminals:
        status = event.get("status")
        statuses[status] = statuses.get(status, 0) + 1
    parent = update_parent_ledger(evidence_root)
    request_charges = parent["request_charge_count"]
    settlements = parent["settlement_count"]
    charges = [
        charge for batch in ("A", "B")
        for charge in _load_child_ledger(_batch_paths(evidence_root, batch)["ledger"]).request_charges
    ]
    input_tokens = sum(charge.input_tokens for charge in charges)
    completion_tokens = sum(charge.output_tokens for charge in charges)
    reasoning_tokens = sum(charge.reasoning_tokens or 0 for charge in charges)
    complete = (
        not invalid and len(terminals) == 20 and
        statuses["infrastructure_error"] == statuses["provider_error"] == statuses["agent_error"] == 0 and
        statuses["resolved"] + statuses["unresolved"] == 20 and
        request_charges == settlements and parent["active_reservation"] is None and parent["budget_block_count"] == 0
    )
    costs = [Decimal(str(event.get("actual_cny", "0"))) for event in terminals]
    durations = [float(event.get("duration_seconds", 0)) for event in terminals]
    by_bucket: dict[str, dict[str, Any]] = {}
    by_repo: dict[str, dict[str, Any]] = {}
    for event in terminals:
        task = expected[event["task_id"]]
        for group, key in ((by_bucket, task["size_bucket"]), (by_repo, task["repo"])):
            current = group.setdefault(key, {"registered": 0, "resolved": 0, "unresolved": 0, "cost_cny": Decimal("0"), "requests": 0})
            current["registered"] += 1
            current[event["status"]] = current.get(event["status"], 0) + 1
            current["cost_cny"] += Decimal(str(event.get("actual_cny", "0")))
            current["requests"] += int(event.get("provider_request_count", 0))
    def _decimal_average(total: Decimal, denominator: int) -> str | None:
        return str(_money(total / denominator)) if denominator else None
    total_cost = Decimal(parent["spent_cny"])
    return {
        "schema_version": 1, "matrix_sha256": frozen["matrix_sha256"], "registered": 20,
        "attempted": len(terminals), "completed": len(terminals),
        "scorable": statuses["resolved"] + statuses["unresolved"], **statuses,
        "resolved_rate": Decimal(statuses["resolved"]) / Decimal(20),
        "scorable_rate": Decimal(statuses["resolved"] + statuses["unresolved"]) / Decimal(20),
        "total_requests": request_charges, "input_tokens": input_tokens,
        "completion_tokens": completion_tokens, "reasoning_tokens": reasoning_tokens,
        "total_cost_cny": parent["spent_cny"],
        "average_requests_per_attempted": _decimal_average(Decimal(request_charges), len(terminals)),
        "average_cost_per_attempted": _decimal_average(total_cost, len(terminals)),
        "average_cost_per_scorable": _decimal_average(total_cost, statuses["resolved"] + statuses["unresolved"]),
        "average_cost_per_resolved": _decimal_average(total_cost, statuses["resolved"]),
        "median_cost_per_task": str(_money(Decimal(str(statistics.median(costs))))) if costs else None,
        "median_elapsed_seconds": str(statistics.median(durations)) if durations else None,
        "active_reservation": parent["active_reservation"], "settlements": settlements,
        "invalid_terminal_task_ids": invalid, "accepted": complete,
        "per_task": terminals, "per_task_costs": [str(cost) for cost in costs],
        "by_bucket": by_bucket, "by_repository": by_repo,
    }


def finalize_evidence(*, root: Path, freeze_path: Path, evidence_root: Path) -> dict[str, Any]:
    """Compile immutable evidence facts and Claims without changing historical Trials."""
    evidence_root = evidence_root.resolve()
    summary = evidence_summary(root=root, freeze_path=freeze_path, evidence_root=evidence_root)
    reports = evidence_root / "reports"
    reports.mkdir(exist_ok=True)
    report_path, claims_path = reports / "GOAL4_FINAL_REPORT.md", reports / "claims.goal4.json"
    if report_path.exists() or claims_path.exists():
        raise ValueError("Goal 4 final report or Claims already exists")
    lines = [
        "# Goal 4 Final Evidence Report", "",
        f"Status: {_final_status(summary)}", "",
        "## Scope", "",
        "This is a pre-registered 20-task Python-only SWE-bench-Live Lite subset.",
        "Goal 3 Pilot tasks are excluded. This is not a full Lite result, leaderboard result, or pass@k.", "",
        "## Results", "",
        f"- Registered / attempted / completed / scorable: {summary['registered']} / {summary['attempted']} / {summary['completed']} / {summary['scorable']}",
        f"- Resolved / unresolved: {summary['resolved']} / {summary['unresolved']}",
        f"- Infrastructure / Provider / Agent errors: {summary['infrastructure_error']} / {summary['provider_error']} / {summary['agent_error']}",
        f"- Total requests: {summary['total_requests']}",
        f"- Input / completion / reasoning Tokens: {summary['input_tokens']} / {summary['completion_tokens']} / {summary['reasoning_tokens']}",
        f"- Total cost: CNY {summary['total_cost_cny']}",
        f"- Active reservation: {summary['active_reservation']}",
        f"- Average requests per attempted: {summary['average_requests_per_attempted']}",
        f"- Average cost per attempted / scorable / resolved: {summary['average_cost_per_attempted']} / {summary['average_cost_per_scorable']} / {summary['average_cost_per_resolved']}",
        f"- Median cost / elapsed seconds: {summary['median_cost_per_task']} / {summary['median_elapsed_seconds']}",
        "", "## Evidence Index", "",
        "| Task | Status | Requests | Cost |", "| --- | --- | ---: | ---: |",
    ]
    for event in summary["per_task"]:
        lines.append(f"| {event['task_id']} | {event['status']} | {event.get('provider_request_count', 0)} | {event.get('actual_cny', 0)} |")
    lines.extend(["", "## Stratified Results", "", "| Bucket | Registered | Resolved | Unresolved | Requests | Cost |", "| --- | ---: | ---: | ---: | ---: | ---: |"])
    for bucket, values in summary["by_bucket"].items():
        lines.append(f"| {bucket} | {values['registered']} | {values['resolved']} | {values['unresolved']} | {values['requests']} | {values['cost_cny']} |")
    lines.extend(["", "## Claim Boundary", "", "Only the frozen matrix, model identity, official evaluator, actual costs, and observed resolved count are claimed. No model comparison, statistical significance, generalization, or production success rate is implied.", ""])
    report_path.write_text("\n".join(lines), encoding="utf-8")
    claims = {
        "schema_version": 1, "status": "verified" if summary["accepted"] else "insufficient-data",
        "goal": "Goal 4 — Formal SWE-bench-Live Evaluation and Evidence Publication",
        "matrix_sha256": summary["matrix_sha256"], "resolved": summary["resolved"],
        "denominator": 20, "total_cost_cny": summary["total_cost_cny"],
        "evidence_report_sha256": _sha256(report_path),
        "limitations": [
            "Pre-registered 20-task Python-only Lite subset, not full SWE-bench-Live Lite.",
            "Not pass@k, a model comparison, or a claim of statistical significance.",
        ],
    }
    claims_path.write_text(json.dumps(claims, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    validation = validate_claims(root=root, freeze_path=freeze_path, evidence_root=evidence_root, claims_path=claims_path)
    return {"status": _final_status(summary), "summary": summary, "report": str(report_path), "claims": str(claims_path), "claims_validation": validation}


def _final_status(summary: dict[str, Any]) -> str:
    if summary["accepted"]:
        return "GOAL4_ACCEPTED"
    if summary["infrastructure_error"] or summary["provider_error"] or summary["agent_error"] or summary["active_reservation"] is not None or summary["invalid_terminal_task_ids"]:
        return "GOAL4_BLOCKED"
    return "GOAL4_PARTIAL"


def validate_claims(*, root: Path, freeze_path: Path, evidence_root: Path, claims_path: Path) -> dict[str, Any]:
    summary = evidence_summary(root=root, freeze_path=freeze_path, evidence_root=evidence_root)
    claims = json.loads(claims_path.read_text(encoding="utf-8"))
    expected_status = "verified" if summary["accepted"] else "insufficient-data"
    if claims.get("status") != expected_status or claims.get("matrix_sha256") != summary["matrix_sha256"]:
        raise ValueError("Goal 4 Claims do not match frozen evidence")
    if claims.get("resolved") != summary["resolved"] or claims.get("denominator") != 20:
        raise ValueError("Goal 4 Claims result does not match evidence")
    return {"valid": True, "verified": summary["accepted"], "status": expected_status}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Goal 4 formal SWE-bench-Live runner")
    parser.add_argument("command", choices=[
        "validate", "preflight", "freeze-formal", "prepare-paid-artifacts", "zero-provider",
        "paid-preflight", "execute-batch", "control-empty", "control-gold", "evaluator-recovery", "finalize", "validate-claims",
    ])
    parser.add_argument("--freeze", type=Path, default=DEFAULT_RUNS_DIR / "formal-freeze.json")
    parser.add_argument("--dataset-jsonl", type=Path)
    parser.add_argument("--source-dataset-jsonl", type=Path)
    parser.add_argument("--pricing-snapshot", type=Path, default=DEFAULT_RUNS_DIR / "pricing-snapshot.json")
    parser.add_argument("--evidence-root", type=Path, default=DEFAULT_RUNS_DIR)
    parser.add_argument("--freeze-output-dir", type=Path)
    parser.add_argument("--dataset-revision")
    parser.add_argument("--batch", choices=["A", "B"])
    parser.add_argument("--run-id")
    parser.add_argument("--instance-id")
    parser.add_argument("--control-runs-dir", type=Path, default=DEFAULT_CONTROL_RUNS_DIR)
    parser.add_argument("--claims", type=Path)
    parser.add_argument("--confirm-paid-run", action="store_true")
    args = parser.parse_args(argv)
    root = Path.cwd()
    try:
        if args.command == "validate":
            payload = {
                "valid": True, "formal_instances": 20, "batch_targets": BATCH_TARGETS,
                "request_ceiling": MAXIMUM_REQUESTS_PER_INSTANCE,
                "goal3_pilot_exclusions": sorted(GOAL3_PILOT_IDS),
            }
        elif args.command == "preflight":
            payload = require_native_preflight(root=root)
        elif args.command == "freeze-formal":
            if args.dataset_jsonl is None or args.freeze_output_dir is None or args.dataset_revision is None:
                raise ValueError("freeze-formal requires dataset, output directory, and dataset revision")
            payload = freeze_formal_bundle(
                root=root, dataset_jsonl=args.dataset_jsonl, pricing_snapshot=args.pricing_snapshot,
                output_dir=args.freeze_output_dir, dataset_revision=args.dataset_revision,
            )
        elif args.command == "prepare-paid-artifacts":
            payload = prepare_paid_artifacts(
                root=root, freeze_path=args.freeze, pricing_path=args.pricing_snapshot,
                evidence_root=args.evidence_root,
            )
        elif args.command == "zero-provider":
            payload = zero_provider_check(
                root=root, freeze_path=args.freeze, pricing_path=args.pricing_snapshot,
                evidence_root=args.evidence_root,
            )
        elif args.command == "paid-preflight":
            if args.dataset_jsonl is None:
                raise ValueError("paid-preflight requires --dataset-jsonl")
            payload = paid_preflight(
                root=root, freeze_path=args.freeze, dataset_jsonl=args.dataset_jsonl,
                pricing_path=args.pricing_snapshot, evidence_root=args.evidence_root,
            )
        elif args.command == "execute-batch":
            if args.dataset_jsonl is None or args.batch is None or args.run_id is None:
                raise ValueError("execute-batch requires dataset, batch, and run ID")
            payload = {"run_path": str(execute_batch(
                root=root, freeze_path=args.freeze, dataset_jsonl=args.dataset_jsonl,
                pricing_path=args.pricing_snapshot, evidence_root=args.evidence_root,
                batch=args.batch, run_id=args.run_id, confirmed=args.confirm_paid_run,
            ).path)}
        elif args.command in {"control-empty", "control-gold"}:
            if args.source_dataset_jsonl is None or args.instance_id is None or args.run_id is None:
                raise ValueError("control requires source dataset, instance ID, and run ID")
            payload = {"run_path": str(run_control(
                root=root, source_dataset_jsonl=args.source_dataset_jsonl, instance_id=args.instance_id,
                control="empty" if args.command == "control-empty" else "gold",
                runs_dir=args.control_runs_dir, run_id=args.run_id,
            ).path)}
        elif args.command == "evaluator-recovery":
            if args.run_id is None or args.instance_id is None:
                raise ValueError("evaluator-recovery requires run ID and instance ID")
            payload = evaluator_recovery(root=root, evidence_root=args.evidence_root, run_id=args.run_id, instance_id=args.instance_id)
        elif args.command == "validate-claims":
            if args.claims is None:
                raise ValueError("validate-claims requires --claims")
            payload = validate_claims(root=root, freeze_path=args.freeze, evidence_root=args.evidence_root, claims_path=args.claims)
        else:
            payload = finalize_evidence(root=root, freeze_path=args.freeze, evidence_root=args.evidence_root)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"Goal 4 SWE error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

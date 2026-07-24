"""Evaluation V2 zero-provider preparation for the Goal 4 twenty-task replay.

The module freezes only Agent-visible task data and deterministic contracts.
Provider execution is impossible unless a caller selects ``paid-run``, supplies
an exact Freeze identity and hard cap, and explicitly confirms paid execution.
The normal CI and readiness paths use deterministic replay and cancelled
reservations only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import venv
from collections import Counter
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from codepacex.prompts import build_static_system_instruction
from codepacex.tools import create_default_registry
from evals.benchmark import canonical_hash, current_git_commit
from evals.costing import load_pricing, pricing_snapshot_hash
from evals.evaluation_v2 import control_canary
from evals.goal3_swe import execution_instance_payload_hash
from evals.paid_gate import (
    BudgetAuthorization,
    BudgetLedger,
    PaidRunGate,
    authorization_hash,
    worst_case_reservation,
)


SCHEMA_VERSION = 1
GOAL4_RUN_ID = "29830820618"
GOAL4_ARTIFACT_ID = "8496125148"
GOAL4_ARTIFACT_DIGEST = "8b9309a9ee03b068bf96e69afd50ecc2c18e4a70046dc1ae99359310dc70c6c8"
GOAL4_FREEZE_COMMIT = "75a1eca465913e1c5be81e58eba89bc4d1cd8853"
GOAL4_MATRIX_SHA256 = "9ff16e850b92a6eb0bd1338cb85253a605fdfb0e0aa77180488382eca353972a"
GOAL4_DATASET_REVISION = "a637bd46829f3132e12938c8a0ca93173a977b8e"
GOAL4_DATASET_SOURCE_SHA256 = "67f8459e41df193a9956f1ef450aaaa4788fabfb02977030ce72039ec8ec89d7"
GOAL4_AGENT_DATASET_FILE_SHA256 = "50cc5c3b9927a5f079c2ce117e3cc15bbd4f8ca63ee5d2b2afaac991314c02fa"
OFFICIAL_EVALUATOR_COMMIT = control_canary.OFFICIAL_EVALUATOR_COMMIT
PRICING_PATH = control_canary.PRICING_PATH
PAYLOAD_ROOT = Path("evals/evaluation_v2/full_replay_payloads")
PAYLOAD_DATASET = PAYLOAD_ROOT / "tasks.jsonl"
PAYLOAD_MANIFEST = PAYLOAD_ROOT / "manifest.json"
SELECTION_MANIFEST = PAYLOAD_ROOT / "diagnostic-selection.json"
ENVIRONMENT_CONTRACT = PAYLOAD_ROOT / "environment-normalization.json"
COMMITTED_FREEZE = PAYLOAD_ROOT / "full-20-freeze.json"
WORKFLOW_PATH = Path(".github/workflows/evaluation-v2-full-20-replay.yml")
MAX_REQUESTS_PER_TASK = 40
AGENT_MAX_ITERATIONS = 50
MAX_INPUT_TOKENS = 128_000
MAX_OUTPUT_TOKENS = 8_192
MAX_REASONING_TOKENS = 6_144
PHASE_A_HARD_CAP_CNY = Decimal("80.000000")
PHASE_B_INCREMENTAL_HARD_CAP_CNY = Decimal("170.000000")
TOTAL_HARD_CAP_CNY = Decimal("250.000000")
SAFE_FIELDS = frozenset({
    "instance_id", "repo", "base_commit", "problem_statement", "platform",
    "version", "environment_setup_commit",
})
FORBIDDEN_KEY = re.compile(
    r"(?:^|_)(?:patch|gold|test_patch|solution|reference|answer|hint)(?:_|$)", re.I,
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")

GOAL4_ORDER = (
    "aws-cloudformation__cfn-lint-3749",
    "aws-cloudformation__cfn-lint-3764",
    "beetbox__beets-5457",
    "beetbox__beets-5495",
    "deepset-ai__haystack-8489",
    "beancount__beancount-931",
    "beeware__briefcase-2075",
    "beeware__briefcase-2085",
    "bridgecrewio__checkov-6893",
    "bridgecrewio__checkov-6895",
    "conan-io__conan-17092",
    "conan-io__conan-17102",
    "cyclotruc__gitingest-115",
    "cyclotruc__gitingest-134",
    "deepset-ai__haystack-8525",
    "delgan__loguru-1297",
    "delgan__loguru-1306",
    "dynaconf__dynaconf-1225",
    "dynaconf__dynaconf-1249",
    "instructlab__instructlab-2540",
)
PHASE_A_IDS = (
    "aws-cloudformation__cfn-lint-3749",
    "aws-cloudformation__cfn-lint-3764",
    "bridgecrewio__checkov-6893",
    "conan-io__conan-17092",
    "dynaconf__dynaconf-1225",
    "instructlab__instructlab-2540",
)
PHASE_B_IDS = tuple(item for item in GOAL4_ORDER if item not in PHASE_A_IDS)
CAPABILITY_TERMINALS = frozenset({
    "resolved", "unresolved", "agent_no_candidate", "validation_failed",
    "request_ceiling_reached",
})
INFRASTRUCTURE_TERMINALS = frozenset({
    "protocol_blocked", "provider_transport_error", "evaluator_unavailable",
    "evaluator_execution_error", "evaluator_report_selection_error",
    "budget_blocked", "runner_error", "task_environment_blocked",
    "preflight_wiring_blocked",
})
EXPECTED_SELECTION = {
    "bridgecrewio__checkov-6893": ("incomplete_patch", "one_file"),
    "conan-io__conan-17092": ("incomplete_patch", "two_to_four_files"),
    "aws-cloudformation__cfn-lint-3764": ("regression_introduced", "one_file"),
    "aws-cloudformation__cfn-lint-3749": ("root_cause_localization_failure", "one_file"),
    "instructlab__instructlab-2540": ("cross_file_propagation_missed", "five_plus_files"),
    "dynaconf__dynaconf-1225": ("request_ceiling_exhausted", "five_plus_files"),
}
RUNTIME_SOURCES = (
    "codepacex/agent.py",
    "codepacex/client.py",
    "codepacex/permissions/checker.py",
    "codepacex/tools/edit_file.py",
    "codepacex/tools/run_test.py",
    "evals/evaluation_v2/control_canary.py",
    "evals/evaluation_v2/full_replay.py",
    "evals/paid_gate.py",
    str(WORKFLOW_PATH),
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _scan_safe(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if FORBIDDEN_KEY.search(str(key)):
                raise ValueError(f"full replay payload contains forbidden key: {key}")
            _scan_safe(item)
    elif isinstance(value, list):
        for item in value:
            _scan_safe(item)
    elif isinstance(value, str) and "diff --git" in value and "--- a/" in value:
        raise ValueError("full replay payload contains a patch-like value")


def load_tasks(root: Path) -> list[dict[str, Any]]:
    """Load the exact seven-field Goal 4 projection without gold data."""
    path = root / PAYLOAD_DATASET
    if _sha256(path) != GOAL4_AGENT_DATASET_FILE_SHA256:
        raise ValueError("Agent-visible Goal 4 dataset file identity changed")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if len(rows) != 20 or tuple(item.get("instance_id") for item in rows) != GOAL4_ORDER:
        raise ValueError("full replay task order differs from Goal 4")
    for row in rows:
        if not isinstance(row, dict) or set(row) != SAFE_FIELDS:
            raise ValueError("full replay task must contain exactly seven Agent-visible fields")
        _scan_safe(row)
        if not COMMIT.fullmatch(str(row.get("base_commit", ""))):
            raise ValueError("full replay task has an invalid base commit")
        if not all(isinstance(row.get(key), str) and row[key] for key in (
            "instance_id", "repo", "base_commit", "problem_statement",
        )):
            raise ValueError("full replay task has incomplete Agent-visible identity")
    return rows


def _goal4_task_hashes() -> dict[str, tuple[str, str]]:
    """Return published full/Agent-visible hashes; no gold payload is loaded."""
    values = {
        "aws-cloudformation__cfn-lint-3749": ("98bb3abe612d495ca80d8f0e4f74ffa761da804473c5431330b07e62a3c00297", "92aaec543cf5614e2cb7fc3b72cd4247b6616bbefbe072a700e27f9bbceb02b6"),
        "aws-cloudformation__cfn-lint-3764": ("cd7dce9b371a590fcff92a1f6976c8fe59531d0ea6027342bfd95da689be71eb", "067a1f40003c67f43834a480e5c39ad18b31a7574c7d5777d42f13d8947ce8ee"),
        "beetbox__beets-5457": ("104fb9d5c577495246629717779b6ce93667333831451629501c6e73f21e9088", "a67568f494ec4a076eda9daf4ca500b12c522f350cee9c6279600fca3dd67577"),
        "beetbox__beets-5495": ("c0d50fe2cd032d73d620a45c8a7c9ec6b7372dc9c216283be43e5b07e862c733", "bafee5f439276bdffb9c5aa97bbaf647379e5273cf406429c1c198b850fab1c4"),
        "deepset-ai__haystack-8489": ("58ed7127d8370c5f9fa16fca5d1c20e7f988b1ad32b704be9592c9cfcec8d558", "0fdec283e3a10a06da22408245cca484ed5ebc0a3c239f306e6a0c9af6c89a07"),
        "beancount__beancount-931": ("fd9ba314942afae9ec089a774ae508fc890e22f355652f1c9aa36701bef90d34", "fee10262072e86664c1f57134bf98cb25ef36388de068d545049d843afdecc42"),
        "beeware__briefcase-2075": ("8fdfce0f5f0a0346ed012cd14dd9de1fa877fe162528547b5ceed6840639d540", "3865f1227ad844401cb818c8488f817845d048e061818ea3ea1d51ef684dc874"),
        "beeware__briefcase-2085": ("0651fc270ea3629ca6e08fa1ae1aa03706ebb104cb38d43d974dba9c9c967647", "8931c5c07ea407240ff45ea2a1e970a3220a44bfe31a66accbd87f30830b8873"),
        "bridgecrewio__checkov-6893": ("bb48a6a12d3d1ec152236de669867ed05914fdefc98041697df951e533cb9b4d", "88f88cc1be37140555071d724b41a49e05201227519be93674f32c79323fe0ff"),
        "bridgecrewio__checkov-6895": ("0fa316af119900321a9f455cd958d4961eb78c242313fec281e122f0536f7c7c", "9296bdd9f21634c70acdc30b7952b042640ed3be7807b0f441f1ca5146465895"),
        "conan-io__conan-17092": ("0d0bb8320f83a446d4af1226b4b4c55997cda0f627f21834a112d8b66a6c4045", "98e22890f90e94a68851a2ea14d0e1501184f5c178df37c9c7f62a18ae1b07e4"),
        "conan-io__conan-17102": ("caafab2434419d2ebef946ed0de086f7f81e2d233630ff2a761a0381e211676a", "8e99da50e8d8333d494989ea5101e090c2431f9f70adf90facae720f044b6912"),
        "cyclotruc__gitingest-115": ("f3656494ee2d1ec9f8984a75408526b8b0934d15067152aa5d4f9058892cd999", "93e16b3f52010d2d6dff3eacce932dd86608b6f0ad8cbda1bd412f31d5344913"),
        "cyclotruc__gitingest-134": ("0bcb66e6b4dadc0f7aa94d78f654aa5b41efc64b741d25579d84d80a78b97520", "6ea8b216232e5ba50680f14e271359c1078681ce3562da80d6c70616424023a6"),
        "deepset-ai__haystack-8525": ("e2a3ec1826aef3a12be2497dc69041545f62c5e36cdd54990b0ff2c045408a4b", "b5d2bd15efbdca83f87b09906ad7b932692a75732622fcde1b594804889ba6fc"),
        "delgan__loguru-1297": ("d65d1aad5c30d54b6900520a871c885ae5fa6806ace0a6b38e9b15a608533622", "a8d198412588896f59ec58cce317856bd52e869d0422d385a5237fa9f86bf1b6"),
        "delgan__loguru-1306": ("af07961c95ed62f64e3543940f02dbe7966c93aa3ba288e82973c04664fa809f", "329520f1fbb85de6fc2587963cf06c53bc5a851933c714589c5d526b989407c9"),
        "dynaconf__dynaconf-1225": ("3fd1eb38a1905908f8fb6f4e1758c1af366614698a07e775fda2f119b6545bad", "fdf9f09a4edf9c763f01e5b974a233e977dd787ef5a04f40bd192fa335bb71e8"),
        "dynaconf__dynaconf-1249": ("547d3dc0337614d09b04144fb1b972776b655a8f1756c17c00354f45f9ac6bb2", "9bd8c12078bd24cdfcc62064668635a1a149e14dc3cd339ae56be09abee1fa14"),
        "instructlab__instructlab-2540": ("6c7c13e2f2326168984f4fb4eec5ed706cff177a0391abde21ad2cb67965a121", "384da0d1f916bb18bc926dd78ebc6093624a2d7f39b6b7b95e93343c16157d90"),
    }
    if set(values) != set(GOAL4_ORDER):
        raise AssertionError("Goal 4 published task hashes are incomplete")
    return values


def build_payload_manifest(root: Path) -> dict[str, Any]:
    rows = load_tasks(root)
    hashes = _goal4_task_hashes()
    payloads = []
    for row in rows:
        instance_id = row["instance_id"]
        full_hash, visible_hash = hashes[instance_id]
        actual = execution_instance_payload_hash(row)
        if actual != visible_hash:
            raise ValueError(f"Agent-visible payload differs from Goal 4: {instance_id}")
        payloads.append({
            "instance_id": instance_id,
            "repo": row["repo"],
            "base_commit": row["base_commit"],
            "problem_statement_sha256": hashlib.sha256(row["problem_statement"].encode()).hexdigest(),
            "agent_visible_payload_sha256": actual,
            "goal4_execution_payload_sha256": visible_hash,
            "goal4_full_payload_sha256_hash_only": full_hash,
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "task_order": list(GOAL4_ORDER),
        "source": {
            "goal4_run_id": GOAL4_RUN_ID,
            "goal4_artifact_id": GOAL4_ARTIFACT_ID,
            "goal4_artifact_digest": GOAL4_ARTIFACT_DIGEST,
            "goal4_freeze_commit": GOAL4_FREEZE_COMMIT,
            "goal4_matrix_sha256": GOAL4_MATRIX_SHA256,
            "dataset_revision": GOAL4_DATASET_REVISION,
            "dataset_source_sha256": GOAL4_DATASET_SOURCE_SHA256,
            "agent_visible_dataset_file_sha256": GOAL4_AGENT_DATASET_FILE_SHA256,
            "extraction_contract": "exact-seven-agent-visible-fields-no-gold-v1",
        },
        "payloads": payloads,
    }


def _baseline_rows(root: Path) -> list[dict[str, Any]]:
    baseline = _read_json(root / "evals/stage_c/stage_c_baseline.json")["rows"]
    if tuple(item["instance_id"] for item in baseline) != GOAL4_ORDER:
        raise ValueError("Goal 4 published baseline order changed")
    taxonomy: dict[str, dict[str, str]] = {}
    with (root / "evals/goal4_failure_taxonomy.csv").open(newline="", encoding="utf-8") as handle:
        taxonomy = {item["instance_id"]: item for item in csv.DictReader(handle)}
    rows = []
    for item in baseline:
        row = dict(item)
        detail = taxonomy.get(row["instance_id"])
        row["failure_category"] = detail["primary_attribution"] if detail else None
        rows.append(row)
    return rows


def build_selection_manifest(root: Path) -> dict[str, Any]:
    by_id = {item["instance_id"]: item for item in _baseline_rows(root)}
    reasons = {
        "aws-cloudformation__cfn-lint-3749": "tests root-cause localization after a reproduced one-file failure",
        "aws-cloudformation__cfn-lint-3764": "tests regression-aware validation for a one-file regression",
        "bridgecrewio__checkov-6893": "tests incomplete graph-policy behavior and regression coverage",
        "conan-io__conan-17092": "tests multi-surface completeness at the historical 40-request boundary",
        "dynaconf__dynaconf-1225": "tests high-scope planning when the historical request ceiling interrupted work",
        "instructlab__instructlab-2540": "tests configuration propagation across a five-plus-file task",
    }
    selected = []
    for instance_id in PHASE_A_IDS:
        row = by_id[instance_id]
        category, bucket = EXPECTED_SELECTION[instance_id]
        if row["goal4_status"] != "unresolved" or row["failure_category"] != category or row["size_bucket"] != bucket:
            raise ValueError(f"diagnostic selection is unsupported by Goal 4 taxonomy: {instance_id}")
        selected.append({
            "instance_id": instance_id,
            "goal4_outcome": row["goal4_status"],
            "goal4_failure_category": category,
            "size_bucket": bucket,
            "goal4_requests": row["goal4_requests"],
            "goal4_cost_cny": row["goal4_selected_terminal_cost_cny"],
            "selection_reason": reasons[instance_id],
            "diagnostic_target": category,
        })
    categories = Counter(item["goal4_failure_category"] for item in selected)
    if categories != Counter({
        "incomplete_patch": 2, "regression_introduced": 1,
        "root_cause_localization_failure": 1, "cross_file_propagation_missed": 1,
        "request_ceiling_exhausted": 1,
    }):
        raise ValueError("diagnostic selection category mix changed")
    return {
        "schema_version": SCHEMA_VERSION,
        "claim_boundary": "Phase A is diagnostic within the full twenty-task replay, not a standalone success-rate sample",
        "phase_a_order": list(PHASE_A_IDS),
        "phase_b_order": list(PHASE_B_IDS),
        "tasks": selected,
    }


def budget_contract(root: Path) -> dict[str, Any]:
    pricing = load_pricing(root / PRICING_PATH)
    one = worst_case_reservation(
        pricing, maximum_requests=1,
        maximum_input_tokens_per_request=MAX_INPUT_TOKENS,
        maximum_output_tokens_per_request=MAX_OUTPUT_TOKENS,
    )
    per_task = one * MAX_REQUESTS_PER_TASK
    baseline = _baseline_rows(root)
    phase_a_history = sum(
        (Decimal(item["goal4_selected_terminal_cost_cny"]) for item in baseline if item["instance_id"] in PHASE_A_IDS),
        Decimal("0"),
    )
    full_history = sum((Decimal(item["goal4_selected_terminal_cost_cny"]) for item in baseline), Decimal("0"))
    return {
        "currency": "CNY",
        "pricing_snapshot_path": str(PRICING_PATH),
        "pricing_snapshot_sha256": pricing_snapshot_hash(pricing),
        "provider_request_ceiling_per_task": MAX_REQUESTS_PER_TASK,
        "agent_max_iterations": AGENT_MAX_ITERATIONS,
        "maximum_input_tokens_per_request": MAX_INPUT_TOKENS,
        "maximum_output_tokens_per_request": MAX_OUTPUT_TOKENS,
        "maximum_reasoning_tokens_per_request": MAX_REASONING_TOKENS,
        "rolling_reservation": "one_provider_request",
        "one_request_theoretical_exposure_cny": str(one),
        "one_task_theoretical_exposure_cny": str(per_task),
        "phase_a_theoretical_exposure_cny": str(per_task * len(PHASE_A_IDS)),
        "phase_b_theoretical_exposure_cny": str(per_task * len(PHASE_B_IDS)),
        "full_20_theoretical_exposure_cny": str(per_task * len(GOAL4_ORDER)),
        "goal4_phase_a_selected_cost_cny": str(phase_a_history),
        "goal4_full_selected_cost_cny": str(full_history),
        "phase_a_recommended_hard_cap_cny": str(PHASE_A_HARD_CAP_CNY),
        "phase_b_incremental_recommended_hard_cap_cny": str(PHASE_B_INCREMENTAL_HARD_CAP_CNY),
        "full_20_recommended_hard_cap_cny": str(TOTAL_HARD_CAP_CNY),
        "recommendation_basis": "retains the reviewed Stage C 80/250 envelope over Goal 4 selected costs; rolling reservations fail closed and completion is not guaranteed",
        "completion_guarantee": False,
    }


def runtime_contract(root: Path) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "runtime_source_sha256": {name: _sha256(root / name) for name in RUNTIME_SOURCES},
        "system_instruction_sha256": hashlib.sha256(build_static_system_instruction().encode()).hexdigest(),
        "tool_schemas_sha256": canonical_hash(create_default_registry().get_all_schemas("openai-compat")),
        "agent_entrypoint": "Agent.run_to_completion",
        "pilot_config_max_iterations": AGENT_MAX_ITERATIONS,
        "provider_request_budget_bridge_ceiling": MAX_REQUESTS_PER_TASK,
        "candidate_export_contract": "git-diff-binary-sha256-bound-v1",
        "phase_transition_contract": "capability-terminals-continue-infrastructure-accounting-terminals-stop-v1",
    }


def paired_comparison_contract(root: Path) -> dict[str, Any]:
    manifest = build_payload_manifest(root)
    return {
        "comparison": "Goal 4 system-level Harness vs Evaluation V2 system-level Harness",
        "paired_instances": list(GOAL4_ORDER),
        "goal4_matrix_sha256": GOAL4_MATRIX_SHA256,
        "payload_manifest_sha256": canonical_hash(manifest),
        "fixed": {
            "repo_base_commit_problem_statement": True,
            "dataset_revision": GOAL4_DATASET_REVISION,
            "official_evaluator_commit": OFFICIAL_EVALUATOR_COMMIT,
            "provider": "bailian-qwen37-max",
            "model": "qwen3.7-max-2026-06-08",
            "pricing_sha256": budget_contract(root)["pricing_snapshot_sha256"],
            "provider_request_ceiling_per_task": MAX_REQUESTS_PER_TASK,
            "agent_max_iterations": AGENT_MAX_ITERATIONS,
            "strict_serial": True,
            "fallback": False,
            "retry": 0,
            "fresh_workspace_authorization_allocation_ledger": True,
        },
        "treatment_differences": [
            "Evaluation V2 corrected ProviderRequestBudget bridge",
            "Evaluation V2 Base Lane and deterministic static system instruction",
            "Evaluation V2 runner, Candidate binding, accounting, summary, and report-selection changes",
        ],
        "claim_limit": "system-level paired comparison; no single-variable causal isolation claim",
    }


def freeze_payload(root: Path) -> dict[str, Any]:
    manifest = build_payload_manifest(root)
    selection = build_selection_manifest(root)
    runtime = runtime_contract(root)
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_name": "evaluation-v2-goal4-full-20-replay",
        "status": "frozen_pending_single_full_20_authorization",
        "goal4_source": {
            "run_id": GOAL4_RUN_ID,
            "artifact_id": GOAL4_ARTIFACT_ID,
            "artifact_digest": GOAL4_ARTIFACT_DIGEST,
            "freeze_commit": GOAL4_FREEZE_COMMIT,
            "matrix_sha256": GOAL4_MATRIX_SHA256,
            "accepted_result": "4 resolved / 16 unresolved; 20/20 scorable",
        },
        "logical_goal4_order": list(GOAL4_ORDER),
        "phase_a_diagnostic_ids": list(PHASE_A_IDS),
        "phase_b_remaining_ids": list(PHASE_B_IDS),
        "payload_manifest_sha256": canonical_hash(manifest),
        "selection_manifest_sha256": canonical_hash(selection),
        "environment_contract_sha256": _sha256(root / ENVIRONMENT_CONTRACT),
        "runtime_contract": runtime,
        "runtime_contract_sha256": canonical_hash(runtime),
        "paired_comparison_contract": paired_comparison_contract(root),
        "budget_contract": budget_contract(root),
        "provider_contract": {
            "provider": "bailian-qwen37-max",
            "protocol": "openai-compat",
            "base_url": control_canary.PROVIDER_BASE_URL,
            "provider_secret_name": "BAILIAN_API_KEY",
            "model_id": "qwen3.7-max-2026-06-08",
            "fallback_enabled": False,
            "retry": 0,
            "strict_serial": True,
        },
        "official_evaluator": {
            "repository": "https://github.com/microsoft/SWE-bench-Live",
            "commit": OFFICIAL_EVALUATOR_COMMIT,
            "dataset": "SWE-bench-Live/SWE-bench-Live",
            "split": "lite",
            "namespace": "starryzhang",
            "report_selection": "detailed_then_summary_fail_closed-v1",
        },
        "gold_patch_forbidden": True,
        "paid_execution_default": False,
        "fresh_workspace_authorization_allocation_ledger_required": True,
    }


def write_contract_files(root: Path) -> dict[str, str]:
    """Regenerate deterministic manifests and Freeze during development."""
    _write_json(root / PAYLOAD_MANIFEST, build_payload_manifest(root))
    _write_json(root / SELECTION_MANIFEST, build_selection_manifest(root))
    frozen = freeze_payload(root)
    _write_json(root / COMMITTED_FREEZE, frozen)
    return {
        "payload_manifest_sha256": _sha256(root / PAYLOAD_MANIFEST),
        "selection_manifest_sha256": _sha256(root / SELECTION_MANIFEST),
        "freeze_sha256": _sha256(root / COMMITTED_FREEZE),
        "runtime_contract_sha256": frozen["runtime_contract_sha256"],
        "system_instruction_sha256": frozen["runtime_contract"]["system_instruction_sha256"],
    }


def validate_contract(root: Path) -> dict[str, Any]:
    expected_manifest = build_payload_manifest(root)
    expected_selection = build_selection_manifest(root)
    if _read_json(root / PAYLOAD_MANIFEST) != expected_manifest:
        raise ValueError("committed full replay payload manifest differs from source")
    if _read_json(root / SELECTION_MANIFEST) != expected_selection:
        raise ValueError("committed diagnostic selection differs from Goal 4 taxonomy")
    expected_freeze = freeze_payload(root)
    if _read_json(root / COMMITTED_FREEZE) != expected_freeze:
        raise ValueError("committed full replay Freeze differs from canonical contract")
    return {
        "valid": True,
        "freeze_sha256": _sha256(root / COMMITTED_FREEZE),
        "runtime_contract_sha256": expected_freeze["runtime_contract_sha256"],
        "system_instruction_sha256": expected_freeze["runtime_contract"]["system_instruction_sha256"],
        "pricing_sha256": expected_freeze["budget_contract"]["pricing_snapshot_sha256"],
        "payload_manifest_sha256": _sha256(root / PAYLOAD_MANIFEST),
    }


def _run(command: Sequence[str], *, cwd: Path, timeout: int = 1800) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False)


def _environment_blocker(result: subprocess.CompletedProcess[str]) -> str | None:
    output = f"{result.stdout}\n{result.stderr}".lower()
    patterns = {
        "modulenotfounderror": "missing_python_dependency",
        "no module named": "missing_python_dependency",
        "fixture '" : "missing_pytest_fixture",
        "error collecting": "pytest_collection_error",
        "not found:": "pytest_selector_not_found",
        "command not found": "command_not_found",
        "no such file or directory": "workspace_path_error",
        "unrecognized arguments": "controlled_argv_rejection",
    }
    return next((reason for marker, reason in patterns.items() if marker in output), None)


def canonical_task_environment_plan(task: Mapping[str, Any], contract: Mapping[str, Any]) -> dict[str, Any]:
    """The single task bootstrap contract used by readiness and paid execution."""
    plan = {
        "instance_id": str(task["instance_id"]),
        "editable_target": str(contract["editable_target"]),
        "dependencies": [str(item) for item in contract["dependencies"]],
        "test_target": str(contract["test_target"]),
    }
    if not plan["editable_target"] or not plan["test_target"]:
        raise ValueError("canonical task environment plan is incomplete")
    return plan


def _bootstrap(workspace: Path, contract: Mapping[str, Any]) -> tuple[Path, list[dict[str, Any]]]:
    return control_canary._bootstrap(
        workspace,
        [str(item) for item in contract["dependencies"]],
        editable_target=str(contract["editable_target"]),
    )


def preflight_task(
    task: Mapping[str, Any], contract: Mapping[str, Any], *, work_root: Path,
    materializer: Callable[[dict[str, Any], Path], None] = control_canary._goal3_materialize_instance,
) -> dict[str, Any]:
    instance_id = str(task["instance_id"])
    task_root = work_root / instance_id
    workspace = task_root / "workspace"
    evidence_root = task_root / "evidence"
    evidence_root.mkdir(parents=True)
    result: dict[str, Any] = {
        "instance_id": instance_id,
        "repo": task["repo"],
        "base_commit": task["base_commit"],
        "task_workspace_materialized": False,
        "dependencies_installed": False,
        "test_collection_completed": False,
        "meaningful_test_executed": False,
        "official_evaluator_available": False,
        "candidate_export_path_ready": False,
        "artifact_path_ready": True,
        "environment_status": "runner_wiring_blocked",
        "environment_blocker": None,
        "test_command": None,
        "evidence_path": str(evidence_root.relative_to(work_root.parent)),
    }
    try:
        materializer(dict(task), workspace)
        result["task_workspace_materialized"] = True
        python, bootstrap = _bootstrap(workspace, contract)
        _write_json(evidence_root / "dependency-bootstrap.json", bootstrap)
        if not bootstrap or any(item["exit_code"] for item in bootstrap):
            raise RuntimeError("dependency_bootstrap_failed")
        result["dependencies_installed"] = True
        selector = str(contract["test_target"])
        collect_command = [str(python), "-m", "pytest", "--collect-only", "-q", selector]
        collected = _run(collect_command, cwd=workspace)
        (evidence_root / "collection.stdout.txt").write_text(collected.stdout, encoding="utf-8")
        (evidence_root / "collection.stderr.txt").write_text(collected.stderr, encoding="utf-8")
        blocker = _environment_blocker(collected)
        if collected.returncode not in (0, 1) or blocker:
            raise RuntimeError(blocker or "pytest_collection_failed")
        if "collected 0 items" in collected.stdout.lower() or "no tests collected" in collected.stdout.lower():
            raise RuntimeError("pytest_empty_collection")
        result["test_collection_completed"] = True
        test_command = [str(python), "-m", "pytest", "-q", selector, "--maxfail=1"]
        executed = _run(test_command, cwd=workspace)
        (evidence_root / "pre-edit.stdout.txt").write_text(executed.stdout, encoding="utf-8")
        (evidence_root / "pre-edit.stderr.txt").write_text(executed.stderr, encoding="utf-8")
        blocker = _environment_blocker(executed)
        if blocker:
            raise RuntimeError(blocker)
        evaluator_check = _run([sys.executable, "-c", "import swebench"], cwd=workspace, timeout=60)
        result.update({
            "meaningful_test_executed": True,
            "test_command": test_command,
            "test_exit_code": executed.returncode,
            "candidate_export_path_ready": workspace.joinpath(".git").is_dir(),
            "official_evaluator_available": (
                shutil.which("docker") is not None and evaluator_check.returncode == 0
            ),
        })
        if not result["candidate_export_path_ready"]:
            raise RuntimeError("candidate_export_path_unavailable")
        if not result["official_evaluator_available"]:
            raise RuntimeError("official_evaluator_environment_unavailable")
        result["environment_status"] = "ready"
    except Exception as exc:
        reason = str(exc)
        result["environment_blocker"] = reason
        result["environment_status"] = (
            "evaluator_environment_blocked" if "evaluator" in reason
            else "task_environment_blocked" if result["task_workspace_materialized"]
            else "runner_wiring_blocked"
        )
    _write_json(evidence_root / "preflight-result.json", result)
    return result


def run_preflight(root: Path, artifact_root: Path) -> dict[str, Any]:
    validate_contract(root)
    if artifact_root.exists():
        raise ValueError("refusing to overwrite full replay preflight evidence")
    artifact_root.mkdir(parents=True)
    tasks = load_tasks(root)
    environment = _read_json(root / ENVIRONMENT_CONTRACT)
    contracts = {item["instance_id"]: item for item in environment["tasks"]}
    results = [
        preflight_task(task, contracts[task["instance_id"]], work_root=artifact_root / "tasks")
        for task in tasks
    ]
    summary = {
        "schema_version": SCHEMA_VERSION,
        "paid_execution": False,
        "provider_requests": 0,
        "usage": 0,
        "charge_cny": "0",
        "provider_secret_read": False,
        "agent_started": False,
        "passed": len(results) == 20 and all(item["environment_status"] == "ready" for item in results),
        "ready_count": sum(item["environment_status"] == "ready" for item in results),
        "tasks": results,
    }
    _write_json(artifact_root / "preflight-summary.json", summary)
    return summary


def _fresh_gate(root: Path, artifact_root: Path, *, acknowledgement: str) -> PaidRunGate:
    if not acknowledgement:
        raise ValueError("full replay requires a non-empty authorization acknowledgement")
    pricing = load_pricing(root / PRICING_PATH)
    authorization = BudgetAuthorization(
        authorized_total_cny=TOTAL_HARD_CAP_CNY,
        stage_limits_cny={"A": PHASE_A_HARD_CAP_CNY, "B": TOTAL_HARD_CAP_CNY, "C": TOTAL_HARD_CAP_CNY},
        pricing_snapshot_hash=pricing_snapshot_hash(pricing),
        experiment_commit=current_git_commit(root),
        authorized_at="single-full-20-replay-authorization",
        authorized_by="user",
    )
    authorization_path = artifact_root / "authorization.json"
    ledger_path = artifact_root / "ledger.json"
    allocation_path = artifact_root / "stage-c-gate-compatibility-allocation.json"
    _write_json(authorization_path, authorization.model_dump(mode="json"))
    _write_json(artifact_root / "authorization-acknowledgement.json", {"acknowledgement": acknowledgement})
    ledger = BudgetLedger(authorization_hash=authorization_hash(authorization), updated_at="full-20-start")
    _write_json(ledger_path, ledger.model_dump(mode="json"))
    allocation = control_canary._fresh_rehearsal_allocation(authorization, ledger, pricing_snapshot_hash(pricing))
    _write_json(allocation_path, allocation.model_dump(mode="json"))
    return PaidRunGate(
        root=root, authorization_path=authorization_path, ledger_path=ledger_path,
        allocation_path=allocation_path, pricing_path=root / PRICING_PATH,
        pricing=pricing, stage="C",
    )


def phase_b_admission(results: Sequence[control_canary.PaidTaskResult], ledger: BudgetLedger, root: Path) -> dict[str, Any]:
    blockers = []
    if tuple(item.instance_id for item in results) != PHASE_A_IDS:
        blockers.append("phase_a_identity_or_duplicate_execution")
    if any(item.terminal_status in INFRASTRUCTURE_TERMINALS for item in results):
        blockers.append("phase_a_infrastructure_failure")
    if ledger.active_reservation is not None:
        blockers.append("active_reservation_exists")
    next_request = Decimal(budget_contract(root)["one_request_theoretical_exposure_cny"])
    if ledger.spent_cny + next_request > TOTAL_HARD_CAP_CNY:
        blockers.append("insufficient_next_reservation")
    return {
        "admitted": not blockers,
        "blockers": blockers,
        "capability_outcomes_do_not_block": sorted(CAPABILITY_TERMINALS),
        "spent_cny": str(ledger.spent_cny),
        "next_request_reservation_cny": str(next_request),
        "active_reservation": None if ledger.active_reservation is None else ledger.active_reservation.model_dump(mode="json"),
    }


def _write_task_shadow(
    gate: PaidRunGate, artifact_root: Path, run_id: str, instance_id: str, status: str,
) -> control_canary.PaidTaskResult:
    task_root = artifact_root / "tasks" / instance_id
    task_root.mkdir(parents=True)
    reservation = gate.reserve(
        f"swe/v2-full-shadow/{run_id}/{instance_id}", maximum_requests=1,
        maximum_input_tokens_per_request=MAX_INPUT_TOKENS,
        maximum_output_tokens_per_request=MAX_OUTPUT_TOKENS,
    )
    gate.cancel(reservation, reason="provider_confirmed_not_submitted")
    candidate = status != "agent_no_candidate"
    candidate_sha = hashlib.sha256(f"shadow:{instance_id}".encode()).hexdigest() if candidate else None
    evaluator = status in {"resolved", "unresolved", "validation_failed", "request_ceiling_reached"}
    result = control_canary.PaidTaskResult(
        instance_id=instance_id,
        agent_status="completed",
        candidate_status="exported_nonempty" if candidate else "not_exported",
        validation_status="failed" if status == "validation_failed" else "executed",
        evaluator_status="completed" if evaluator else "not_run",
        runner_status="completed",
        provider_status="deterministic_replay_no_transport",
        terminal_status=status,
        provider_requests=0,
        usage={"input_tokens": 0, "output_tokens": 0},
        charge_cny="0",
        candidate_sha256=candidate_sha,
        workspace_diff_sha256=candidate_sha,
        candidate_diff_identity=candidate,
        evaluator_report_sha256=hashlib.sha256(f"report:{instance_id}".encode()).hexdigest() if evaluator else None,
        resolved=True if status == "resolved" else False if status == "unresolved" else None,
        active_reservation=None,
    )
    _write_json(task_root / "task-result.json", asdict(result))
    return result


def compile_paired_report(root: Path, results: Sequence[control_canary.PaidTaskResult]) -> dict[str, Any]:
    baseline = {item["instance_id"]: item for item in _baseline_rows(root)}
    actual = {item.instance_id: item for item in results}
    rows = []
    for instance_id in GOAL4_ORDER:
        old = baseline[instance_id]
        new = actual.get(instance_id)
        status = new.terminal_status if new else "not_run"
        old_status = old["goal4_status"]
        flip = "unchanged" if status == old_status else f"{old_status}_to_{status}"
        rows.append({
            "instance_id": instance_id,
            "goal4_outcome": old_status,
            "v2_outcome": status,
            "resolved_flip": flip,
            "candidate": bool(new and new.candidate_status == "exported_nonempty"),
            "scorable": bool(new and new.evaluator_status == "completed"),
            "provider_requests": 0 if new is None else new.provider_requests,
            "cost_cny": "0" if new is None else new.charge_cny,
            "goal4_failure_category": old["failure_category"],
            "size_bucket": old["size_bucket"],
        })
    resolved = sum(item["v2_outcome"] == "resolved" for item in rows)
    return {
        "schema_version": SCHEMA_VERSION,
        "comparison_contract": paired_comparison_contract(root),
        "rows": rows,
        "summary": {
            "total": 20,
            "goal4_resolved": 4,
            "goal4_resolved_rate": "0.20",
            "v2_resolved": resolved,
            "v2_resolved_rate": str(Decimal(resolved) / Decimal("20")),
            "candidate_count": sum(item["candidate"] for item in rows),
            "scorable_count": sum(item["scorable"] for item in rows),
            "infrastructure_failure_count": sum(item["v2_outcome"] in INFRASTRUCTURE_TERMINALS for item in rows),
        },
    }


def run_shadow(root: Path, preflight_summary: Path, artifact_root: Path, run_id: str) -> dict[str, Any]:
    validate_contract(root)
    preflight = _read_json(preflight_summary)
    if not preflight.get("passed"):
        raise ValueError("full replay shadow requires 20/20 environment readiness")
    if artifact_root.exists() or not run_id or Path(run_id).name != run_id:
        raise ValueError("full replay shadow requires a fresh safe Artifact root and Run ID")
    artifact_root.mkdir(parents=True)
    frozen = _read_json(root / COMMITTED_FREEZE)
    pilot = control_canary._paid_pilot_config(frozen)
    if pilot.max_iterations != AGENT_MAX_ITERATIONS:
        raise RuntimeError("full replay shadow generated an invalid Agent iteration limit")
    with tempfile.TemporaryDirectory(prefix="codepacex-v2-full-shadow-home-") as home_text:
        control_canary._initialize_paid_agent_config(pilot=pilot, home=Path(home_text))
    gate = _fresh_gate(root, artifact_root, acknowledgement="zero-provider-full-20-shadow")
    statuses = ["request_ceiling_reached", "resolved", "agent_no_candidate", "validation_failed", "unresolved", "resolved"]
    phase_a = [
        _write_task_shadow(gate, artifact_root, run_id, instance_id, status)
        for instance_id, status in zip(PHASE_A_IDS, statuses, strict=True)
    ]
    ledger = BudgetLedger.model_validate_json(gate.ledger_path.read_text(encoding="utf-8"))
    admission = phase_b_admission(phase_a, ledger, root)
    _write_json(artifact_root / "phase-a-interim-summary.json", {
        "results": [asdict(item) for item in phase_a], "phase_b_admission": admission,
    })
    phase_b = []
    if admission["admitted"]:
        phase_b = [
            _write_task_shadow(gate, artifact_root, run_id, instance_id, "resolved" if index % 3 == 0 else "unresolved")
            for index, instance_id in enumerate(PHASE_B_IDS)
        ]
    ledger = BudgetLedger.model_validate_json(gate.ledger_path.read_text(encoding="utf-8"))
    results = phase_a + phase_b
    paired = compile_paired_report(root, results)
    _write_json(artifact_root / "paired-report.json", paired)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "paid_execution": False,
        "provider_transport": "deterministic_replay_no_transport",
        "provider_secret_read": False,
        "provider_requests": 0,
        "usage": 0,
        "charge_cny": "0",
        "simulated_reservations": len(ledger.settlements),
        "phase_a_completed": len(phase_a) == 6,
        "phase_b_admitted": admission["admitted"],
        "phase_b_completed": len(phase_b) == 14,
        "pilot_max_iterations": pilot.max_iterations,
        "provider_request_ceiling_per_task": MAX_REQUESTS_PER_TASK,
        "results": [asdict(item) for item in results],
        "ledger_closed": ledger.active_reservation is None,
        "active_reservation": None,
        "paired_report_sha256": _sha256(artifact_root / "paired-report.json"),
        "freeze_sha256": _sha256(root / COMMITTED_FREEZE),
        "runtime_contract_sha256": _read_json(root / COMMITTED_FREEZE)["runtime_contract_sha256"],
        "completed": len(results) == 20 and ledger.active_reservation is None,
    }
    _write_json(artifact_root / "full-20-shadow-summary.json", summary)
    _write_json(artifact_root / "zero-provider-ledger-summary.json", {
        "provider_requests": 0, "usage": 0, "charge_cny": "0",
        "provider_secret_read": False, "active_reservation": None,
        "request_charges": len(ledger.request_charges),
        "settlements": len(ledger.settlements),
        "spent_cny": str(ledger.spent_cny),
    })
    return summary


def _task_environment_contract(root: Path) -> dict[str, dict[str, Any]]:
    environment = _read_json(root / ENVIRONMENT_CONTRACT)
    contracts = environment.get("tasks")
    if not isinstance(contracts, list):
        raise ValueError("full replay environment contract has no task list")
    by_id = {str(item.get("instance_id")): item for item in contracts if isinstance(item, dict)}
    if tuple(by_id) != GOAL4_ORDER:
        raise ValueError("full replay environment contract order changed")
    return by_id


def _full_task_executor(
    root: Path, frozen: Mapping[str, Any], metadata: Mapping[str, Mapping[str, Any]],
    gate: PaidRunGate, artifact_root: Path, run_id: str, task: dict[str, Any],
) -> control_canary.PaidTaskResult:
    """Run the shared paid executor with this replay's safe payload identity."""
    payload_path = artifact_root / "safe-payloads" / f"{task['instance_id']}.json"
    _write_json(payload_path, task)
    plan = canonical_task_environment_plan(task, metadata[task["instance_id"]])
    return control_canary._live_task_executor(
        root=root, freeze_payload=dict(frozen), task=task,
        metadata={
            "preflight_dependencies": plan["dependencies"],
            "editable_target": plan["editable_target"],
            "test_target": plan["test_target"],
        }, gate=gate,
        artifact_root=artifact_root, run_id=run_id, payload_path=payload_path,
        trial_namespace="v2-full-20",
    )


def _phase_is_healthy_for_continuation(result: control_canary.PaidTaskResult, ledger: BudgetLedger) -> bool:
    """Capability outcomes continue; accounting and infrastructure failures do not."""
    return (
        result.terminal_status in CAPABILITY_TERMINALS
        and result.provider_status != "provider_transport_error"
        and ledger.active_reservation is None
    )


def run_paid_replay(
    root: Path, artifact_root: Path, *, expected_freeze_sha256: str,
    approved_total_hard_cap_cny: str, authorization_acknowledgement: str, run_id: str,
) -> dict[str, Any]:
    """The one future 6+14 paid path, guarded by its complete frozen contract."""
    identities = validate_contract(root)
    if expected_freeze_sha256 != identities["freeze_sha256"]:
        raise ValueError("expected full-20 Freeze SHA does not match the committed contract")
    if Decimal(approved_total_hard_cap_cny) != TOTAL_HARD_CAP_CNY:
        raise ValueError("approved hard cap does not match the frozen full-20 CNY 250 contract")
    if not run_id or Path(run_id).name != run_id or artifact_root.exists():
        raise ValueError("full replay paid execution requires a fresh safe Run ID and Artifact root")
    artifact_root.mkdir(parents=True)
    frozen = _read_json(root / COMMITTED_FREEZE)
    gate = _fresh_gate(root, artifact_root, acknowledgement=authorization_acknowledgement)
    tasks = {item["instance_id"]: item for item in load_tasks(root)}
    metadata = _task_environment_contract(root)
    results: list[control_canary.PaidTaskResult] = []
    for instance_id in PHASE_A_IDS:
        result = _full_task_executor(root, frozen, metadata, gate, artifact_root, run_id, tasks[instance_id])
        results.append(result)
        ledger = BudgetLedger.model_validate_json(gate.ledger_path.read_text(encoding="utf-8"))
        if not _phase_is_healthy_for_continuation(result, ledger):
            break
    ledger = BudgetLedger.model_validate_json(gate.ledger_path.read_text(encoding="utf-8"))
    admission = phase_b_admission(results, ledger, root) if len(results) == len(PHASE_A_IDS) else {
        "admitted": False, "blockers": ["phase_a_not_completed"],
        "active_reservation": None if ledger.active_reservation is None else ledger.active_reservation.model_dump(mode="json"),
    }
    _write_json(artifact_root / "phase-a-interim-summary.json", {
        "results": [asdict(item) for item in results], "phase_b_admission": admission,
    })
    if admission["admitted"]:
        for instance_id in PHASE_B_IDS:
            result = _full_task_executor(root, frozen, metadata, gate, artifact_root, run_id, tasks[instance_id])
            results.append(result)
            ledger = BudgetLedger.model_validate_json(gate.ledger_path.read_text(encoding="utf-8"))
            if not _phase_is_healthy_for_continuation(result, ledger):
                break
    ledger = BudgetLedger.model_validate_json(gate.ledger_path.read_text(encoding="utf-8"))
    paired = compile_paired_report(root, results)
    _write_json(artifact_root / "paired-report.json", paired)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "paid_execution": True,
        "freeze_sha256": identities["freeze_sha256"],
        "runtime_contract_sha256": identities["runtime_contract_sha256"],
        "results": [asdict(item) for item in results],
        "phase_a_completed": len(results) >= len(PHASE_A_IDS),
        "phase_b_admission": admission,
        "phase_b_completed": len(results) == 20,
        "provider_requests": len(ledger.request_charges),
        "usage": sum(item.input_tokens + item.output_tokens for item in ledger.request_charges),
        "charge_cny": str(ledger.spent_cny),
        "active_reservation": None if ledger.active_reservation is None else ledger.active_reservation.model_dump(mode="json"),
        "ledger_closed": ledger.active_reservation is None,
        "paired_report_sha256": _sha256(artifact_root / "paired-report.json"),
        "completed": len(results) == 20 and ledger.active_reservation is None,
    }
    _write_json(artifact_root / "paid-full-20-summary.json", summary)
    return summary


def release_check(root: Path, preflight_summary: Path, shadow_summary: Path) -> dict[str, Any]:
    identities = validate_contract(root)
    preflight = _read_json(preflight_summary)
    shadow = _read_json(shadow_summary)
    head = current_git_commit(root)
    remote_process = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "refs/remotes/origin/main^{commit}"],
        text=True, capture_output=True, check=False,
    )
    remote = remote_process.stdout.strip()
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"], text=True, capture_output=True, check=False,
    ).stdout
    blockers = []
    if remote_process.returncode or not COMMIT.fullmatch(remote):
        remote = None
        blockers.append("origin_main_ref_unavailable")
    elif head != remote:
        blockers.append("head_is_not_origin_main")
    if status:
        blockers.append("worktree_not_clean")
    if not preflight.get("passed") or preflight.get("ready_count") != 20:
        blockers.append("full_20_environment_preflight_not_passed")
    if not shadow.get("completed") or not shadow.get("phase_b_completed"):
        blockers.append("full_20_shadow_not_completed")
    if any((shadow.get("provider_requests"), shadow.get("usage"), Decimal(str(shadow.get("charge_cny", "0"))))):
        blockers.append("zero_provider_accounting_changed")
    if not shadow.get("ledger_closed") or shadow.get("active_reservation") is not None:
        blockers.append("shadow_ledger_not_closed")
    return {
        "status": "READY_FOR_SINGLE_FULL_20_PAID_REPLAY" if not blockers else blockers[0],
        "blockers": blockers,
        "head": head,
        "origin_main": remote,
        "head_is_origin_main": head == remote,
        "git_status": status,
        "preflight_ready_count": preflight.get("ready_count"),
        "shadow_phase_a_completed": shadow.get("phase_a_completed"),
        "shadow_phase_b_completed": shadow.get("phase_b_completed"),
        **identities,
        "workflow_inputs": {
            "paid_execution": "true",
            "expected_freeze_sha256": identities["freeze_sha256"],
            "approved_total_hard_cap_cny": str(TOTAL_HARD_CAP_CNY),
            "authorization_acknowledgement": "REPLACE_WITH_EXPLICIT_FULL_20_AUTHORIZATION",
            "run_id": "REPLACE_WITH_FRESH_RUN_ID",
        },
        "provider_requests": 0,
        "usage": 0,
        "charge_cny": "0",
        "provider_secret_read": False,
        "paid_execution_started": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluation V2 Goal 4 full-20 replay contracts")
    sub = parser.add_subparsers(dest="command", required=True)
    generate = sub.add_parser("generate"); generate.add_argument("--root", type=Path, required=True)
    validate = sub.add_parser("validate"); validate.add_argument("--root", type=Path, required=True)
    preflight = sub.add_parser("preflight"); preflight.add_argument("--root", type=Path, required=True); preflight.add_argument("--artifact-root", type=Path, required=True)
    shadow = sub.add_parser("shadow"); shadow.add_argument("--root", type=Path, required=True); shadow.add_argument("--preflight-summary", type=Path, required=True); shadow.add_argument("--artifact-root", type=Path, required=True); shadow.add_argument("--run-id", required=True)
    release = sub.add_parser("release-check"); release.add_argument("--root", type=Path, required=True); release.add_argument("--preflight-summary", type=Path, required=True); release.add_argument("--shadow-summary", type=Path, required=True); release.add_argument("--output", type=Path)
    paid = sub.add_parser("paid-run"); paid.add_argument("--root", type=Path, required=True); paid.add_argument("--artifact-root", type=Path, required=True); paid.add_argument("--expected-freeze-sha256", required=True); paid.add_argument("--approved-total-hard-cap-cny", required=True); paid.add_argument("--authorization-acknowledgement", required=True); paid.add_argument("--run-id", required=True); paid.add_argument("--confirm-paid-execution", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "generate":
        result = write_contract_files(args.root.resolve())
    elif args.command == "validate":
        result = validate_contract(args.root.resolve())
    elif args.command == "preflight":
        result = run_preflight(args.root.resolve(), args.artifact_root.resolve())
    elif args.command == "shadow":
        result = run_shadow(args.root.resolve(), args.preflight_summary.resolve(), args.artifact_root.resolve(), args.run_id)
    elif args.command == "release-check":
        result = release_check(args.root.resolve(), args.preflight_summary.resolve(), args.shadow_summary.resolve())
        if args.output is not None:
            _write_json(args.output.resolve(), result)
    else:
        if not args.confirm_paid_execution:
            raise ValueError("paid execution requires --confirm-paid-execution")
        result = run_paid_replay(
            args.root.resolve(), args.artifact_root.resolve(),
            expected_freeze_sha256=args.expected_freeze_sha256,
            approved_total_hard_cap_cny=args.approved_total_hard_cap_cny,
            authorization_acknowledgement=args.authorization_acknowledgement,
            run_id=args.run_id,
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

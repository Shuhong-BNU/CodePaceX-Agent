"""Zero-provider Freeze contract for the Stage D two-task protocol canary."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from codepacex.experiments import ExperimentProfile
from codepacex.tools.run_test import RunTestParams
from codepacex.tools.validation_checkpoint import ValidationCheckpointParams
from evals.benchmark import canonical_hash

STAGE_D_UNBLOCKER_MERGE_COMMIT = "400475531dc2f44ed5661e9153fff27dc3d1cc8d"
OFFICIAL_EVALUATOR_COMMIT = "ad79b850f15e33992e96f03f6e97f05ddf9aa0be"
CANARY_INSTANCE_IDS = (
    "beetbox__beets-5495",
    "beancount__beancount-931",
)
RUNTIME_SOURCE_PATHS = (
    "codepacex/agent.py",
    "codepacex/validation.py",
    "codepacex/tools/validation_checkpoint.py",
    "codepacex/tools/run_test.py",
    "codepacex/permissions/checker.py",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stage_d_profile() -> ExperimentProfile:
    """The canary uses the fixed Stage B treatment, never instance-based toggles."""
    return ExperimentProfile(
        tool_loading="deferred",
        compression_profile="recovery_v1",
        permission_strategy="session_allow",
        agent_mode="single",
        validation_mode="stage_b",
    )


def runtime_contract_payload(root: Path) -> dict[str, Any]:
    """Hash the executable Stage D protocol surface without loading a Provider."""
    profile = stage_d_profile()
    return {
        "schema_version": 1,
        "runtime_source_sha256": {
            relative: _sha256(root / relative) for relative in RUNTIME_SOURCE_PATHS
        },
        "tool_parameter_schema_sha256": {
            "RunTest": canonical_hash(RunTestParams.model_json_schema()),
            "ValidationCheckpoint": canonical_hash(
                ValidationCheckpointParams.model_json_schema(),
            ),
        },
        "experiment_profile": profile.canonical_payload(),
        "experiment_profile_hash": profile.profile_hash(),
        "loop_entrypoints": ["Agent._run", "Agent.run_to_completion"],
        "protocol_guarantees": [
            "typed_checkpoint_declarations",
            "recent_observed_result_binding",
            "bounded_pytest_argv",
            "permission_chain_after_reproduction",
            "completion_gate",
        ],
    }


def freeze_payload(root: Path) -> dict[str, Any]:
    runtime_contract = runtime_contract_payload(root)
    return {
        "schema_version": 1,
        "stage": "D",
        "status": "frozen_zero_provider_canary_not_authorized",
        "experiment_kind": "stage-d-live-tool-protocol-canary",
        "stage_d_unblocker_merge_commit": STAGE_D_UNBLOCKER_MERGE_COMMIT,
        "runtime_contract": runtime_contract,
        "runtime_contract_hash": canonical_hash(runtime_contract),
        "provider_contract": {
            "provider": "bailian-qwen37-max",
            "protocol": "openai-compat",
            "base_url": "https://llm-ipge9fy38w648m28.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            "model_id": "qwen3.7-max-2026-06-08",
            "official_evaluator_commit": OFFICIAL_EVALUATOR_COMMIT,
            "fallback_enabled": False,
            "automatic_retry": 0,
            "maximum_requests_per_instance": 40,
            "strict_serial": True,
            "one_formal_candidate_per_instance": True,
        },
        "canary_instance_ids": list(CANARY_INSTANCE_IDS),
        "admission": {
            "paid_execution_authorized": False,
            "authorization_identity": None,
            "workflow_dispatch_allowed": False,
            "required_before_execution": [
                "separate authorization identity",
                "fresh immutable checkout commitment",
                "per-request rolling reservation contract",
            ],
        },
        "claims_boundary": {
            "allowed_if_completed": [
                "two-task Stage D protocol-canary process evidence",
                "per-task scorable evaluator outcomes",
                "descriptive Goal 4 and Stage C comparison",
            ],
            "prohibited": [
                "Stage C Phase 2 claim",
                "Stage C historical-evidence modification",
                "six-task Phase 1 claim",
                "twenty-task claim",
                "holdout_or_leaderboard_claim",
                "general_model_capability_claim",
            ],
            "next_decision": "A six-task Stage D phase requires a new user authorization after canary reporting.",
        },
    }


def write_freeze(root: Path, output: Path) -> dict[str, Any]:
    payload = freeze_payload(root.resolve())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def validate_freeze(root: Path, freeze_path: Path) -> dict[str, Any]:
    actual = json.loads(freeze_path.read_text(encoding="utf-8"))
    expected = freeze_payload(root.resolve())
    if actual != expected:
        raise ValueError("Stage D Freeze differs from the current deterministic contract")
    if actual["canary_instance_ids"] != list(CANARY_INSTANCE_IDS):
        raise ValueError("Stage D Freeze does not contain the exact two-task canary")
    if actual["admission"]["paid_execution_authorized"] is not False:
        raise ValueError("Stage D Freeze must not authorize paid execution")
    return actual


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("write", "validate"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--freeze", type=Path, default=Path("evals/stage_d/stage_d_freeze.json"))
    args = parser.parse_args()
    if args.command == "write":
        print(json.dumps(write_freeze(args.root, args.freeze), sort_keys=True))
    else:
        print(json.dumps(validate_freeze(args.root, args.freeze), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Zero-provider Freeze contract for the isolated Stage D.1 one-task canary."""

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


STAGE_D_CANARY_RUN = "29933711107"
STAGE_D_CANARY_ARTIFACT_SHA256 = "1129c64d9aa1153c8b21fe85f9030627683257d4d4e6aa14064d486002fe28a3"
OFFICIAL_EVALUATOR_COMMIT = "ad79b850f15e33992e96f03f6e97f05ddf9aa0be"
CANARY_INSTANCE_IDS = ("beetbox__beets-5495",)
RUNTIME_SOURCE_PATHS = (
    "codepacex/agent.py",
    "codepacex/validation.py",
    "codepacex/tools/validation_checkpoint.py",
    "codepacex/tools/run_test.py",
    "codepacex/permissions/checker.py",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stage_d1_profile() -> ExperimentProfile:
    return ExperimentProfile(
        tool_loading="deferred",
        compression_profile="recovery_v1",
        permission_strategy="session_allow",
        agent_mode="single",
        validation_mode="stage_b",
    )


def runtime_contract_payload(root: Path) -> dict[str, Any]:
    profile = stage_d1_profile()
    return {
        "schema_version": 1,
        "runtime_source_sha256": {relative: _sha256(root / relative) for relative in RUNTIME_SOURCE_PATHS},
        "tool_parameter_schema_sha256": {
            "RunTest": canonical_hash(RunTestParams.model_json_schema()),
            "ValidationCheckpoint": canonical_hash(ValidationCheckpointParams.model_json_schema()),
        },
        "experiment_profile": profile.canonical_payload(),
        "experiment_profile_hash": profile.profile_hash(),
        "loop_entrypoints": ["Agent._run", "Agent.run_to_completion"],
        "protocol_guarantees": [
            "typed_checkpoint_declarations",
            "single_json_string_container_normalization",
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
        "stage": "D.1",
        "status": "frozen_zero_provider_single_task_canary_not_authorized",
        "experiment_kind": "stage-d1-live-tool-protocol-canary",
        "supersedes": {
            "stage": "D",
            "run_id": STAGE_D_CANARY_RUN,
            "artifact_sha256": STAGE_D_CANARY_ARTIFACT_SHA256,
            "result": "NO_GO",
            "historical_evidence_modified": False,
        },
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
            "authorization_cap_cny": "15",
            "rolling_per_request_reservation": True,
        },
        "canary_instance_ids": list(CANARY_INSTANCE_IDS),
        "admission": {
            "paid_execution_authorized": False,
            "authorization_identity": None,
            "workflow_dispatch_allowed": False,
            "required_before_execution": [
                "separate Stage D.1 authorization identity",
                "fresh immutable checkout commitment",
                "per-request rolling reservation contract",
                "zero-provider live-like E2E pass",
            ],
        },
        "claims_boundary": {
            "allowed_if_completed": [
                "one-task Stage D.1 protocol-canary process evidence",
                "a per-task scorable evaluator outcome",
                "descriptive Goal 4, Stage C, and Stage D comparison",
            ],
            "prohibited": [
                "Stage D historical-evidence modification",
                "Stage C Phase 2 claim",
                "second Stage D.1 task claim",
                "six-task Phase 1 claim",
                "twenty-task claim",
                "holdout_or_leaderboard_claim",
                "general_model_capability_claim",
            ],
            "next_decision": "Any second task requires a new user authorization after Stage D.1 reporting.",
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
        raise ValueError("Stage D.1 Freeze differs from the current deterministic contract")
    if actual["canary_instance_ids"] != list(CANARY_INSTANCE_IDS):
        raise ValueError("Stage D.1 Freeze does not contain the exact one-task canary")
    if actual["admission"]["paid_execution_authorized"] is not False:
        raise ValueError("Stage D.1 Freeze must not authorize paid execution")
    return actual


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("write", "validate"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--freeze", type=Path, default=Path("evals/stage_d1/stage_d1_freeze.json"))
    args = parser.parse_args()
    result = write_freeze(args.root, args.freeze) if args.command == "write" else validate_freeze(args.root, args.freeze)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

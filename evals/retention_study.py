"""Guarded 32K-context retention study with real automatic compaction calls."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

from codepacex.agent import Agent
from codepacex.client import create_client
from codepacex.config import load_config as load_codepacex_config
from codepacex.conversation import ConversationManager
from codepacex.experiments import CompressionProfile, ExperimentProfile
from codepacex.permissions import DangerousCommandDetector, PathSandbox, PermissionChecker, PermissionMode, RuleEngine
from codepacex.tools import create_default_registry
from evals.benchmark import RunManifest, RunRecorder, canonical_hash, current_git_commit, sanitize_origin
from evals.costing import load_pricing, pricing_snapshot_hash
from evals.goal2_studies import Goal2Studies, load_studies, retention_canaries
from evals.paid_gate import PaidRunGate
from evals.pilot import _provider_payload, _runtime_secrets, load_config as load_pilot_config

MAXIMUM_REQUESTS_PER_SESSION = 50
MAXIMUM_INPUT_TOKENS_PER_REQUEST = 32_768
MAXIMUM_OUTPUT_TOKENS_PER_REQUEST = 8192
FILLER_MESSAGE_COUNT = 8
FILLER_MESSAGE_CHARACTERS = 4000


def profiles(studies: Goal2Studies) -> list[ExperimentProfile]:
    return [ExperimentProfile(
        tool_loading="deferred", compression_profile=name,
        permission_strategy="default", agent_mode="single",
    ) for name in studies.retention.profiles]


def asset_hash(studies_path: Path) -> str:
    return canonical_hash({
        "studies": hashlib.sha256(studies_path.read_bytes()).hexdigest(),
        "runner": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "filler_message_count": FILLER_MESSAGE_COUNT,
        "filler_message_characters": FILLER_MESSAGE_CHARACTERS,
    })


def _git_dirty(root: Path) -> bool | None:
    result = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        text=True, capture_output=True, check=False,
    )
    return bool(result.stdout) if result.returncode == 0 else None


def build_manifest(
    *, root: Path, studies_path: Path, studies: Goal2Studies,
    profile: ExperimentProfile,
) -> RunManifest:
    pilot = load_pilot_config(root / "evals" / "pilot.qwen.yaml")
    return RunManifest(
        experiment_kind="goal2-retention-32k",
        provider=pilot.provider, protocol=pilot.protocol,
        base_url_origin=sanitize_origin(pilot.base_url),
        api_key_env=pilot.api_key_env, model_id=pilot.model_id,
        git_commit=current_git_commit(root), dirty_worktree=_git_dirty(root),
        prompt_version="retention-study-v1", feature_flags={},
        experiment_profile=profile.canonical_payload(),
        experiment_profile_hash=profile.profile_hash(),
        runtime_contract_hash=profile.runtime_contract_hash(),
        benchmark_asset_hash=asset_hash(studies_path),
        task_ids=[f"retention-session-{index + 1:02d}" for index in range(10)],
        repetitions=1,
        model_parameters=pilot.model_parameters.model_dump(mode="json"),
        context_window=studies.retention.context_window,
        max_output_tokens=pilot.model_parameters.max_output_tokens,
        retry_budget=0, fallback_enabled=False, max_iterations=50,
        experiment_config_hash=canonical_hash({
            "retention": studies.retention.model_dump(mode="json"),
            "profile": profile.canonical_payload(),
            "filler": [FILLER_MESSAGE_COUNT, FILLER_MESSAGE_CHARACTERS],
        }),
    )


def strict_canary_grade(answer: str, canaries: list[str]) -> tuple[bool, dict[str, Any]]:
    try:
        decoded = json.loads(answer)
    except json.JSONDecodeError:
        decoded = None
    exact_shape = (
        isinstance(decoded, dict)
        and set(decoded) == {"canaries"}
        and isinstance(decoded.get("canaries"), list)
    )
    observed = decoded.get("canaries") if exact_shape else []
    passed = exact_shape and observed == canaries
    return passed, {
        "strict_json": exact_shape,
        "expected_count": len(canaries),
        "observed_count": len(observed) if isinstance(observed, list) else 0,
        "ordered_exact_match": passed,
        "expected_sha256": canonical_hash(canaries),
        "observed_sha256": canonical_hash(observed) if isinstance(observed, list) else None,
    }


def filler_messages(seed: str, cycle: int) -> list[tuple[str, str]]:
    """Deterministic synthetic transcript load; no claim treats it as organic use."""
    pairs: list[tuple[str, str]] = []
    for index in range(FILLER_MESSAGE_COUNT):
        marker = hashlib.sha256(f"{seed}:{cycle}:{index}".encode()).hexdigest()
        body = ((f"controlled-filler-{marker} ") * 200)[:FILLER_MESSAGE_CHARACTERS]
        pairs.append((
            f"Retention load cycle {cycle}, item {index}: {body}",
            f"Acknowledged controlled load item {cycle}-{index}.",
        ))
    return pairs


class _Telemetry:
    def __init__(self, recorder: RunRecorder, task_id: str) -> None:
        self.recorder = recorder
        self.task_id = task_id
        self.request_count = 0
        self.compression_count = 0
        self.tool_uses: list[dict[str, Any]] = []
        self._cumulative_input = 0
        self._cumulative_output = 0

    def __call__(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "runtime_manifest":
            self.request_count += 1
            self.recorder.capture_event({
                **event, "request_index": self.request_count,
                "task_id": self.task_id, "repetition_id": "1", "attempt_id": 1,
            })
        elif event_type == "compression":
            if event.get("success"):
                self.compression_count += 1
            self.recorder.capture_event({
                **event, "task_id": self.task_id,
                "repetition_id": "1", "attempt_id": 1,
            })
        elif event_type == "usage" and self.request_count:
            nested = event.get("usage") if isinstance(event.get("usage"), dict) else None
            if nested is not None:
                total_input = int(nested.get("inputTokens") or 0)
                total_output = int(nested.get("outputTokens") or 0)
                request_input = max(0, total_input - self._cumulative_input)
                request_output = max(0, total_output - self._cumulative_output)
            else:
                total_input = int(event.get("input_tokens") or self._cumulative_input)
                total_output = int(event.get("output_tokens") or self._cumulative_output)
                request_input = int(event.get("request_input_tokens") or 0)
                request_output = int(event.get("request_output_tokens") or 0)
            self._cumulative_input = max(self._cumulative_input, total_input)
            self._cumulative_output = max(self._cumulative_output, total_output)
            self.recorder.capture_event({
                "type": "usage", "request_index": self.request_count,
                "input_tokens": total_input, "output_tokens": total_output,
                "request_input_tokens": request_input,
                "request_output_tokens": request_output,
                "provider_usage": event.get("provider_usage"),
                "task_id": self.task_id, "repetition_id": "1", "attempt_id": 1,
            })
        elif event_type == "tool_use":
            self.tool_uses.append(event)


def _write_provider_config(*, pilot: Any, path: Path, context_window: int) -> Any:
    payload = _provider_payload(pilot)
    payload["providers"][0]["context_window"] = context_window
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    config = load_codepacex_config(path)
    provider = config.providers[0]
    if provider.get_context_window() != context_window:
        raise ValueError("retention child context window changed during validation")
    return provider


async def _run_session(
    *, workspace: Path, pilot: Any, profile: ExperimentProfile,
    canaries: list[str], minimum_compactions: int, context_window: int,
    recorder: RunRecorder, task_id: str,
) -> tuple[str, dict[str, Any], int, int, int]:
    canary_path = workspace / "canaries.json"
    canary_path.write_text(json.dumps(canaries), encoding="utf-8")
    provider = _write_provider_config(
        pilot=pilot, path=workspace / ".home" / ".codepacex" / "config.yaml",
        context_window=context_window,
    )
    client = create_client(provider, max_retries=0)
    registry = create_default_registry()
    checker = PermissionChecker(
        DangerousCommandDetector(), PathSandbox(str(workspace)), RuleEngine(),
        PermissionMode.DEFAULT,
    )
    agent = Agent(
        client, registry, provider.protocol, work_dir=str(workspace),
        max_iterations=50, permission_checker=checker,
        context_window=context_window, active_provider=provider,
        providers=[provider], fallback=[], experiment_profile=profile,
    )
    conversation = ConversationManager()
    telemetry = _Telemetry(recorder, task_id)
    load_prompt = (
        f"Call ReadFile on the absolute path {canary_path} and retain the JSON array "
        "for a later exact-recall check. Reply only CANARIES_LOADED after the tool succeeds."
    )
    await agent.run_to_completion(load_prompt, conversation, telemetry)
    for cycle in range(1, minimum_compactions + 1):
        for user, assistant in filler_messages(task_id, cycle):
            conversation.add_user_message(user)
            conversation.add_assistant_message(assistant)
        await agent.run_to_completion(
            f"Reply exactly ACK_{cycle} for controlled retention cycle {cycle}.",
            conversation, telemetry,
        )
    answer = await agent.run_to_completion(
        'Return the retained values as exactly one JSON object: {"canaries":[...]} '
        "with the original 12 strings in original order. No Markdown or extra keys.",
        conversation, telemetry,
    )
    passed, grade = strict_canary_grade(answer, canaries)
    read_used = any(
        event.get("toolName") == "ReadFile"
        and str(event.get("args", {}).get("file_path", "")) == str(canary_path)
        for event in telemetry.tool_uses
    )
    grade.update({
        "read_file_used": read_used,
        "successful_compactions": telemetry.compression_count,
        "minimum_compactions": minimum_compactions,
        "synthetic_controlled_filler": True,
    })
    status = "success" if passed and read_used and telemetry.compression_count >= minimum_compactions else "task_failure"
    return (
        status, grade, telemetry.request_count,
        agent.total_input_tokens, agent.total_output_tokens,
    )


def dry_run(
    *, root: Path, studies_path: Path, runs_dir: Path, run_prefix: str,
) -> list[RunRecorder]:
    studies = load_studies(studies_path)
    recorders: list[RunRecorder] = []
    for profile in profiles(studies):
        recorder = RunRecorder(
            runs_dir,
            build_manifest(
                root=root, studies_path=studies_path, studies=studies, profile=profile,
            ),
            run_id=f"{run_prefix}-{profile.compression_profile.value}", repo_root=root,
        )
        recorder.event("dry_run", {
            "network_called": False, "model_called": False,
            "session_count": len(studies.retention.session_seeds),
            "canaries_per_session": studies.retention.canaries_per_session,
            "minimum_real_compactions": studies.retention.minimum_real_compactions,
            "context_window": studies.retention.context_window,
            "synthetic_controlled_filler": True,
        })
        recorder.finalize({
            "status": "dry_run", "execution_mode": "dry_run", "scorable": False,
        })
        recorders.append(recorder)
    return recorders


def execute(
    *, root: Path, studies_path: Path, runs_dir: Path, run_prefix: str,
    pricing_snapshot: Path, budget_authorization: Path, budget_ledger: Path,
    confirmed: bool,
) -> list[RunRecorder]:
    studies = load_studies(studies_path)
    pilot = load_pilot_config(root / "evals" / "pilot.qwen.yaml")
    if not confirmed or not os.environ.get(pilot.api_key_env):
        raise ValueError("execute requires --confirm-paid-run and the configured API key")
    pricing = load_pricing(pricing_snapshot)
    gate = PaidRunGate(
        root=root, authorization_path=budget_authorization,
        ledger_path=budget_ledger, pricing=pricing,
    )
    recorders: list[RunRecorder] = []
    with gate.locked():
        for profile in profiles(studies):
            manifest = build_manifest(
                root=root, studies_path=studies_path, studies=studies, profile=profile,
            )
            manifest.pricing_snapshot_hash = pricing_snapshot_hash(pricing)
            recorder = RunRecorder(
                runs_dir, manifest,
                run_id=f"{run_prefix}-{profile.compression_profile.value}",
                repo_root=root, secrets=_runtime_secrets(pilot),
            )
            statuses: list[str] = []
            for index, _seed in enumerate(studies.retention.session_seeds):
                task_id = f"retention-session-{index + 1:02d}"
                reservation = gate.reserve(
                    f"retention/{profile.compression_profile.value}/{task_id}",
                    maximum_requests=MAXIMUM_REQUESTS_PER_SESSION,
                    maximum_input_tokens_per_request=MAXIMUM_INPUT_TOKENS_PER_REQUEST,
                    maximum_output_tokens_per_request=MAXIMUM_OUTPUT_TOKENS_PER_REQUEST,
                )
                recorder.event("trial_started", {
                    "task_id": task_id, "repetition_id": "1", "attempt_id": 1,
                    "budget_reservation_id": reservation.reservation_id,
                })
                started = time.monotonic()
                try:
                    with tempfile.TemporaryDirectory(prefix=f"codepacex-{task_id}-") as text:
                        status, grade, requests, input_tokens, output_tokens = asyncio.run(
                            _run_session(
                                workspace=Path(text), pilot=pilot, profile=profile,
                                canaries=retention_canaries(studies.retention, index),
                                minimum_compactions=studies.retention.minimum_real_compactions,
                                context_window=studies.retention.context_window,
                                recorder=recorder, task_id=task_id,
                            )
                        )
                except Exception as exc:
                    recorder.event("trial_completed", {
                        "task_id": task_id, "repetition_id": "1", "attempt_id": 1,
                        "status": "infrastructure_error",
                        "duration_seconds": time.monotonic() - started,
                        "budget_reconciliation_required": True,
                        "error_category": type(exc).__name__,
                    })
                    statuses.append("infrastructure_error")
                    break
                settlement = gate.settle(
                    reservation, requests=requests,
                    input_tokens=input_tokens, output_tokens=output_tokens,
                )
                statuses.append(status)
                recorder.event("trial_completed", {
                    "task_id": task_id, "repetition_id": "1", "attempt_id": 1,
                    "status": status, "duration_seconds": time.monotonic() - started,
                    "actual_cny": str(settlement.actual_cny), "grade": grade,
                })
            aggregate = (
                "success" if statuses and all(item == "success" for item in statuses)
                else "infrastructure_error" if "infrastructure_error" in statuses
                else "task_failure"
            )
            recorder.finalize({
                "status": aggregate, "execution_mode": "live",
                "profile": profile.compression_profile.value,
                "synthetic_controlled_filler": True,
            })
            recorders.append(recorder)
            if aggregate == "infrastructure_error":
                break
    return recorders


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Goal 2 retention study")
    parser.add_argument("command", choices=["validate", "dry-run", "execute"])
    parser.add_argument("--studies", type=Path, default=Path("evals/goal2/studies.yaml"))
    parser.add_argument("--runs-dir", type=Path, default=Path("evals/.runs/goal2-retention"))
    parser.add_argument("--run-prefix", default="retention-dry")
    parser.add_argument("--pricing-snapshot", type=Path)
    parser.add_argument("--budget-authorization", type=Path)
    parser.add_argument("--budget-ledger", type=Path)
    parser.add_argument("--confirm-paid-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        root = Path.cwd()
        studies = load_studies(args.studies)
        payload: dict[str, Any] = {
            "valid": True, "session_count": 20,
            "canary_count": 120,
            "minimum_real_compactions_per_session": studies.retention.minimum_real_compactions,
            "context_window": studies.retention.context_window,
            "asset_hash": asset_hash(args.studies),
            "synthetic_controlled_filler": True,
        }
        if args.command == "dry-run":
            payload["run_paths"] = [str(item.path) for item in dry_run(
                root=root, studies_path=args.studies, runs_dir=args.runs_dir,
                run_prefix=args.run_prefix,
            )]
        elif args.command == "execute":
            required = [args.pricing_snapshot, args.budget_authorization, args.budget_ledger]
            if any(item is None for item in required):
                raise ValueError("execute requires pricing, budget authorization, and ledger paths")
            payload["run_paths"] = [str(item.path) for item in execute(
                root=root, studies_path=args.studies, runs_dir=args.runs_dir,
                run_prefix=args.run_prefix, pricing_snapshot=args.pricing_snapshot,
                budget_authorization=args.budget_authorization,
                budget_ledger=args.budget_ledger, confirmed=args.confirm_paid_run,
            )]
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"retention study error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

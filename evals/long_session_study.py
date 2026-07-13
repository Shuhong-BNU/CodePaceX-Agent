"""Resumable Goal 2 long-session supervisor with frozen wall-clock cadence."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from codepacex.agent import Agent
from codepacex.client import create_client
from codepacex.config import ProviderConfig
from codepacex.conversation import ConversationManager, Message, ThinkingBlock, ToolResultBlock, ToolUseBlock
from codepacex.experiments import ExperimentProfile
from codepacex.permissions import DangerousCommandDetector, PathSandbox, PermissionChecker, PermissionMode, RuleEngine
from codepacex.tools import create_default_registry
from evals.benchmark import RunManifest, RunRecorder, canonical_hash, current_git_commit, sanitize_origin
from evals.costing import load_pricing, pricing_snapshot_hash
from evals.goal2_studies import Goal2Studies, load_studies
from evals.paid_gate import PaidRunGate
from evals.pilot import _runtime_secrets, load_config as load_pilot_config

MAXIMUM_INPUT_TOKENS_PER_REQUEST = 128_000
MAXIMUM_OUTPUT_TOKENS_PER_REQUEST = 8192


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_timestamp(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def frozen_profile() -> ExperimentProfile:
    return ExperimentProfile(
        tool_loading="deferred", compression_profile="recovery_v1",
        permission_strategy="default", agent_mode="single",
    )


def schedule(
    studies: Goal2Studies, *, kind: Literal["pilot", "formal"], index: int,
) -> dict[str, Any]:
    if kind == "pilot":
        if index != 1:
            raise ValueError("long-session pilot index must be 1")
        count = studies.long_session.pilot.count
        duration_hours = studies.long_session.pilot.duration_hours
        restart_minutes = studies.long_session.pilot.planned_restart_minutes
    else:
        count = studies.long_session.formal.count
        if not 1 <= index <= count:
            raise ValueError("long-session formal index is out of range")
        duration_hours = studies.long_session.formal.duration_hours
        restart_minutes = studies.long_session.formal.planned_restart_hours * 60
    cycles = duration_hours * 60 // studies.long_session.workload_interval_minutes
    restart_after_cycle = restart_minutes // studies.long_session.workload_interval_minutes
    checkpoint_every_cycles = (
        studies.long_session.checkpoint_interval_minutes
        // studies.long_session.workload_interval_minutes
    )
    return {
        "kind": kind, "index": index, "declared_count": count,
        "duration_hours": duration_hours, "cycle_count": cycles,
        "workload_interval_minutes": studies.long_session.workload_interval_minutes,
        "checkpoint_every_cycles": checkpoint_every_cycles,
        "restart_after_cycle": restart_after_cycle,
        "maximum_provider_requests_per_cycle": (
            studies.long_session.maximum_provider_requests_per_cycle
        ),
    }


def checkpoint_hash(payload: dict[str, Any]) -> str:
    return canonical_hash({key: value for key, value in payload.items() if key != "checkpoint_hash"})


def validate_checkpoint_chain(checkpoints: list[dict[str, Any]]) -> None:
    previous: str | None = None
    for checkpoint in checkpoints:
        if checkpoint.get("previous_checkpoint_hash") != previous:
            raise ValueError("long-session checkpoint chain is broken")
        if checkpoint.get("checkpoint_hash") != checkpoint_hash(checkpoint):
            raise ValueError("long-session checkpoint content hash mismatch")
        previous = str(checkpoint["checkpoint_hash"])


def _message_payload(message: Message) -> dict[str, Any]:
    return dataclasses.asdict(message)


def _message_from_payload(payload: dict[str, Any]) -> Message:
    return Message(
        role=str(payload["role"]), content=str(payload.get("content", "")),
        tool_uses=[ToolUseBlock(**item) for item in payload.get("tool_uses", [])],
        tool_results=[ToolResultBlock(**item) for item in payload.get("tool_results", [])],
        thinking_blocks=[ThinkingBlock(**item) for item in payload.get("thinking_blocks", [])],
    )


def _write_checkpoint(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    existing: list[dict[str, Any]] = []
    if path.exists():
        decoded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(decoded, list):
            raise ValueError("long-session checkpoint file must contain a list")
        existing = decoded
        validate_checkpoint_chain(existing)
    payload = {
        **payload,
        "previous_checkpoint_hash": (
            existing[-1]["checkpoint_hash"] if existing else None
        ),
    }
    payload["checkpoint_hash"] = checkpoint_hash(payload)
    existing.append(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return payload


def load_latest_checkpoint(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    decoded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(decoded, list):
        raise ValueError("long-session checkpoint file must contain a list")
    validate_checkpoint_chain(decoded)
    return decoded[-1] if decoded else None


def strict_cycle_grade(answer: str, *, cycle: int, marker: str) -> tuple[bool, dict[str, Any]]:
    try:
        decoded = json.loads(answer)
    except json.JSONDecodeError:
        decoded = None
    expected = {"cycle": cycle, "marker": marker, "status": "ok"}
    passed = decoded == expected
    return passed, {
        "strict_json": isinstance(decoded, dict),
        "exact_match": passed,
        "expected_sha256": canonical_hash(expected),
        "observed_sha256": canonical_hash(decoded) if isinstance(decoded, dict) else None,
    }


def recovery_rate_fields(status: str, *, recovery_probe: bool) -> dict[str, int]:
    if not recovery_probe:
        return {}
    return {"numerator": int(status == "success"), "denominator": 1}


def _git_dirty(root: Path) -> bool | None:
    result = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        text=True, capture_output=True, check=False,
    )
    return bool(result.stdout) if result.returncode == 0 else None


def build_manifest(
    *, root: Path, studies_path: Path, studies: Goal2Studies,
    kind: Literal["pilot", "formal"], index: int,
) -> RunManifest:
    pilot = load_pilot_config(root / "evals" / "pilot.qwen.yaml")
    spec = schedule(studies, kind=kind, index=index)
    profile = frozen_profile()
    task_id = f"long-{kind}-{index}"
    return RunManifest(
        experiment_kind="goal2-long-session",
        provider=pilot.provider, protocol=pilot.protocol,
        base_url_origin=sanitize_origin(pilot.base_url),
        api_key_env=pilot.api_key_env, model_id=pilot.model_id,
        git_commit=current_git_commit(root), dirty_worktree=_git_dirty(root),
        prompt_version="long-session-v1", feature_flags={},
        experiment_profile=profile.canonical_payload(),
        experiment_profile_hash=profile.profile_hash(),
        runtime_contract_hash=profile.runtime_contract_hash(),
        benchmark_asset_hash=canonical_hash({
            "studies_sha256": hashlib.sha256(studies_path.read_bytes()).hexdigest(),
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "schedule": spec,
        }),
        task_ids=[task_id], repetitions=1,
        model_parameters=pilot.model_parameters.model_dump(mode="json"),
        context_window=MAXIMUM_INPUT_TOKENS_PER_REQUEST,
        max_output_tokens=pilot.model_parameters.max_output_tokens,
        retry_budget=0, fallback_enabled=False,
        max_iterations=spec["maximum_provider_requests_per_cycle"],
        experiment_config_hash=canonical_hash({
            "long_session": studies.long_session.model_dump(mode="json"),
            "schedule": spec, "profile": profile.canonical_payload(),
        }),
    )


class _CycleTelemetry:
    def __init__(self, recorder: RunRecorder, task_id: str, cycle: int) -> None:
        self.recorder = recorder
        self.task_id = task_id
        self.cycle = cycle
        self.requests = 0
        self.request_usages: list[tuple[int, int]] = []

    def __call__(self, event: dict[str, Any]) -> None:
        if event.get("type") == "runtime_manifest":
            self.requests += 1
            self.recorder.capture_event({
                **event, "request_index": self.requests,
                "task_id": self.task_id, "repetition_id": str(self.cycle),
                "attempt_id": 1,
            })
        elif event.get("type") == "usage" and self.requests:
            self.request_usages.append((
                int(event.get("request_input_tokens") or 0),
                int(event.get("request_output_tokens") or 0),
            ))
            self.recorder.capture_event({
                **event, "request_index": self.requests,
                "task_id": self.task_id, "repetition_id": str(self.cycle),
                "attempt_id": 1,
            })
        elif event.get("type") == "compression":
            self.recorder.capture_event({
                **event, "task_id": self.task_id,
                "repetition_id": str(self.cycle), "attempt_id": 1,
            })


def _provider(pilot: Any) -> ProviderConfig:
    return ProviderConfig(
        name=pilot.provider, protocol=pilot.protocol, base_url=pilot.base_url,
        model=pilot.model_id, api_key_env=pilot.api_key_env,
        context_window=MAXIMUM_INPUT_TOKENS_PER_REQUEST,
        max_output_tokens=pilot.model_parameters.max_output_tokens or 8192,
    )


def _new_runtime(
    *, pilot: Any, workspace: Path, profile: ExperimentProfile,
) -> Agent:
    provider = _provider(pilot)
    checker = PermissionChecker(
        DangerousCommandDetector(), PathSandbox(str(workspace)), RuleEngine(),
        PermissionMode.DEFAULT,
    )
    return Agent(
        create_client(provider, max_retries=0), create_default_registry(),
        provider.protocol, work_dir=str(workspace), max_iterations=10,
        permission_checker=checker, context_window=provider.context_window,
        active_provider=provider, providers=[provider], fallback=[],
        experiment_profile=profile,
    )


async def _run_cycle(
    *, agent: Agent, conversation: ConversationManager, recorder: RunRecorder,
    task_id: str, cycle: int, marker: str,
) -> tuple[str, dict[str, Any], list[tuple[int, int]]]:
    telemetry = _CycleTelemetry(recorder, task_id, cycle)
    answer = await agent.run_to_completion(
        f"Long-session continuity check {cycle}. Return exactly one JSON object "
        f'{{"cycle":{cycle},"marker":"{marker}","status":"ok"}}. '
        "Use no tools, Markdown, or extra keys.",
        conversation, telemetry,
    )
    passed, grade = strict_cycle_grade(answer, cycle=cycle, marker=marker)
    status = "success" if passed and telemetry.requests <= 10 else "task_failure"
    if len(telemetry.request_usages) != telemetry.requests:
        raise ValueError("long-session provider request usage is incomplete")
    return status, grade, telemetry.request_usages


def dry_run(
    *, root: Path, studies_path: Path, runs_dir: Path, run_prefix: str,
    kind: Literal["pilot", "formal"], index: int,
) -> RunRecorder:
    studies = load_studies(studies_path)
    spec = schedule(studies, kind=kind, index=index)
    recorder = RunRecorder(
        runs_dir,
        build_manifest(
            root=root, studies_path=studies_path, studies=studies,
            kind=kind, index=index,
        ),
        run_id=f"{run_prefix}-{kind}-{index}", repo_root=root,
    )
    recorder.event("dry_run", {
        "network_called": False, "model_called": False, "schedule": spec,
        "real_wall_clock_not_started": True,
    })
    recorder.finalize({
        "status": "dry_run", "execution_mode": "dry_run", "scorable": False,
    })
    return recorder


def execute(
    *, root: Path, studies_path: Path, runs_dir: Path, run_id: str,
    kind: Literal["pilot", "formal"], index: int,
    pricing_snapshot: Path, budget_authorization: Path, budget_ledger: Path,
    checkpoint_path: Path, confirmed: bool, resume: bool,
    budget_stage: Literal["A", "B", "C"] = "C",
) -> RunRecorder:
    studies = load_studies(studies_path)
    spec = schedule(studies, kind=kind, index=index)
    pilot = load_pilot_config(root / "evals" / "pilot.qwen.yaml")
    if not confirmed or not os.environ.get(pilot.api_key_env):
        raise ValueError("execute requires --confirm-paid-run and the configured API key")
    pricing = load_pricing(pricing_snapshot)
    gate = PaidRunGate(
        root=root, authorization_path=budget_authorization,
        ledger_path=budget_ledger, pricing=pricing, stage=budget_stage,
    )
    manifest = build_manifest(
        root=root, studies_path=studies_path, studies=studies,
        kind=kind, index=index,
    )
    manifest.pricing_snapshot_hash = pricing_snapshot_hash(pricing)
    if resume:
        recorder = RunRecorder.resume(
            runs_dir, run_id, manifest, secrets=_runtime_secrets(pilot),
        )
    else:
        recorder = RunRecorder(
            runs_dir, manifest, run_id=run_id, repo_root=root,
            secrets=_runtime_secrets(pilot),
        )
    latest = load_latest_checkpoint(checkpoint_path) if resume else None
    task_id = f"long-{kind}-{index}"
    marker = canonical_hash({"task_id": task_id, "commit": manifest.git_commit})[:24]
    if latest:
        if latest.get("run_id") != run_id or latest.get("marker_sha256") != canonical_hash(marker):
            raise ValueError("long-session checkpoint identity mismatch")
        conversation = ConversationManager()
        conversation.history = [_message_from_payload(item) for item in latest["conversation"]]
        conversation.env_injected = bool(latest.get("env_injected"))
        conversation.ltm_injected = bool(latest.get("ltm_injected"))
        next_cycle = int(latest["completed_cycle"]) + 1
        completed_before = int(latest["completed_cycle"])
        started_at = str(latest["started_at"])
        restart_completed = bool(latest["restart_completed"])
    else:
        conversation = ConversationManager()
        conversation.add_user_message(
            f"Long-session continuity marker: {marker}. Preserve it for all later checks."
        )
        next_cycle = 1
        completed_before = 0
        started_at = _utc_now()
        restart_completed = False
    profile = frozen_profile()
    workspace = checkpoint_path.parent / f"{run_id}-workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    agent = _new_runtime(pilot=pilot, workspace=workspace, profile=profile)
    statuses: list[str] = []
    with gate.locked():
        for cycle in range(next_cycle, int(spec["cycle_count"]) + 1):
            recovery_probe = (
                restart_completed
                and cycle == int(spec["restart_after_cycle"]) + 1
            )
            due = (
                _utc_timestamp(started_at)
                + cycle * int(spec["workload_interval_minutes"]) * 60
            )
            delay = due - time.time()
            if delay > 0:
                time.sleep(delay)
            reservation = gate.reserve(
                f"long_session/{task_id}/cycle-{cycle}",
                maximum_requests=int(spec["maximum_provider_requests_per_cycle"]),
                maximum_input_tokens_per_request=MAXIMUM_INPUT_TOKENS_PER_REQUEST,
                maximum_output_tokens_per_request=MAXIMUM_OUTPUT_TOKENS_PER_REQUEST,
            )
            recorder.event("trial_started", {
                "task_id": task_id, "repetition_id": str(cycle), "attempt_id": 1,
                "budget_reservation_id": reservation.reservation_id,
                "scheduled_at": datetime.fromtimestamp(due, timezone.utc).isoformat(),
            })
            started = time.monotonic()
            try:
                status, grade, request_usages = asyncio.run(
                    _run_cycle(
                        agent=agent, conversation=conversation, recorder=recorder,
                        task_id=task_id, cycle=cycle, marker=marker,
                    )
                )
            except Exception as exc:
                recorder.event("trial_completed", {
                    "task_id": task_id, "repetition_id": str(cycle), "attempt_id": 1,
                    "status": "infrastructure_error",
                    "budget_reconciliation_required": True,
                    "error_category": type(exc).__name__,
                })
                statuses.append("infrastructure_error")
                break
            settlement = gate.settle(
                reservation, request_usages=request_usages,
            )
            statuses.append(status)
            recorder.event("trial_completed", {
                "task_id": task_id, "repetition_id": str(cycle), "attempt_id": 1,
                "status": status, "duration_seconds": time.monotonic() - started,
                "actual_cny": str(settlement.actual_cny), "grade": grade,
                "post_restart_recovery_probe": recovery_probe,
                **recovery_rate_fields(status, recovery_probe=recovery_probe),
            })
            checkpoint_due = cycle % int(spec["checkpoint_every_cycles"]) == 0
            restart_due = cycle == int(spec["restart_after_cycle"]) and not restart_completed
            if checkpoint_due or restart_due or cycle == int(spec["cycle_count"]):
                checkpoint = _write_checkpoint(checkpoint_path, {
                    "schema_version": 1, "run_id": run_id, "task_id": task_id,
                    "started_at": started_at, "checkpointed_at": _utc_now(),
                    "completed_cycle": cycle,
                    "restart_completed": restart_completed or restart_due,
                    "marker_sha256": canonical_hash(marker),
                    "conversation": [_message_payload(item) for item in conversation.history],
                    "env_injected": conversation.env_injected,
                    "ltm_injected": conversation.ltm_injected,
                })
                recorder.event("long_session_checkpoint", {
                    "task_id": task_id, "completed_cycle": cycle,
                    "checkpoint_hash": checkpoint["checkpoint_hash"],
                    "planned_restart": restart_due,
                })
            if restart_due:
                # Recreate provider client, Agent state, and conversation object from
                # the just-written durable checkpoint. The supervisor process stays
                # alive so it retains the single paid-session lock.
                latest = load_latest_checkpoint(checkpoint_path)
                assert latest is not None
                conversation = ConversationManager()
                conversation.history = [
                    _message_from_payload(item) for item in latest["conversation"]
                ]
                conversation.env_injected = bool(latest.get("env_injected"))
                conversation.ltm_injected = bool(latest.get("ltm_injected"))
                agent = _new_runtime(pilot=pilot, workspace=workspace, profile=profile)
                restart_completed = True
                recorder.event("planned_runtime_restart_completed", {
                    "task_id": task_id, "after_cycle": cycle,
                    "checkpoint_hash": latest["checkpoint_hash"],
                })
        aggregate = (
            "success" if completed_before + len(statuses) == int(spec["cycle_count"])
            and all(item == "success" for item in statuses)
            else "infrastructure_error" if "infrastructure_error" in statuses
            else "task_failure"
        )
        recorder.finalize({
            "status": aggregate, "execution_mode": "live",
            "session_kind": kind, "session_index": index,
            "completed_cycles_this_process": len(statuses),
            "planned_runtime_restart_completed": restart_completed,
            "real_wall_clock_session": True,
        })
    return recorder


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Goal 2 long-session supervisor")
    parser.add_argument("command", choices=["validate", "dry-run", "execute", "resume"])
    parser.add_argument("--studies", type=Path, default=Path("evals/goal2/studies.yaml"))
    parser.add_argument("--runs-dir", type=Path, default=Path("evals/.runs/goal2-long"))
    parser.add_argument("--run-id", default="long-session")
    parser.add_argument("--run-prefix", default="long-dry")
    parser.add_argument("--kind", choices=["pilot", "formal"], default="pilot")
    parser.add_argument("--index", type=int, default=1)
    parser.add_argument("--pricing-snapshot", type=Path)
    parser.add_argument("--budget-authorization", type=Path)
    parser.add_argument("--budget-ledger", type=Path)
    parser.add_argument("--budget-stage", choices=["A", "B", "C"])
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--confirm-paid-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        root = Path.cwd()
        studies = load_studies(args.studies)
        spec = schedule(studies, kind=args.kind, index=args.index)
        payload: dict[str, Any] = {"valid": True, "schedule": spec}
        if args.command == "dry-run":
            recorder = dry_run(
                root=root, studies_path=args.studies, runs_dir=args.runs_dir,
                run_prefix=args.run_prefix, kind=args.kind, index=args.index,
            )
            payload["run_path"] = str(recorder.path)
        elif args.command in {"execute", "resume"}:
            required = [
                args.pricing_snapshot, args.budget_authorization,
                args.budget_ledger, args.checkpoint, args.budget_stage,
            ]
            if any(item is None for item in required):
                raise ValueError("live long-session requires pricing, budget, ledger, and checkpoint paths")
            recorder = execute(
                root=root, studies_path=args.studies, runs_dir=args.runs_dir,
                run_id=args.run_id, kind=args.kind, index=args.index,
                pricing_snapshot=args.pricing_snapshot,
                budget_authorization=args.budget_authorization,
                budget_ledger=args.budget_ledger, checkpoint_path=args.checkpoint,
                confirmed=args.confirm_paid_run, resume=args.command == "resume",
                budget_stage=args.budget_stage,
            )
            payload["run_path"] = str(recorder.path)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"long-session study error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

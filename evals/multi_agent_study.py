"""Guarded single-vs-multi Agent study on frozen cross-file fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Literal

import yaml

from codepacex.experiments import AgentMode, ExperimentProfile
from evals.benchmark import RunManifest, RunRecorder, canonical_hash, current_git_commit, sanitize_origin
from evals.costing import load_pricing, pricing_snapshot_hash
from evals.goal2_studies import Goal2Studies, MultiTask, load_studies
from evals.paid_gate import PaidRunGate, provider_request_budget_environment
from evals.permission_study import trace_usage
from evals.pilot import (
    _child_environment, _ingest_trace, _provider_payload, _runtime_secrets,
    load_config as load_pilot_config, trace_request_usages,
)

FIXTURE = Path("evals/fixtures/multi_agent")
MAXIMUM_REQUESTS_PER_TRIAL = 50
MAXIMUM_INPUT_TOKENS_PER_REQUEST = 128_000
MAXIMUM_OUTPUT_TOKENS_PER_REQUEST = 8192
WORKER_DEFINITION = """---
name: benchmark-worker
description: Goal 2 controlled cross-file implementation worker
permissionMode: acceptEdits
maxTurns: 20
---
Implement only the exact file assigned by the lead Agent. Read the relevant frozen test.
Do not edit tests, configuration, or unrelated files. Finish the edit and report concisely.
"""


def profiles(studies: Goal2Studies) -> list[ExperimentProfile]:
    return [ExperimentProfile(
        tool_loading="deferred", compression_profile="recovery_v1",
        permission_strategy="session_allow", agent_mode=mode,
    ) for mode in studies.multi_agent.modes]


def scoped_tasks(
    studies: Goal2Studies, *, scope: Literal["pilot", "formal"],
) -> tuple[list[MultiTask], int]:
    if scope == "formal":
        return list(studies.multi_agent.tasks), studies.multi_agent.repetitions
    return [studies.multi_agent.tasks[0]], 1


def asset_hash(studies_path: Path) -> str:
    files = {
        "studies": studies_path,
        "runner": Path(__file__),
        **{
            f"fixture/{path.relative_to(FIXTURE)}": path
            for path in sorted(FIXTURE.rglob("*")) if path.is_file()
        },
    }
    return canonical_hash({
        label: hashlib.sha256(path.read_bytes()).hexdigest()
        for label, path in files.items()
    } | {"worker_definition": hashlib.sha256(WORKER_DEFINITION.encode()).hexdigest()})


def _git_dirty(root: Path) -> bool | None:
    result = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        text=True, capture_output=True, check=False,
    )
    return bool(result.stdout) if result.returncode == 0 else None


def build_manifest(
    *, root: Path, studies_path: Path, studies: Goal2Studies,
    profile: ExperimentProfile, scope: Literal["pilot", "formal"] = "formal",
) -> RunManifest:
    pilot = load_pilot_config(root / "evals" / "pilot.qwen.yaml")
    tasks, repetitions = scoped_tasks(studies, scope=scope)
    return RunManifest(
        experiment_kind="goal2-single-vs-multi-agent",
        provider=pilot.provider, protocol=pilot.protocol,
        base_url_origin=sanitize_origin(pilot.base_url),
        api_key_env=pilot.api_key_env, model_id=pilot.model_id,
        git_commit=current_git_commit(root), dirty_worktree=_git_dirty(root),
        prompt_version="multi-agent-study-v1", feature_flags={},
        experiment_profile=profile.canonical_payload(),
        experiment_profile_hash=profile.profile_hash(),
        runtime_contract_hash=profile.runtime_contract_hash(),
        benchmark_asset_hash=asset_hash(studies_path),
        task_ids=[task.id for task in tasks],
        repetitions=repetitions,
        model_parameters=pilot.model_parameters.model_dump(mode="json"),
        max_output_tokens=pilot.model_parameters.max_output_tokens,
        retry_budget=0, fallback_enabled=False, max_iterations=50,
        experiment_config_hash=canonical_hash({
            "multi_agent": studies.multi_agent.model_dump(mode="json"),
            "profile": profile.canonical_payload(),
            "scope": scope,
        }),
    )


def _events(trace_text: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for line in trace_text.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            result.append(item)
    return result


def agent_summary(trace_text: str) -> dict[str, Any] | None:
    summaries = [
        item for item in _events(trace_text)
        if item.get("type") == "experiment_agent_summary"
    ]
    return summaries[0] if len(summaries) == 1 else None


def changed_files(workspace: Path) -> list[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=workspace,
        text=True, capture_output=True, check=False,
    )
    if result.returncode != 0:
        raise ValueError("cannot inspect multi-agent fixture changes")
    paths: list[str] = []
    for line in result.stdout.splitlines():
        raw = line[3:]
        path = raw.split(" -> ")[-1]
        paths.append(path)
    return sorted(set(paths))


def grade_trial(
    *, task: MultiTask, mode: AgentMode, trace_text: str,
    workspace: Path, test_returncode: int,
) -> tuple[bool, dict[str, Any]]:
    summary = agent_summary(trace_text)
    changed = changed_files(workspace)
    child_count = int(summary.get("child_count", -1)) if summary else -1
    delegation_ok = child_count == 0 if mode is AgentMode.SINGLE else 1 <= child_count <= 3
    mode_ok = bool(summary and summary.get("agent_mode") == mode.value)
    expected_changes = sorted(task.expected_files)
    conflict_markers = any(
        "<<<<<<<" in (workspace / path).read_text(encoding="utf-8", errors="replace")
        for path in expected_changes if (workspace / path).is_file()
    )
    passed = (
        test_returncode == 0 and changed == expected_changes
        and delegation_ok and mode_ok and not conflict_markers
    )
    return passed, {
        "test_passed": test_returncode == 0,
        "expected_files": expected_changes,
        "changed_files": changed,
        "exact_change_scope": changed == expected_changes,
        "delegation_ok": delegation_ok,
        "runtime_mode_ok": mode_ok,
        "child_count": child_count,
        "completed_child_count": summary.get("completed_child_count") if summary else None,
        "failed_child_count": summary.get("failed_child_count") if summary else None,
        "child_input_tokens": summary.get("child_input_tokens") if summary else None,
        "child_output_tokens": summary.get("child_output_tokens") if summary else None,
        "child_request_count": summary.get("child_request_count") if summary else None,
        "maximum_parallel_children": summary.get("maximum_parallel_children") if summary else None,
        "integration_conflict_markers": conflict_markers,
    }


def grader_preflight(*, studies_path: Path) -> dict[str, Any]:
    """Exercise the frozen scope grader with real fixture noise and no model."""
    studies = load_studies(studies_path)
    task = studies.multi_agent.tasks[0]
    with tempfile.TemporaryDirectory(prefix="codepacex-multi-grader-") as text:
        workspace = Path(text)
        _prepare_workspace(workspace)
        (workspace / "mini_multi" / "parser.py").write_text(
            "def parse_command(raw: str) -> tuple[str, str]:\n"
            "    command, separator, value = raw.strip().partition(\":\")\n"
            "    return command.strip().lower(), value.strip() if separator else \"\"\n",
            encoding="utf-8",
        )
        (workspace / "mini_multi" / "commands.py").write_text(
            "ALIASES = {\"ls\": \"list\", \"rm\": \"remove\"}\n\n"
            "def resolve_command(name: str) -> str:\n"
            "    normalized = name.strip().lower()\n"
            "    return ALIASES.get(normalized, normalized)\n",
            encoding="utf-8",
        )
        (workspace / ".codepacex" / "debug.log").write_text("preflight\n", encoding="utf-8")
        test = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", task.test_file],
            cwd=workspace, text=True, capture_output=True, check=False,
            env={**os.environ, "PYTHONPATH": str(workspace)},
        )
        trace = json.dumps({
            "type": "experiment_agent_summary", "agent_mode": "multi",
            "child_count": 1, "completed_child_count": 1, "failed_child_count": 0,
            "child_input_tokens": 0, "child_output_tokens": 0,
            "child_request_count": 0, "maximum_parallel_children": 1,
        })
        passed, grade = grade_trial(
            task=task, mode=AgentMode.MULTI, trace_text=trace,
            workspace=workspace, test_returncode=test.returncode,
        )
    return {
        "model_called": False, "network_called": False,
        "status": "GO" if passed else "NO-GO",
        "test_returncode": test.returncode, "grade": grade,
    }


def success_rate_fields(status: str) -> dict[str, int]:
    return {"numerator": int(status == "success"), "denominator": 1}


def _prepare_workspace(workspace: Path) -> None:
    for source in FIXTURE.iterdir():
        target = workspace / source.name
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    agents = workspace / ".codepacex" / "agents"
    agents.mkdir(parents=True)
    (agents / "benchmark-worker.md").write_text(WORKER_DEFINITION, encoding="utf-8")
    commands = [
        ["git", "init", "-q"],
        ["git", "config", "user.email", "goal2@codepacex.invalid"],
        ["git", "config", "user.name", "CodePaceX Goal2"],
        ["git", "add", "."],
        ["git", "commit", "-q", "-m", "frozen fixture"],
    ]
    for command in commands:
        result = subprocess.run(
            command, cwd=workspace, text=True, capture_output=True, check=False,
        )
        if result.returncode != 0:
            raise ValueError(f"failed to prepare fixture Git repository: {command[1]}")


def _write_child_config(*, pilot: Any, home: Path) -> None:
    payload = _provider_payload(pilot)
    payload["enable_fork"] = False
    payload["sandbox"] = {
        "enabled": False, "auto_allow": False, "network_enabled": False,
    }
    config_dir = home / ".codepacex"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=True), encoding="utf-8",
    )


def _task_prompt(task: MultiTask, mode: AgentMode) -> str:
    delegation = (
        "The Agent tool is unavailable in this arm; solve both files directly."
        if mode is AgentMode.SINGLE else
        "You MUST call exactly one foreground Agent with subagent_type benchmark-worker, "
        f"delegating exactly one of {task.expected_files}; then complete the other file yourself."
    )
    return (
        f"Controlled benchmark task {task.id}: {task.prompt}. {delegation} "
        f"Only modify {task.expected_files}. Never edit tests. "
        f"Use {task.test_file} as the specification and finish with the workspace passing that test."
    )


def dry_run(
    *, root: Path, studies_path: Path, runs_dir: Path, run_prefix: str,
    scope: Literal["pilot", "formal"] = "formal",
) -> list[RunRecorder]:
    studies = load_studies(studies_path)
    tasks, repetitions = scoped_tasks(studies, scope=scope)
    recorders: list[RunRecorder] = []
    for profile in profiles(studies):
        recorder = RunRecorder(
            runs_dir,
            build_manifest(
                root=root, studies_path=studies_path, studies=studies,
                profile=profile, scope=scope,
            ),
            run_id=f"{run_prefix}-{profile.agent_mode.value}", repo_root=root,
        )
        recorder.event("dry_run", {
            "network_called": False, "model_called": False,
            "planned_trial_count": (
                len(tasks) * repetitions
            ),
            "scope": scope,
            "maximum_workers": studies.multi_agent.maximum_workers,
        })
        recorder.finalize({
            "status": "dry_run", "execution_mode": "dry_run", "scorable": False,
        })
        recorders.append(recorder)
    return recorders


def _run_profile(
    *, root: Path, studies: Goal2Studies, profile: ExperimentProfile,
    recorder: RunRecorder, gate: PaidRunGate,
    scope: Literal["pilot", "formal"],
) -> str:
    pilot = load_pilot_config(root / "evals" / "pilot.qwen.yaml")
    statuses: list[str] = []
    with tempfile.TemporaryDirectory(prefix="codepacex-multi-home-") as home_text:
        home = Path(home_text)
        _write_child_config(pilot=pilot, home=home)
        profile_path = home / "profile.yaml"
        profile_path.write_text(
            yaml.safe_dump(profile.canonical_payload(), sort_keys=True), encoding="utf-8",
        )
        environment = _child_environment(pilot, home_text, root=root)
        tasks, repetitions = scoped_tasks(studies, scope=scope)
        for repetition in range(1, repetitions + 1):
            for task in tasks:
                trial_id = (
                    f"multi/{recorder.run_id}/{profile.agent_mode.value}/"
                    f"{task.id}/{repetition}"
                )
                recorder.event("trial_started", {
                    "task_id": task.id, "repetition_id": str(repetition),
                    "attempt_id": 1, "budget_mode": "per_provider_request",
                })
                started = time.monotonic()
                with tempfile.TemporaryDirectory(prefix=f"codepacex-{task.id}-") as text:
                    workspace = Path(text)
                    _prepare_workspace(workspace)
                    try:
                        request_environment = dict(environment)
                        request_environment.update(provider_request_budget_environment(
                            gate, trial_id=trial_id,
                            maximum_input_tokens_per_request=MAXIMUM_INPUT_TOKENS_PER_REQUEST,
                            maximum_output_tokens_per_request=MAXIMUM_OUTPUT_TOKENS_PER_REQUEST,
                        ))
                        process = subprocess.run(
                            [
                                sys.executable, "-m", "codepacex", "-p",
                                _task_prompt(task, profile.agent_mode),
                                "--output-format", "stream-json",
                                "--experiment-profile", str(profile_path),
                            ], cwd=workspace, env=request_environment,
                            text=True, capture_output=True, timeout=900, check=False,
                        )
                    except subprocess.TimeoutExpired:
                        recorder.event("trial_completed", {
                            "task_id": task.id, "repetition_id": str(repetition),
                            "attempt_id": 1, "status": "timeout",
                            "budget_reconciliation_required": True,
                        })
                        return "timeout"
                    test_environment = dict(environment)
                    test_environment.pop(pilot.api_key_env, None)
                    test_environment["PYTHONPATH"] = str(workspace)
                    test = subprocess.run(
                        [sys.executable, "-m", "pytest", "-q", task.test_file],
                        cwd=workspace, env=test_environment,
                        text=True, capture_output=True, timeout=120, check=False,
                    )
                    passed, grade = grade_trial(
                        task=task, mode=profile.agent_mode,
                        trace_text=process.stdout or "", workspace=workspace,
                        test_returncode=test.returncode,
                    )
                    parent_usages = trace_request_usages(process.stdout or "")
                    summary = agent_summary(process.stdout or "") or {}
                    child_usage_payload = summary.get("child_request_usages")
                    if not isinstance(child_usage_payload, list):
                        child_usage_payload = []
                    child_usages = [
                        (int(item.get("input_tokens") or 0), int(item.get("output_tokens") or 0))
                        for item in child_usage_payload if isinstance(item, dict)
                    ]
                    if (
                        len(child_usages) != int(summary.get("child_request_count") or 0)
                        or sum(item[0] for item in child_usages)
                        != int(summary.get("child_input_tokens") or 0)
                        or sum(item[1] for item in child_usages)
                        != int(summary.get("child_output_tokens") or 0)
                    ):
                        recorder.event("trial_completed", {
                            "task_id": task.id, "repetition_id": str(repetition),
                            "attempt_id": 1, "status": "infrastructure_error",
                            "budget_reconciliation_required": True,
                            "error_category": "incomplete_child_request_usage",
                        })
                        return "infrastructure_error"
                    request_usages = [*parent_usages, *child_usages]
                    requests = len(request_usages)
                    input_tokens = sum(item[0] for item in request_usages)
                    output_tokens = sum(item[1] for item in request_usages)
                    if requests == 0:
                        recorder.event("trial_completed", {
                            "task_id": task.id, "repetition_id": str(repetition),
                            "attempt_id": 1, "status": "infrastructure_error",
                            "budget_reconciliation_required": True,
                        })
                        return "infrastructure_error"
                    with tempfile.NamedTemporaryFile("w", suffix=".ndjson", encoding="utf-8") as trace:
                        trace.write(process.stdout or "")
                        trace.flush()
                        _ingest_trace(
                            recorder, Path(trace.name), task.id, str(repetition), 1,
                        )
                accounting = gate.trial_accounting(trial_id)
                if accounting["budget_blocked"]:
                    recorder.event("trial_completed", {
                        "task_id": task.id, "repetition_id": str(repetition),
                        "attempt_id": 1, "status": "budget_blocked",
                        "budget_block_reasons": accounting["budget_block_reasons"],
                        "actual_cny": accounting["actual_cny"],
                    })
                    return "budget_blocked"
                if accounting["active_reservation"] is not None:
                    recorder.event("trial_completed", {
                        "task_id": task.id, "repetition_id": str(repetition),
                        "attempt_id": 1, "status": "infrastructure_error",
                        "budget_reconciliation_required": True,
                    })
                    return "infrastructure_error"
                if accounting["request_count"] != requests:
                    recorder.event("trial_completed", {
                        "task_id": task.id, "repetition_id": str(repetition),
                        "attempt_id": 1, "status": "infrastructure_error",
                        "budget_reconciliation_required": True,
                        "error_category": "ledger_trace_request_count_mismatch",
                    })
                    return "infrastructure_error"
                status = (
                    "success" if process.returncode == 0 and passed
                    else "task_failure" if process.returncode == 0
                    else "infrastructure_error"
                )
                statuses.append(status)
                recorder.event("trial_completed", {
                    "task_id": task.id, "repetition_id": str(repetition),
                    "attempt_id": 1, "status": status,
                    "duration_seconds": time.monotonic() - started,
                    "actual_cny": accounting["actual_cny"], "grade": grade,
                    "provider_request_count": accounting["request_count"],
                    "input_tokens": input_tokens, "output_tokens": output_tokens,
                    **success_rate_fields(status),
                })
    if all(item == "success" for item in statuses):
        return "success"
    return "infrastructure_error" if "infrastructure_error" in statuses else "task_failure"


def execute(
    *, root: Path, studies_path: Path, runs_dir: Path, run_prefix: str,
    pricing_snapshot: Path, budget_authorization: Path, budget_ledger: Path,
    budget_allocation: Path | None = None,
    confirmed: bool, budget_stage: Literal["A", "B", "C"] = "C",
    scope: Literal["pilot", "formal"] = "formal",
) -> list[RunRecorder]:
    studies = load_studies(studies_path)
    if scope == "formal":
        preflight = grader_preflight(studies_path=studies_path)
        if preflight["status"] != "GO":
            raise ValueError("Multi-Agent formal grader preflight is NO-GO")
    pilot = load_pilot_config(root / "evals" / "pilot.qwen.yaml")
    if not confirmed or not os.environ.get(pilot.api_key_env):
        raise ValueError("execute requires --confirm-paid-run and the configured API key")
    pricing = load_pricing(pricing_snapshot)
    gate = PaidRunGate(
        root=root, authorization_path=budget_authorization,
        ledger_path=budget_ledger, pricing=pricing, stage=budget_stage,
        allocation_path=budget_allocation,
        pricing_path=pricing_snapshot,
    )
    recorders: list[RunRecorder] = []
    for profile in profiles(studies):
        manifest = build_manifest(
            root=root, studies_path=studies_path, studies=studies,
            profile=profile, scope=scope,
        )
        manifest.pricing_snapshot_hash = pricing_snapshot_hash(pricing)
        recorder = RunRecorder(
            runs_dir, manifest,
            run_id=f"{run_prefix}-{profile.agent_mode.value}",
            repo_root=root, secrets=_runtime_secrets(pilot),
        )
        status = _run_profile(
            root=root, studies=studies, profile=profile,
            recorder=recorder, gate=gate, scope=scope,
        )
        recorder.finalize({
            "status": status, "execution_mode": "live",
            "agent_mode": profile.agent_mode.value,
            "maximum_workers": studies.multi_agent.maximum_workers,
        })
        recorders.append(recorder)
        if status in {"timeout", "infrastructure_error", "budget_blocked"}:
            break
    return recorders


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Goal 2 single-vs-multi Agent study")
    parser.add_argument("command", choices=["validate", "dry-run", "grader-preflight", "execute"])
    parser.add_argument("--studies", type=Path, default=Path("evals/goal2/studies.yaml"))
    parser.add_argument("--runs-dir", type=Path, default=Path("evals/.runs/goal2-multi"))
    parser.add_argument("--run-prefix", default="multi-dry")
    parser.add_argument("--pricing-snapshot", type=Path)
    parser.add_argument("--budget-authorization", type=Path)
    parser.add_argument("--budget-ledger", type=Path)
    parser.add_argument("--budget-allocation", type=Path)
    parser.add_argument("--budget-stage", choices=["A", "B", "C"])
    parser.add_argument("--scope", choices=["pilot", "formal"])
    parser.add_argument("--confirm-paid-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        root = Path.cwd()
        studies = load_studies(args.studies)
        scope = args.scope or "formal"
        selected_tasks, repetitions = scoped_tasks(studies, scope=scope)
        payload: dict[str, Any] = {
            "valid": True, "scope": scope, "task_count": len(selected_tasks),
            "top_level_trial_count": (
                len(selected_tasks) * repetitions
                * len(studies.multi_agent.modes)
            ),
            "maximum_workers": studies.multi_agent.maximum_workers,
            "asset_hash": asset_hash(args.studies),
        }
        if args.command == "grader-preflight":
            payload["grader_preflight"] = grader_preflight(studies_path=args.studies)
        elif args.command == "dry-run":
            payload["run_paths"] = [str(item.path) for item in dry_run(
                root=root, studies_path=args.studies, runs_dir=args.runs_dir,
                run_prefix=args.run_prefix, scope=scope,
            )]
        elif args.command == "execute":
            required = [args.pricing_snapshot, args.budget_authorization, args.budget_ledger, args.budget_stage, args.scope]
            if any(item is None for item in required):
                raise ValueError("execute requires pricing, budget authorization, and ledger paths")
            payload["run_paths"] = [str(item.path) for item in execute(
                root=root, studies_path=args.studies, runs_dir=args.runs_dir,
                run_prefix=args.run_prefix, pricing_snapshot=args.pricing_snapshot,
                budget_authorization=args.budget_authorization,
                budget_ledger=args.budget_ledger, confirmed=args.confirm_paid_run,
                budget_allocation=args.budget_allocation,
                budget_stage=args.budget_stage,
                scope=args.scope,
            )]
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"multi-agent study error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

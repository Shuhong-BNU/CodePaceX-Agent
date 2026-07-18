"""Validation and frozen design helpers for the controlled 50-tool MCP study."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from codepacex.experiments import ExperimentProfile, ToolLoading
from codepacex.config import load_config as load_codepacex_config
from evals.benchmark import (
    RunManifest,
    RunRecorder,
    canonical_hash,
    current_git_commit,
    sanitize_origin,
)
from evals.costing import load_pricing, pricing_snapshot_hash
from evals.paid_gate import PaidRunGate, provider_request_budget_environment
from evals.pilot import (
    _child_environment,
    _ingest_trace,
    _provider_payload,
    _runtime_secrets,
    load_config as load_pilot_config,
    trace_request_usages,
)

MAXIMUM_REQUESTS_PER_TRIAL = 50
MAXIMUM_INPUT_TOKENS_PER_REQUEST = 128_000
MAXIMUM_OUTPUT_TOKENS_PER_REQUEST = 8192


class MCPTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    category: Literal["no_mcp", "one_mcp", "multi_mcp"]
    prompt: str
    expected_tools: list[str]
    expected_answer: str

    @model_validator(mode="after")
    def validate_tool_count(self) -> MCPTask:
        expected = {"no_mcp": (0, 0), "one_mcp": (1, 1), "multi_mcp": (2, 3)}
        low, high = expected[self.category]
        if not low <= len(self.expected_tools) <= high:
            raise ValueError(f"{self.category} task has an invalid MCP tool count")
        if any(not name.startswith("mcp_fixture_tool_") for name in self.expected_tools):
            raise ValueError("task references a tool outside the controlled fixture")
        return self


class MCPTaskManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    tasks: list[MCPTask]

    @model_validator(mode="after")
    def validate_matrix(self) -> MCPTaskManifest:
        if len(self.tasks) != 30 or len({task.id for task in self.tasks}) != 30:
            raise ValueError("MCP study requires exactly 30 independent task IDs")
        counts = Counter(task.category for task in self.tasks)
        if counts != {"no_mcp": 10, "one_mcp": 10, "multi_mcp": 10}:
            raise ValueError("MCP study requires 10 tasks in each category")
        return self


class MCPStudyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    study_id: Literal["goal2-mcp-tool-loading"]
    task_manifest: Path
    fixture_server: Path
    repetitions: Literal[5] = 5
    timeout_seconds: Literal[420] = 420
    arms: list[ToolLoading]
    controlled_corpus_only: Literal[True] = True

    @model_validator(mode="after")
    def validate_arms(self) -> MCPStudyConfig:
        if self.arms != [ToolLoading.EAGER, ToolLoading.DEFERRED]:
            raise ValueError("MCP study arms must be ordered eager, deferred")
        return self


def _load_mapping(path: Path) -> dict:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected YAML mapping: {path}")
    return raw


def load_study(path: Path) -> tuple[MCPStudyConfig, MCPTaskManifest]:
    study = MCPStudyConfig.model_validate(_load_mapping(path))
    tasks = MCPTaskManifest.model_validate(_load_mapping(study.task_manifest))
    if not study.fixture_server.is_file():
        raise ValueError("MCP fixture server does not exist")
    return study, tasks


def profiles(study: MCPStudyConfig) -> list[ExperimentProfile]:
    return [ExperimentProfile(
        tool_loading=arm,
        compression_profile="recovery_v1",
        permission_strategy="default",
        agent_mode="single",
    ) for arm in study.arms]


def fixture_permission_rules(tasks: MCPTaskManifest) -> list[dict[str, str]]:
    """Return the only MCP commands preauthorized for this controlled study.

    MCP wrappers are command-category tools.  The study deliberately uses the
    normal permission strategy, so the fixture calls need explicit rules in
    non-interactive runs.  Keep the allow-list tied to the frozen manifest,
    rather than granting a wildcard for MCP tools or commands.
    """
    return [
        {"rule": f"{tool_name}(*)", "effect": "allow"}
        for tool_name in sorted({
            tool_name for task in tasks.tasks for tool_name in task.expected_tools
        })
    ]


def _write_fixture_permissions(*, workspace: Path, tasks: MCPTaskManifest) -> None:
    """Install manifest-scoped MCP permissions in the isolated trial workspace."""
    permissions_path = workspace / ".codepacex" / "permissions.yaml"
    permissions_path.parent.mkdir(parents=True, exist_ok=True)
    permissions_path.write_text(
        yaml.safe_dump(fixture_permission_rules(tasks), sort_keys=True),
        encoding="utf-8",
    )


def top_level_trial_count(
    study: MCPStudyConfig, tasks: MCPTaskManifest,
) -> int:
    return len(study.arms) * study.repetitions * len(tasks.tasks)


def scoped_tasks(
    study: MCPStudyConfig, tasks: MCPTaskManifest, *,
    scope: Literal["pilot", "formal"],
) -> tuple[list[MCPTask], int]:
    if scope == "formal":
        return list(tasks.tasks), study.repetitions
    selected = [
        next(task for task in tasks.tasks if task.category == category)
        for category in ("no_mcp", "one_mcp", "multi_mcp")
    ]
    return selected, 1


def study_asset_hash(
    study_path: Path, study: MCPStudyConfig,
) -> str:
    files = {
        "study": study_path,
        "tasks": study.task_manifest,
        "fixture_server": study.fixture_server,
    }
    return canonical_hash({
        label: hashlib.sha256(path.read_bytes()).hexdigest()
        for label, path in files.items()
    })


def _git_dirty(root: Path) -> bool | None:
    result = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        text=True, capture_output=True, check=False,
    )
    return bool(result.stdout) if result.returncode == 0 else None


def build_arm_manifest(
    *, root: Path, study_path: Path, study: MCPStudyConfig,
    tasks: MCPTaskManifest, profile: ExperimentProfile,
    scope: Literal["pilot", "formal"] = "formal",
) -> RunManifest:
    pilot = load_pilot_config(root / "evals" / "pilot.qwen.yaml")
    selected_tasks, repetitions = scoped_tasks(study, tasks, scope=scope)
    return RunManifest(
        experiment_kind=study.study_id,
        provider=pilot.provider,
        protocol=pilot.protocol,
        base_url_origin=sanitize_origin(pilot.base_url),
        api_key_env=pilot.api_key_env,
        model_id=pilot.model_id,
        git_commit=current_git_commit(root),
        dirty_worktree=_git_dirty(root),
        prompt_version="codepacex-system-v1",
        feature_flags={},
        experiment_profile=profile.canonical_payload(),
        experiment_profile_hash=profile.profile_hash(),
        runtime_contract_hash=profile.runtime_contract_hash(),
        benchmark_asset_hash=study_asset_hash(study_path, study),
        task_ids=[task.id for task in selected_tasks],
        repetitions=repetitions,
        model_parameters=pilot.model_parameters.model_dump(mode="json"),
        max_output_tokens=pilot.model_parameters.max_output_tokens,
        retry_budget=0,
        fallback_enabled=False,
        max_iterations=50,
        experiment_config_hash=canonical_hash({
            "study": study.model_dump(mode="json"),
            "profile": profile.canonical_payload(),
            "scope": scope,
        }),
    )


def dry_run(
    *, root: Path, study_path: Path, runs_dir: Path, run_prefix: str,
    scope: Literal["pilot", "formal"] = "formal",
) -> list[RunRecorder]:
    study, tasks = load_study(study_path)
    selected_tasks, repetitions = scoped_tasks(study, tasks, scope=scope)
    recorders: list[RunRecorder] = []
    for profile in profiles(study):
        recorder = RunRecorder(
            runs_dir,
            build_arm_manifest(
                root=root, study_path=study_path, study=study,
                tasks=tasks, profile=profile, scope=scope,
            ),
            run_id=f"{run_prefix}-{profile.tool_loading.value}",
            repo_root=root,
        )
        recorder.event("dry_run", {
            "network_called": False,
            "model_called": False,
            "arm": profile.tool_loading.value,
            "planned_trial_count": len(selected_tasks) * repetitions,
            "scope": scope,
        })
        recorder.finalize({
            "status": "dry_run", "execution_mode": "dry_run", "scorable": False,
        })
        recorders.append(recorder)
    return recorders


def grade_trace(task: MCPTask, trace_text: str) -> tuple[bool, dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in trace_text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    fixture_prefix = "mcp_fixture_tool_"
    mcp_uses = [
        event for event in events
        if event.get("type") == "tool_use"
        and str(event.get("tool_name", "")).startswith(fixture_prefix)
    ]
    observed_mcp = [str(event.get("tool_name")) for event in mcp_uses]
    use_ids = [event.get("tool_id") for event in mcp_uses]
    valid_use_ids = all(isinstance(tool_id, str) and tool_id for tool_id in use_ids)
    use_by_id = {
        str(event["tool_id"]): str(event["tool_name"])
        for event in mcp_uses
        if isinstance(event.get("tool_id"), str) and event.get("tool_id")
    }
    fixture_results = [
        event for event in events
        if event.get("type") == "tool_result"
        and str(event.get("tool_name", "")).startswith(fixture_prefix)
    ]
    result_ids = [event.get("tool_id") for event in fixture_results]
    valid_result_ids = all(
        isinstance(tool_id, str) and tool_id for tool_id in result_ids
    )
    result_names_match = all(
        valid_result_ids
        and str(event.get("tool_name")) == use_by_id.get(str(event.get("tool_id")))
        for event in fixture_results
    )
    successful_results = all(event.get("is_error") is False for event in fixture_results)
    results = [event for event in events if event.get("type") == "result"]
    answer = str(results[-1].get("result", "")) if results else ""
    required_answers = task.expected_answer.split("|")
    # MCP tool order is not part of the study contract, but every expected call
    # must occur exactly once per declared occurrence.
    tools_match = Counter(observed_mcp) == Counter(task.expected_tools)
    execution_match = (
        valid_use_ids
        and len(use_by_id) == len(mcp_uses)
        and valid_result_ids
        and len(set(str(tool_id) for tool_id in result_ids)) == len(fixture_results)
        and Counter(str(event.get("tool_id")) for event in fixture_results)
        == Counter(str(tool_id) for tool_id in use_ids)
        and result_names_match
        and successful_results
    )
    answer_match = (
        answer.strip() == task.expected_answer
        if task.category == "no_mcp"
        else all(value in answer for value in required_answers)
    )
    passed = len(results) == 1 and tools_match and execution_match and answer_match
    return passed, {
        "expected_tools": task.expected_tools,
        "observed_mcp_tools": observed_mcp,
        "tools_match": tools_match,
        "execution_match": execution_match,
        "successful_mcp_tools": [
            str(event.get("tool_name")) for event in fixture_results
            if event.get("is_error") is False
        ],
        "answer_match": answer_match,
        "result_event_count": len(results),
    }


def _write_child_config(
    *, pilot: Any, study: MCPStudyConfig, root: Path, home: Path,
) -> None:
    payload = _provider_payload(pilot)
    payload["mcp_servers"] = [{
        "name": "fixture",
        "command": sys.executable,
        "args": [str((root / study.fixture_server).resolve())],
    }]
    config_dir = home / ".codepacex"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    loaded = load_codepacex_config(config_path)
    if len(loaded.mcp_servers) != 1 or loaded.mcp_servers[0].name != "fixture":
        raise ValueError("generated MCP child configuration failed validation")


def _run_arm(
    *, root: Path, study: MCPStudyConfig, tasks: MCPTaskManifest,
    profile: ExperimentProfile, recorder: RunRecorder, gate: PaidRunGate,
    scope: Literal["pilot", "formal"],
) -> str:
    pilot = load_pilot_config(root / "evals" / "pilot.qwen.yaml")
    statuses: list[str] = []
    with tempfile.TemporaryDirectory(prefix="codepacex-mcp-home-") as home_text:
        home = Path(home_text)
        _write_child_config(pilot=pilot, study=study, root=root, home=home)
        profile_path = home / "experiment-profile.yaml"
        profile_path.write_text(
            yaml.safe_dump(profile.canonical_payload(), sort_keys=True),
            encoding="utf-8",
        )
        environment = _child_environment(pilot, str(home), root=root)
        selected_tasks, repetitions = scoped_tasks(study, tasks, scope=scope)
        for repetition in range(1, repetitions + 1):
            for task in selected_tasks:
                attempt_id = 1
                trial_id = (
                    f"mcp/{recorder.run_id}/{profile.tool_loading.value}/"
                    f"{task.id}/{repetition}"
                )
                recorder.event("trial_started", {
                    "task_id": task.id, "repetition_id": str(repetition),
                    "attempt_id": attempt_id,
                    "budget_mode": "per_provider_request",
                })
                with tempfile.TemporaryDirectory(
                    prefix=f"codepacex-{task.id}-"
                ) as workspace_text:
                    _write_fixture_permissions(
                        workspace=Path(workspace_text), tasks=tasks,
                    )
                    command = [
                        sys.executable, "-m", "codepacex", "-p", task.prompt,
                        "--output-format", "stream-json",
                        "--experiment-profile", str(profile_path),
                    ]
                    request_environment = dict(environment)
                    request_environment.update(provider_request_budget_environment(
                        gate, trial_id=trial_id,
                        maximum_input_tokens_per_request=MAXIMUM_INPUT_TOKENS_PER_REQUEST,
                        maximum_output_tokens_per_request=MAXIMUM_OUTPUT_TOKENS_PER_REQUEST,
                    ))
                    try:
                        process = subprocess.run(
                            command, cwd=workspace_text, env=request_environment,
                            text=True, capture_output=True,
                            timeout=study.timeout_seconds, check=False,
                        )
                        trace_path = Path(workspace_text) / "trace.ndjson"
                        trace_path.write_text(process.stdout or "", encoding="utf-8")
                        usage = _ingest_trace(
                            recorder, trace_path, task.id, str(repetition), attempt_id,
                        )
                        requests, input_tokens, output_tokens = usage
                        request_usages = trace_request_usages(process.stdout or "")
                        passed, grade = grade_trace(task, process.stdout or "")
                        status = (
                            "success" if process.returncode == 0 and passed
                            else "task_failure" if process.returncode == 0
                            else "infrastructure_error"
                        )
                    except subprocess.TimeoutExpired:
                        status, grade = "timeout", {
                            "expected_tools": task.expected_tools,
                            "observed_mcp_tools": [], "tools_match": False,
                            "answer_match": False,
                            "result_event_count": 0,
                        }
                        recorder.event("trial_completed", {
                            "task_id": task.id, "repetition_id": str(repetition),
                            "attempt_id": attempt_id, "status": status,
                            "budget_reconciliation_required": True, "grade": grade,
                        })
                        return status
                    except (OSError, ValueError, json.JSONDecodeError):
                        status, grade = "infrastructure_error", {
                            "expected_tools": task.expected_tools,
                            "observed_mcp_tools": [], "tools_match": False,
                            "answer_match": False,
                            "result_event_count": 0,
                        }
                        recorder.event("trial_completed", {
                            "task_id": task.id, "repetition_id": str(repetition),
                            "attempt_id": attempt_id, "status": status,
                            "budget_reconciliation_required": True, "grade": grade,
                        })
                        return status
                accounting = gate.trial_accounting(trial_id)
                if accounting["budget_blocked"]:
                    recorder.event("trial_completed", {
                        "task_id": task.id, "repetition_id": str(repetition),
                        "attempt_id": attempt_id, "status": "budget_blocked",
                        "budget_block_reasons": accounting["budget_block_reasons"],
                        "actual_cny": accounting["actual_cny"],
                    })
                    return "budget_blocked"
                if accounting["active_reservation"] is not None:
                    recorder.event("trial_completed", {
                        "task_id": task.id, "repetition_id": str(repetition),
                        "attempt_id": attempt_id, "status": "infrastructure_error",
                        "budget_reconciliation_required": True,
                    })
                    return "infrastructure_error"
                if requests == 0 or accounting["request_count"] != requests:
                    recorder.event("trial_completed", {
                        "task_id": task.id, "repetition_id": str(repetition),
                        "attempt_id": attempt_id, "status": "infrastructure_error",
                        "budget_reconciliation_required": True, "grade": grade,
                    })
                    return "infrastructure_error"
                statuses.append(status)
                recorder.event("trial_completed", {
                    "task_id": task.id, "repetition_id": str(repetition),
                    "attempt_id": attempt_id, "status": status,
                    "numerator": int(status == "success"), "denominator": 1,
                    "grade": grade,
                    "actual_cny": accounting["actual_cny"],
                    "provider_request_count": accounting["request_count"],
                })
    if all(status == "success" for status in statuses):
        return "success"
    for failure in ("infrastructure_error", "timeout", "task_failure"):
        if failure in statuses:
            return failure
    return "infrastructure_error"


def execute(
    *, root: Path, study_path: Path, runs_dir: Path, run_prefix: str,
    pricing_snapshot: Path, confirmed: bool,
    budget_authorization: Path | None = None,
    budget_ledger: Path | None = None,
    budget_allocation: Path | None = None,
    budget_stage: Literal["A", "B", "C"] = "C",
    scope: Literal["pilot", "formal"] = "formal",
) -> list[RunRecorder]:
    study, tasks = load_study(study_path)
    pilot = load_pilot_config(root / "evals" / "pilot.qwen.yaml")
    if not confirmed or not os.environ.get(pilot.api_key_env):
        raise ValueError("execute requires --confirm-paid-run and the configured API key")
    if budget_authorization is None or budget_ledger is None:
        raise ValueError("execute requires budget authorization and ledger paths")
    pricing = load_pricing(pricing_snapshot)
    pricing_hash = pricing_snapshot_hash(pricing)
    gate = PaidRunGate(
        root=root, authorization_path=budget_authorization,
        ledger_path=budget_ledger, pricing=pricing, stage=budget_stage,
        allocation_path=budget_allocation,
        pricing_path=pricing_snapshot,
    )
    recorders: list[RunRecorder] = []
    for profile in profiles(study):
        manifest = build_arm_manifest(
            root=root, study_path=study_path, study=study,
            tasks=tasks, profile=profile, scope=scope,
        )
        manifest.pricing_snapshot_hash = pricing_hash
        recorder = RunRecorder(
            runs_dir, manifest,
            run_id=f"{run_prefix}-{profile.tool_loading.value}",
            repo_root=root, secrets=_runtime_secrets(pilot),
        )
        status = _run_arm(
            root=root, study=study, tasks=tasks,
            profile=profile, recorder=recorder, gate=gate, scope=scope,
        )
        recorder.finalize({
            "status": status, "execution_mode": "live",
            "arm": profile.tool_loading.value,
            "controlled_corpus_only": True,
        })
        recorders.append(recorder)
        if status in {"timeout", "infrastructure_error", "budget_blocked"}:
            break
    return recorders


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Controlled Goal 2 MCP study")
    parser.add_argument("command", choices=["validate", "dry-run", "execute"])
    parser.add_argument("--study", type=Path, default=Path("evals/goal2/mcp_study.yaml"))
    parser.add_argument("--runs-dir", type=Path, default=Path("evals/.runs/goal2-mcp"))
    parser.add_argument("--run-prefix", default="mcp-dry")
    parser.add_argument("--pricing-snapshot", type=Path)
    parser.add_argument("--confirm-paid-run", action="store_true")
    parser.add_argument("--budget-authorization", type=Path)
    parser.add_argument("--budget-ledger", type=Path)
    parser.add_argument("--budget-allocation", type=Path)
    parser.add_argument("--budget-stage", choices=["A", "B", "C"])
    parser.add_argument("--scope", choices=["pilot", "formal"])
    args = parser.parse_args(argv)
    try:
        root = Path.cwd()
        study, tasks = load_study(args.study)
        scope = args.scope or "formal"
        selected_tasks, repetitions = scoped_tasks(study, tasks, scope=scope)
        payload = {
            "valid": True,
            "study_id": study.study_id,
            "scope": scope,
            "task_count": len(selected_tasks),
            "top_level_trial_count": len(study.arms) * repetitions * len(selected_tasks),
            "asset_hash": study_asset_hash(args.study, study),
            "controlled_corpus_only": study.controlled_corpus_only,
        }
        if args.command == "dry-run":
            payload["run_paths"] = [str(recorder.path) for recorder in dry_run(
                root=root, study_path=args.study, runs_dir=args.runs_dir,
                run_prefix=args.run_prefix, scope=scope,
            )]
        elif args.command == "execute":
            if any(item is None for item in [
                args.pricing_snapshot, args.budget_authorization,
                args.budget_ledger, args.budget_stage, args.scope,
            ]):
                raise ValueError("execute requires pricing, budget authorization, ledger, and stage")
            payload["run_paths"] = [str(recorder.path) for recorder in execute(
                root=root, study_path=args.study, runs_dir=args.runs_dir,
                run_prefix=args.run_prefix,
                pricing_snapshot=args.pricing_snapshot,
                confirmed=args.confirm_paid_run,
                budget_authorization=args.budget_authorization,
                budget_ledger=args.budget_ledger,
                budget_allocation=args.budget_allocation,
                budget_stage=args.budget_stage,
                scope=args.scope,
            )]
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"MCP study error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

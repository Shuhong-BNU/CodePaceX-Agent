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
from evals.pilot import (
    _child_environment,
    _ingest_trace,
    _provider_payload,
    _runtime_secrets,
    load_config as load_pilot_config,
)


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
        if len(self.expected_tools) != len(set(self.expected_tools)):
            raise ValueError("task repeats an expected MCP tool")
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


def top_level_trial_count(
    study: MCPStudyConfig, tasks: MCPTaskManifest,
) -> int:
    return len(study.arms) * study.repetitions * len(tasks.tasks)


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
) -> RunManifest:
    pilot = load_pilot_config(root / "evals" / "pilot.qwen.yaml")
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
        task_ids=[task.id for task in tasks.tasks],
        repetitions=study.repetitions,
        model_parameters=pilot.model_parameters.model_dump(mode="json"),
        max_output_tokens=pilot.model_parameters.max_output_tokens,
        retry_budget=0,
        fallback_enabled=False,
        max_iterations=50,
        experiment_config_hash=canonical_hash({
            "study": study.model_dump(mode="json"),
            "profile": profile.canonical_payload(),
        }),
    )


def dry_run(
    *, root: Path, study_path: Path, runs_dir: Path, run_prefix: str,
) -> list[RunRecorder]:
    study, tasks = load_study(study_path)
    recorders: list[RunRecorder] = []
    for profile in profiles(study):
        recorder = RunRecorder(
            runs_dir,
            build_arm_manifest(
                root=root, study_path=study_path, study=study,
                tasks=tasks, profile=profile,
            ),
            run_id=f"{run_prefix}-{profile.tool_loading.value}",
            repo_root=root,
        )
        recorder.event("dry_run", {
            "network_called": False,
            "model_called": False,
            "arm": profile.tool_loading.value,
            "planned_trial_count": len(tasks.tasks) * study.repetitions,
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
    observed_mcp = [
        str(event.get("tool_name")) for event in events
        if event.get("type") == "tool_use"
        and str(event.get("tool_name", "")).startswith("mcp_fixture_tool_")
    ]
    results = [event for event in events if event.get("type") == "result"]
    answer = str(results[-1].get("result", "")) if results else ""
    required_answers = task.expected_answer.split("|")
    tools_match = set(observed_mcp) == set(task.expected_tools)
    answer_match = (
        answer.strip() == task.expected_answer
        if task.category == "no_mcp"
        else all(value in answer for value in required_answers)
    )
    passed = len(results) == 1 and tools_match and answer_match
    return passed, {
        "expected_tools": task.expected_tools,
        "observed_mcp_tools": observed_mcp,
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
    profile: ExperimentProfile, recorder: RunRecorder,
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
        environment = _child_environment(pilot, str(home))
        for repetition in range(1, study.repetitions + 1):
            for task in tasks.tasks:
                attempt_id = 1
                recorder.event("trial_started", {
                    "task_id": task.id, "repetition_id": str(repetition),
                    "attempt_id": attempt_id,
                })
                with tempfile.TemporaryDirectory(
                    prefix=f"codepacex-{task.id}-"
                ) as workspace_text:
                    command = [
                        sys.executable, "-m", "codepacex", "-p", task.prompt,
                        "--output-format", "stream-json",
                        "--experiment-profile", str(profile_path),
                    ]
                    try:
                        process = subprocess.run(
                            command, cwd=workspace_text, env=environment,
                            text=True, capture_output=True,
                            timeout=study.timeout_seconds, check=False,
                        )
                        trace_path = Path(workspace_text) / "trace.ndjson"
                        trace_path.write_text(process.stdout or "", encoding="utf-8")
                        _ingest_trace(
                            recorder, trace_path, task.id, str(repetition), attempt_id,
                        )
                        passed, grade = grade_trace(task, process.stdout or "")
                        status = (
                            "success" if process.returncode == 0 and passed
                            else "task_failure" if process.returncode == 0
                            else "infrastructure_error"
                        )
                    except subprocess.TimeoutExpired:
                        status, grade = "timeout", {
                            "expected_tools": task.expected_tools,
                            "observed_mcp_tools": [], "answer_match": False,
                            "result_event_count": 0,
                        }
                    except (OSError, ValueError, json.JSONDecodeError):
                        status, grade = "infrastructure_error", {
                            "expected_tools": task.expected_tools,
                            "observed_mcp_tools": [], "answer_match": False,
                            "result_event_count": 0,
                        }
                statuses.append(status)
                recorder.event("trial_completed", {
                    "task_id": task.id, "repetition_id": str(repetition),
                    "attempt_id": attempt_id, "status": status,
                    "numerator": int(status == "success"), "denominator": 1,
                    "grade": grade,
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
) -> list[RunRecorder]:
    study, tasks = load_study(study_path)
    pilot = load_pilot_config(root / "evals" / "pilot.qwen.yaml")
    if not confirmed or not os.environ.get(pilot.api_key_env):
        raise ValueError("execute requires --confirm-paid-run and the configured API key")
    if _git_dirty(root) is not False:
        raise ValueError("paid MCP study requires a clean frozen Git worktree")
    pricing_hash = hashlib.sha256(pricing_snapshot.read_bytes()).hexdigest()
    recorders: list[RunRecorder] = []
    for profile in profiles(study):
        manifest = build_arm_manifest(
            root=root, study_path=study_path, study=study,
            tasks=tasks, profile=profile,
        )
        manifest.pricing_snapshot_hash = pricing_hash
        recorder = RunRecorder(
            runs_dir, manifest,
            run_id=f"{run_prefix}-{profile.tool_loading.value}",
            repo_root=root, secrets=_runtime_secrets(pilot),
        )
        status = _run_arm(
            root=root, study=study, tasks=tasks,
            profile=profile, recorder=recorder,
        )
        recorder.finalize({
            "status": status, "execution_mode": "live",
            "arm": profile.tool_loading.value,
            "controlled_corpus_only": True,
        })
        recorders.append(recorder)
    return recorders


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Controlled Goal 2 MCP study")
    parser.add_argument("command", choices=["validate", "dry-run", "execute"])
    parser.add_argument("--study", type=Path, default=Path("evals/goal2/mcp_study.yaml"))
    parser.add_argument("--runs-dir", type=Path, default=Path("evals/.runs/goal2-mcp"))
    parser.add_argument("--run-prefix", default="mcp-dry")
    parser.add_argument("--pricing-snapshot", type=Path)
    parser.add_argument("--confirm-paid-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        root = Path.cwd()
        study, tasks = load_study(args.study)
        payload = {
            "valid": True,
            "study_id": study.study_id,
            "task_count": len(tasks.tasks),
            "top_level_trial_count": top_level_trial_count(study, tasks),
            "asset_hash": study_asset_hash(args.study, study),
            "controlled_corpus_only": study.controlled_corpus_only,
        }
        if args.command == "dry-run":
            payload["run_paths"] = [str(recorder.path) for recorder in dry_run(
                root=root, study_path=args.study, runs_dir=args.runs_dir,
                run_prefix=args.run_prefix,
            )]
        elif args.command == "execute":
            if args.pricing_snapshot is None:
                raise ValueError("execute requires --pricing-snapshot")
            payload["run_paths"] = [str(recorder.path) for recorder in execute(
                root=root, study_path=args.study, runs_dir=args.runs_dir,
                run_prefix=args.run_prefix,
                pricing_snapshot=args.pricing_snapshot,
                confirmed=args.confirm_paid_run,
            )]
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"MCP study error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

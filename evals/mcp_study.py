"""Validation and frozen design helpers for the controlled 50-tool MCP study."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from codepacex.experiments import ExperimentProfile, ToolLoading
from evals.benchmark import (
    RunManifest,
    RunRecorder,
    canonical_hash,
    current_git_commit,
    sanitize_origin,
)
from evals.pilot import load_config as load_pilot_config


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Controlled Goal 2 MCP study")
    parser.add_argument("command", choices=["validate", "dry-run"])
    parser.add_argument("--study", type=Path, default=Path("evals/goal2/mcp_study.yaml"))
    parser.add_argument("--runs-dir", type=Path, default=Path("evals/.runs/goal2-mcp"))
    parser.add_argument("--run-prefix", default="mcp-dry")
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
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"MCP study error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

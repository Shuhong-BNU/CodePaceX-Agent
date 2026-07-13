"""Fail-closed validation for the frozen Goal 2 study matrix."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RetentionStudy(StrictModel):
    session_seeds: list[str]
    canaries_per_session: Literal[12]
    profiles: list[Literal["summary_only", "recovery_v1"]]
    minimum_real_compactions: int = Field(ge=3)
    context_window: Literal[32768]

    @model_validator(mode="after")
    def validate_matrix(self) -> RetentionStudy:
        if len(self.session_seeds) != 10 or len(set(self.session_seeds)) != 10:
            raise ValueError("retention study requires 10 unique session seeds")
        if self.profiles != ["summary_only", "recovery_v1"]:
            raise ValueError("retention profiles must be ordered summary_only, recovery_v1")
        return self


class PermissionTask(StrictModel):
    id: str
    tool: str
    dangerous: bool
    description: str


class PermissionStudy(StrictModel):
    repetitions: Literal[5]
    strategies: list[Literal[
        "default", "session_allow", "explicit_rules", "sandbox_auto_allow"
    ]]
    tasks: list[PermissionTask]

    @model_validator(mode="after")
    def validate_matrix(self) -> PermissionStudy:
        if len(self.tasks) != 10 or len({task.id for task in self.tasks}) != 10:
            raise ValueError("permission study requires 10 unique tasks")
        if self.strategies != [
            "default", "session_allow", "explicit_rules", "sandbox_auto_allow"
        ]:
            raise ValueError("permission strategies have changed")
        return self


class MultiTask(StrictModel):
    id: str
    prompt: str
    expected_files: list[str] = Field(min_length=2)


class MultiAgentStudy(StrictModel):
    repetitions: Literal[5]
    modes: list[Literal["single", "multi"]]
    maximum_workers: Literal[3]
    tasks: list[MultiTask]

    @model_validator(mode="after")
    def validate_matrix(self) -> MultiAgentStudy:
        if self.modes != ["single", "multi"]:
            raise ValueError("multi-agent modes must be ordered single, multi")
        if len(self.tasks) != 5 or len({task.id for task in self.tasks}) != 5:
            raise ValueError("multi-agent study requires 5 unique cross-file tasks")
        return self


class HookStudy(StrictModel):
    paths: list[Literal["sequential", "parallel", "streaming", "no_checker"]]
    cases_per_path: Literal[25]
    paid_runs: Literal[0]


class LongPilot(StrictModel):
    count: Literal[1]
    duration_hours: Literal[2]
    planned_restart_minutes: Literal[60]


class LongFormal(StrictModel):
    count: Literal[3]
    duration_hours: Literal[8]
    planned_restart_hours: Literal[4]


class LongSessionStudy(StrictModel):
    checkpoint_interval_minutes: Literal[30]
    workload_interval_minutes: Literal[15]
    maximum_provider_requests_per_cycle: Literal[10]
    maximum_concurrent_paid_sessions: Literal[1]
    pilot: LongPilot
    formal: LongFormal

    def workload_cycle_count(self) -> int:
        hours = (
            self.pilot.count * self.pilot.duration_hours
            + self.formal.count * self.formal.duration_hours
        )
        return hours * 60 // self.workload_interval_minutes


class SWEBenchStudy(StrictModel):
    repository: Literal["https://github.com/microsoft/SWE-bench-Live"]
    branch: Literal["python-only"]
    dataset: Literal["SWE-bench-Live/SWE-bench-Live"]
    split: Literal["lite"]
    pilot_instances: Literal[3]
    formal_instances: Literal[20]
    repeated_instances: Literal[5]
    repeat_extra_runs: Literal[2]
    maximum_instances_per_repo: Literal[2]
    size_buckets: dict[str, int]
    repeated_size_buckets: dict[str, int]

    @model_validator(mode="after")
    def validate_distribution(self) -> SWEBenchStudy:
        if self.size_buckets != {
            "one_file": 8, "two_to_four_files": 8, "five_plus_files": 4
        }:
            raise ValueError("formal SWE-bench size distribution has changed")
        if self.repeated_size_buckets != {
            "one_file": 2, "two_to_four_files": 2, "five_plus_files": 1
        }:
            raise ValueError("repeated SWE-bench size distribution has changed")
        return self


class Goal2Studies(StrictModel):
    schema_version: Literal[1]
    retention: RetentionStudy
    permission: PermissionStudy
    multi_agent: MultiAgentStudy
    hook: HookStudy
    long_session: LongSessionStudy
    swe_bench: SWEBenchStudy


def load_studies(path: Path) -> Goal2Studies:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Goal 2 studies file must be a mapping")
    return Goal2Studies.model_validate(raw)


def retention_canaries(study: RetentionStudy, session_index: int) -> list[str]:
    seed = bytes.fromhex(study.session_seeds[session_index])
    return [
        "CNY-" + hmac.new(seed, str(index).encode(), hashlib.sha256).hexdigest()[:24]
        for index in range(study.canaries_per_session)
    ]


def planned_paid_runs(studies: Goal2Studies) -> dict[str, int]:
    counts = {
        "minimum_pilot": 1,
        "swe_bench": (
            studies.swe_bench.pilot_instances
            + studies.swe_bench.formal_instances
            + studies.swe_bench.repeated_instances
            * studies.swe_bench.repeat_extra_runs
        ),
        "mcp_tool_loading": 30 * 5 * 2,
        "retention": len(studies.retention.session_seeds) * len(studies.retention.profiles),
        "permission": (
            len(studies.permission.tasks) * studies.permission.repetitions
            * len(studies.permission.strategies)
        ),
        "multi_agent": (
            len(studies.multi_agent.tasks) * studies.multi_agent.repetitions
            * len(studies.multi_agent.modes)
        ),
        "hook": studies.hook.paid_runs,
        "long_session": studies.long_session.pilot.count + studies.long_session.formal.count,
    }
    counts["total"] = sum(counts.values())
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate frozen Goal 2 studies")
    parser.add_argument("--config", type=Path, default=Path("evals/goal2/studies.yaml"))
    args = parser.parse_args(argv)
    try:
        studies = load_studies(args.config)
        canaries = [
            value for index in range(len(studies.retention.session_seeds))
            for value in retention_canaries(studies.retention, index)
        ]
        if len(canaries) != len(set(canaries)):
            raise ValueError("retention canaries are not globally unique")
        print(json.dumps({
            "valid": True,
            "planned_paid_runs": planned_paid_runs(studies),
            "retention_canary_count": len(canaries),
            "claims_are_not_statistical_significance": True,
        }, sort_keys=True))
        return 0
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"Goal 2 study error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

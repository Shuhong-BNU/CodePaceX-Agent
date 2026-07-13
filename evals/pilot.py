"""Frozen Pilot configuration, dry-run validation, and guarded execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from evals.benchmark import RunManifest, RunRecorder, canonical_hash, current_git_commit, sanitize_origin

FROZEN_PROVIDER = "bailian-qwen37-max"
FROZEN_PROTOCOL = "openai-compat"
FROZEN_BASE_URL = "https://llm-ipge9fy38w648m28.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
FROZEN_MODEL = "qwen3.7-max-2026-06-08"
FROZEN_KEY_ENV = "BAILIAN_API_KEY"


class ModelParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    temperature: float | None = None
    top_p: float | None = None
    max_output_tokens: int | None = Field(default=None, gt=0)


class PilotConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    experiment_kind: str = "pilot"
    provider: str
    protocol: str
    base_url: str
    api_key_env: str
    model_id: str
    fallback_enabled: bool = False
    model_parameters: ModelParameters = Field(default_factory=ModelParameters)
    timeout_seconds: int | None = Field(default=None, gt=0)
    retry_budget: int = Field(default=0, ge=0)
    task_ids: list[str] = Field(default_factory=list)
    repetitions: int = Field(default=1, ge=1)
    feature_flags: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_frozen_primary(self) -> PilotConfig:
        if self.schema_version != 1 or self.experiment_kind != "pilot":
            raise ValueError("only pilot schema version 1 is supported")
        if (self.provider, self.protocol, self.base_url, self.api_key_env, self.model_id) != (
            FROZEN_PROVIDER, FROZEN_PROTOCOL, FROZEN_BASE_URL, FROZEN_KEY_ENV, FROZEN_MODEL,
        ):
            raise ValueError("Pilot schema v1 only accepts the frozen Bailian/Qwen provider")
        if self.fallback_enabled or self.retry_budget != 0:
            raise ValueError("frozen Pilot configuration requires fallback=false and retry_budget=0")
        if self.model_parameters.temperature is not None or self.model_parameters.top_p is not None:
            raise ValueError("temperature and top_p remain unset until a real Pilot is frozen")
        return self


def load_config(path: Path) -> PilotConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Pilot configuration must be a mapping")
    return PilotConfig.model_validate(raw)


def config_hash(config: PilotConfig) -> str:
    return canonical_hash(config.model_dump(mode="json"))


def _git_dirty(root: Path) -> bool | None:
    try:
        result = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], text=True, capture_output=True, check=False)
    except OSError:
        return None
    return bool(result.stdout) if result.returncode == 0 else None


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def build_manifest(config: PilotConfig, root: Path, *, run_id: str = "") -> RunManifest:
    from codepacex.prompts import build_system_prompt
    from codepacex.tools import create_default_registry

    schemas = create_default_registry().get_all_schemas(config.protocol)
    return RunManifest(
        experiment_kind=config.experiment_kind,
        provider=config.provider,
        protocol=config.protocol,
        base_url_origin=sanitize_origin(config.base_url),
        api_key_env=config.api_key_env,
        model_id=config.model_id,
        run_id=run_id,
        git_commit=current_git_commit(root),
        dirty_worktree=_git_dirty(root),
        prompt_version="codepacex-system-v1",
        system_prompt_hash=_hash_text(build_system_prompt()),
        tool_schema_hash=canonical_hash(schemas),
        feature_flags=config.feature_flags,
        task_ids=config.task_ids,
        model_parameters=config.model_parameters.model_dump(mode="json"),
        context_window=None,
        max_output_tokens=config.model_parameters.max_output_tokens,
        timeout_seconds=config.timeout_seconds,
        retry_budget=config.retry_budget,
        fallback_enabled=config.fallback_enabled,
        experiment_config_hash=config_hash(config),
    )


def dry_run(config: PilotConfig, root: Path, runs_dir: Path, run_id: str | None = None) -> RunRecorder:
    recorder = RunRecorder(runs_dir, build_manifest(config, root, run_id=run_id or ""), run_id=run_id, repo_root=root)
    recorder.event("dry_run", {
        "network_called": False, "model_called": False, "api_key_env": config.api_key_env,
        "api_key_present": bool(os.environ.get(config.api_key_env)), "task_count": len(config.task_ids),
    })
    recorder.finalize({"status": "dry_run", "execution_mode": "dry_run", "scorable": False})
    return recorder


def _configuration_error(
    config: PilotConfig, root: Path, runs_dir: Path, run_id: str | None = None,
) -> RunRecorder:
    recorder = RunRecorder(
        runs_dir, build_manifest(config, root, run_id=run_id or ""), run_id=run_id, repo_root=root,
    )
    recorder.finalize({"status": "configuration_error", "execution_mode": "live", "scorable": False})
    return recorder


def _child_environment(config: PilotConfig, home: str) -> dict[str, str]:
    """Pass only the frozen provider credential to the isolated eval child."""
    environment = {
        key: value for key, value in os.environ.items()
        if not key.upper().endswith(("_API_KEY", "_TOKEN", "_SECRET"))
    }
    environment["HOME"] = home
    environment[config.api_key_env] = os.environ[config.api_key_env]
    return environment


def _aggregate_status(statuses: list[str]) -> str:
    for status in ("provider_error", "infrastructure_error", "timeout", "task_failure"):
        if status in statuses:
            return status
    return "success"


def _ingest_trace(recorder: RunRecorder, trace_path: Path) -> None:
    """Derive only events actually emitted by the existing stream-json runner."""
    try:
        lines = trace_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "usage":
            recorder.capture_event({
                "type": "usage",
                "request_index": event.get("request_index"),
                "provider": event.get("provider"),
                "model_id": event.get("model_id"),
                "input_tokens": event.get("input_tokens"),
                "output_tokens": event.get("output_tokens"),
                "request_input_tokens": event.get("request_input_tokens"),
                "request_output_tokens": event.get("request_output_tokens"),
                "provider_usage": event.get("provider_usage"),
            })
        elif event.get("type") == "compact":
            recorder.capture_event({
                "type": "compression", "success": True, "reason": "automatic",
                "tokens_before": None, "tokens_after": None, "attachment_count": None,
                "error_category": None,
            })


def _run_trials(config: PilotConfig, root: Path, recorder: RunRecorder) -> list[str]:
    """Run only incomplete trials; callers must have performed paid-run checks."""
    statuses: list[str] = []
    stdout_chunks: list[str | bytes] = []
    stderr_chunks: list[str | bytes] = []
    completed = recorder.completed_trials()
    with tempfile.TemporaryDirectory(prefix="codepacex-pilot-") as home:
        config_dir = Path(home) / ".codepacex"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(yaml.safe_dump({
            "providers": [{"name": config.provider, "protocol": config.protocol,
                "base_url": config.base_url, "model": config.model_id,
                "api_key_env": config.api_key_env,
                "max_output_tokens": config.model_parameters.max_output_tokens}],
            "fallback": [],
        }), encoding="utf-8")
        env = _child_environment(config, home)
        for repetition in range(1, config.repetitions + 1):
            for task_id in config.task_ids:
                trial = (task_id, str(repetition))
                if trial in completed:
                    recorder.event("trial_skipped", {"task_id": task_id, "repetition_id": str(repetition), "reason": "already_completed"})
                    continue
                report_dir = recorder.path / "artifacts" / "task-runs" / f"{task_id}-{repetition}"
                command = [sys.executable, "evals/run_eval.py", "--task", task_id, "--report-dir", str(report_dir)]
                started = time.monotonic()
                try:
                    process = subprocess.run(command, cwd=root, env=env, text=True, capture_output=True, timeout=config.timeout_seconds)
                    status = "success" if process.returncode == 0 else "task_failure"
                    stdout_chunks.append(process.stdout or "")
                    stderr_chunks.append(process.stderr or "")
                    for trace_path in report_dir.glob("*/**/trace.ndjson"):
                        _ingest_trace(recorder, trace_path)
                except subprocess.TimeoutExpired as exc:
                    status = "timeout"
                    stdout_chunks.append(exc.stdout or "")
                    stderr_chunks.append(exc.stderr or "")
                except OSError as exc:
                    status = "infrastructure_error"
                    stderr_chunks.append(str(exc))
                statuses.append(status)
                recorder.event("trial_completed", {"task_id": task_id, "repetition_id": str(repetition), "status": status, "duration_seconds": time.monotonic() - started})
    if stdout_chunks:
        recorder.write_artifact("stdout.txt", "\n".join(_as_text(item) for item in stdout_chunks))
    if stderr_chunks:
        recorder.write_artifact("stderr.txt", "\n".join(_as_text(item) for item in stderr_chunks))
    return statuses


def _as_text(value: str | bytes) -> str:
    return value if isinstance(value, str) else value.decode(errors="replace")


def execute(
    config: PilotConfig, root: Path, runs_dir: Path, *, confirmed: bool, run_id: str | None = None,
) -> RunRecorder:
    if not confirmed or not config.task_ids or not os.environ.get(config.api_key_env):
        return _configuration_error(config, root, runs_dir, run_id)
    recorder = RunRecorder(
        runs_dir, build_manifest(config, root, run_id=run_id or ""), run_id=run_id, repo_root=root,
    )
    # The only live backend deliberately reuses the deterministic 6-task harness.
    # Tests mock this subprocess; this module never calls it during dry-run or CI.
    statuses = _run_trials(config, root, recorder)
    recorder.finalize({"status": _aggregate_status(statuses), "execution_mode": "live", "scorable": True})
    return recorder


def resume(
    config: PilotConfig, root: Path, runs_dir: Path, run_id: str, *, confirmed: bool,
) -> RunRecorder:
    if not confirmed or not config.task_ids or not os.environ.get(config.api_key_env):
        raise ValueError("resume requires --confirm-paid-run, tasks, and the configured API key")
    recorder = RunRecorder.resume(runs_dir, run_id, build_manifest(config, root, run_id=run_id))
    statuses = _run_trials(config, root, recorder)
    recorder.finalize({"status": _aggregate_status(statuses), "execution_mode": "live", "scorable": True, "resumed": True})
    return recorder


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CodePaceX reproducible Pilot harness")
    parser.add_argument("command", choices=["validate", "dry-run", "execute", "resume"])
    parser.add_argument("--config", type=Path, default=Path("evals/pilot.qwen.yaml"))
    parser.add_argument("--runs-dir", type=Path, default=Path("evals/.runs/pilot"))
    parser.add_argument("--run-id")
    parser.add_argument("--confirm-paid-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == "validate":
            print(json.dumps({"valid": True, "config_hash": config_hash(config), "provider": config.provider, "model_id": config.model_id, "api_key_env": config.api_key_env, "api_key_present": bool(os.environ.get(config.api_key_env))}))
            return 0
        if args.command == "dry-run":
            print(dry_run(config, Path.cwd(), args.runs_dir, args.run_id).path)
            return 0
        if args.command == "resume":
            if not args.run_id:
                raise ValueError("resume requires --run-id")
            print(resume(config, Path.cwd(), args.runs_dir, args.run_id, confirmed=args.confirm_paid_run).path)
            return 0
        print(execute(config, Path.cwd(), args.runs_dir, confirmed=args.confirm_paid_run, run_id=args.run_id).path)
        return 0
    except (ValueError, OSError, yaml.YAMLError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

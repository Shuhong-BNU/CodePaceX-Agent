"""Frozen Pilot configuration, dry-run validation, and guarded execution."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from codepacex.experiments import ExperimentProfile
from evals.benchmark import RunManifest, RunRecorder, canonical_hash, current_git_commit, sanitize_origin

FROZEN_PROVIDER = "bailian-qwen37-max"
FROZEN_PROTOCOL = "openai-compat"
FROZEN_BASE_URL = "https://llm-ipge9fy38w648m28.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
FROZEN_MODEL = "qwen3.7-max-2026-06-08"
FROZEN_KEY_ENV = "BAILIAN_API_KEY"
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_ENV_NAMES = {
    "PATH", "TMPDIR", "TMP", "TEMP", "LANG", "PYTHONPATH", "VIRTUAL_ENV",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
}
_PROXY_NAMES = {
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
}
_EXIT_CODES = {
    "success": 0, "dry_run": 0, "task_failure": 1, "configuration_error": 2,
    "timeout": 3, "provider_error": 3, "infrastructure_error": 3, "cancelled": 3,
}


class PilotConfigurationError(ValueError):
    """A generated Pilot child configuration cannot be used safely."""


class ModelParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    temperature: float | None = None
    top_p: float | None = None
    max_output_tokens: int | None = Field(default=None, gt=0)


class PilotConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[2] = 2
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
    experiment_profile: ExperimentProfile
    max_iterations: Literal[50] = 50

    @model_validator(mode="after")
    def validate_frozen_primary(self) -> PilotConfig:
        if self.experiment_kind != "pilot":
            raise ValueError("only pilot schema version 2 is supported")
        if (self.provider, self.protocol, self.base_url, self.api_key_env, self.model_id) != (
            FROZEN_PROVIDER, FROZEN_PROTOCOL, FROZEN_BASE_URL, FROZEN_KEY_ENV, FROZEN_MODEL,
        ):
            raise ValueError("Pilot schema v1 only accepts the frozen Bailian/Qwen provider")
        if self.fallback_enabled or self.retry_budget != 0:
            raise ValueError("frozen Pilot configuration requires fallback=false and retry_budget=0")
        if self.model_parameters.temperature is not None or self.model_parameters.top_p is not None:
            raise ValueError("temperature and top_p remain unset until a real Pilot is frozen")
        if self.model_parameters.max_output_tokens != 8192:
            raise ValueError("Pilot v2 freezes max_output_tokens at 8192")
        if self.feature_flags:
            raise ValueError(
                "Pilot v1 does not map feature_flags to runtime behavior; live runs require {}"
            )
        return self


def load_config(path: Path) -> PilotConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Pilot configuration must be a mapping")
    return PilotConfig.model_validate(raw)


def config_hash(config: PilotConfig) -> str:
    return canonical_hash(config.model_dump(mode="json"))


def benchmark_asset_hash(root: Path) -> str:
    """Hash frozen tasks, fixtures, graders, and the deterministic runner."""
    evals_root = root / "evals"
    files = [evals_root / "run_eval.py", evals_root / "graders.py"]
    files.extend(sorted((evals_root / "tasks").glob("*.yaml")))
    files.extend(sorted(
        path for path in (evals_root / "fixtures").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    ))
    payload = {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in files
    }
    return canonical_hash(payload)


def _git_dirty(root: Path) -> bool | None:
    try:
        result = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], text=True, capture_output=True, check=False)
    except OSError:
        return None
    return bool(result.stdout) if result.returncode == 0 else None


def _validate_task_ids(task_ids: list[str], root: Path) -> None:
    registry: set[str] = set()
    for path in (root / "evals" / "tasks").glob("*.yaml"):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("id"), str):
            registry.add(raw["id"])
    for task_id in task_ids:
        if not TASK_ID_RE.fullmatch(task_id) or task_id not in registry:
            raise ValueError(f"unknown or unsafe Pilot task ID: {task_id}")


def build_manifest(config: PilotConfig, root: Path, *, run_id: str = "") -> RunManifest:
    if config.feature_flags:
        raise PilotConfigurationError("unmapped feature_flags cannot enter a Pilot Run")
    _validate_task_ids(config.task_ids, root)
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
        system_prompt_hash=None,
        tool_schema_hash=None,
        feature_flags=config.feature_flags,
        experiment_profile=config.experiment_profile.canonical_payload(),
        experiment_profile_hash=config.experiment_profile.profile_hash(),
        runtime_contract_hash=config.experiment_profile.runtime_contract_hash(),
        benchmark_asset_hash=benchmark_asset_hash(root),
        task_ids=config.task_ids,
        repetitions=config.repetitions,
        model_parameters=config.model_parameters.model_dump(mode="json"),
        context_window=None,
        max_output_tokens=config.model_parameters.max_output_tokens,
        timeout_seconds=config.timeout_seconds,
        retry_budget=config.retry_budget,
        fallback_enabled=config.fallback_enabled,
        max_iterations=config.max_iterations,
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
        if key in _ENV_NAMES or key.startswith("LC_")
    }
    environment["HOME"] = home
    environment[config.api_key_env] = os.environ[config.api_key_env]
    return environment


def _runtime_secrets(config: PilotConfig) -> list[str]:
    names = {config.api_key_env, *_PROXY_NAMES}
    return [os.environ[name] for name in names if os.environ.get(name)]


def _provider_payload(config: PilotConfig) -> dict[str, Any]:
    provider: dict[str, Any] = {
        "name": config.provider, "protocol": config.protocol, "base_url": config.base_url,
        "model": config.model_id, "api_key_env": config.api_key_env,
    }
    if config.model_parameters.max_output_tokens is not None:
        provider["max_output_tokens"] = config.model_parameters.max_output_tokens
    return {"providers": [provider], "fallback": []}


def _write_validated_provider_config(config: PilotConfig, path: Path) -> None:
    from codepacex.config import load_config as load_codepacex_config

    path.write_text(yaml.safe_dump(_provider_payload(config)), encoding="utf-8")
    loaded = load_codepacex_config(path)
    primary = loaded.providers[0]
    if (primary.name, primary.protocol, primary.base_url, primary.model) != (
        config.provider, config.protocol, config.base_url, config.model_id,
    ):
        raise ValueError("generated Provider configuration changed frozen identity")


def _aggregate_status(statuses: list[str]) -> str:
    for status in (
        "configuration_error", "provider_error", "timeout", "infrastructure_error",
        "task_failure",
    ):
        if status in statuses:
            return status
    return "success"


def _suite_status(report_dir: Path, task_id: str, returncode: int) -> str:
    results = list(report_dir.glob("*/suite_result.json"))
    if len(results) != 1:
        return "infrastructure_error"
    try:
        suite = json.loads(results[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "infrastructure_error"
    tasks = suite.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 1 or tasks[0].get("id") != task_id:
        return "infrastructure_error"
    task = tasks[0]
    status = task.get("status")
    if status == "PASS":
        return "success" if returncode == 0 else "infrastructure_error"
    if status == "FAIL":
        return "timeout" if task.get("timed_out") else "task_failure"
    if status != "ERROR":
        return "infrastructure_error"
    error_type = task.get("error_type")
    if error_type == "provider_network_error":
        return "provider_error"
    if error_type in {"auth_error", "config_error"}:
        return "configuration_error"
    if error_type == "startup_timeout" or task.get("timed_out"):
        return "timeout"
    return "infrastructure_error"


def _ingest_trace(
    recorder: RunRecorder, trace_path: Path, task_id: str, repetition_id: str,
    attempt_id: int,
) -> None:
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
                "task_id": task_id, "repetition_id": repetition_id,
                "attempt_id": attempt_id,
            })
        elif event.get("type") == "runtime_manifest":
            recorder.capture_event({
                "type": "runtime_manifest",
                "request_index": event.get("request_index"),
                "provider": event.get("provider"),
                "protocol": event.get("protocol"),
                "model_id": event.get("model_id"),
                "system_sha256": event.get("system_sha256"),
                "tools_sha256": event.get("tools_sha256"),
                "messages_sha256": event.get("messages_sha256"),
                "experiment_profile_hash": event.get("experiment_profile_hash"),
                "runtime_contract_hash": event.get("runtime_contract_hash"),
                "combined_runtime_hash": event.get("combined_runtime_hash"),
                "task_id": task_id, "repetition_id": repetition_id,
                "attempt_id": attempt_id,
            })
        elif event.get("type") == "permission_decision":
            recorder.capture_event({
                "type": "permission_decision",
                "tool_use_id": event.get("tool_use_id"),
                "tool_name": event.get("tool_name"),
                "final_effect": event.get("final_effect"),
                "mandatory_safety": event.get("mandatory_safety"),
                "hook_effect": event.get("hook_effect"),
                "hitl_required": event.get("hitl_required"),
                "hitl_response": event.get("hitl_response"),
                "persistable": event.get("persistable"),
                "executed": event.get("executed"),
                "execution_path": event.get("execution_path"),
                "task_id": task_id, "repetition_id": repetition_id,
                "attempt_id": attempt_id,
            })
        elif event.get("type") == "compression":
            recorder.capture_event({
                "type": "compression", "trigger": event.get("trigger"),
                "success": event.get("success"),
                "tokens_before": event.get("tokens_before"),
                "tokens_after": event.get("tokens_after"),
                "attachment_count": event.get("attachment_count"),
                "error_category": event.get("error_category"),
                "task_id": task_id, "repetition_id": repetition_id,
                "attempt_id": attempt_id,
            })


def _run_trials(config: PilotConfig, root: Path, recorder: RunRecorder) -> list[str]:
    """Run only incomplete trials; callers must have performed paid-run checks."""
    statuses: list[str] = []
    stdout_chunks: list[str | bytes] = []
    stderr_chunks: list[str | bytes] = []
    successful = recorder.successful_trials()
    with (
        tempfile.TemporaryDirectory(prefix="codepacex-pilot-home-") as home,
        tempfile.TemporaryDirectory(prefix="codepacex-pilot-stage-") as staging,
    ):
        config_dir = Path(home) / ".codepacex"
        config_dir.mkdir()
        try:
            _write_validated_provider_config(config, config_dir / "config.yaml")
        except (OSError, ValueError) as exc:
            raise PilotConfigurationError(str(exc)) from exc
        env = _child_environment(config, home)
        profile_path = Path(staging) / "experiment-profile.yaml"
        profile_path.write_text(
            yaml.safe_dump(config.experiment_profile.canonical_payload(), sort_keys=True),
            encoding="utf-8",
        )
        for repetition in range(1, config.repetitions + 1):
            for task_id in config.task_ids:
                trial = (task_id, str(repetition))
                if trial in successful:
                    recorder.event("trial_skipped", {
                        "task_id": task_id,
                        "repetition_id": str(repetition),
                        "reason": "already_successful",
                    })
                    continue
                attempt_id = recorder.next_attempt_id(task_id, str(repetition))
                report_dir = Path(staging) / f"{task_id}-{repetition}"
                command = [
                    sys.executable, "evals/run_eval.py", "--task", task_id,
                    "--report-dir", str(report_dir),
                    "--experiment-profile", str(profile_path),
                ]
                started = time.monotonic()
                recorder.event("trial_started", {
                    "task_id": task_id, "repetition_id": str(repetition),
                    "attempt_id": attempt_id,
                })
                try:
                    process = subprocess.run(command, cwd=root, env=env, text=True, capture_output=True, timeout=config.timeout_seconds)
                    status = _suite_status(report_dir, task_id, process.returncode)
                    stdout_chunks.append(process.stdout or "")
                    stderr_chunks.append(process.stderr or "")
                    for trace_path in report_dir.glob("*/**/trace.ndjson"):
                        _ingest_trace(recorder, trace_path, task_id, str(repetition), attempt_id)
                except subprocess.TimeoutExpired as exc:
                    status = "timeout"
                    stdout_chunks.append(exc.stdout or "")
                    stderr_chunks.append(exc.stderr or "")
                except OSError as exc:
                    status = "infrastructure_error"
                    stderr_chunks.append(str(exc))
                except (ValueError, json.JSONDecodeError) as exc:
                    status = "infrastructure_error"
                    stderr_chunks.append(f"trace ingestion failed: {exc}")
                statuses.append(status)
                recorder.event("trial_completed", {
                    "task_id": task_id,
                    "repetition_id": str(repetition),
                    "attempt_id": attempt_id,
                    "status": status,
                    "duration_seconds": time.monotonic() - started,
                })
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
        runs_dir, build_manifest(config, root, run_id=run_id or ""), run_id=run_id,
        repo_root=root, secrets=_runtime_secrets(config),
    )
    # The only live backend deliberately reuses the deterministic 6-task harness.
    # Tests mock this subprocess; this module never calls it during dry-run or CI.
    try:
        statuses = _run_trials(config, root, recorder)
        recorder.finalize({"status": _aggregate_status(statuses), "execution_mode": "live"})
    except asyncio.CancelledError:
        recorder.finalize({"status": "cancelled", "execution_mode": "live"})
        raise
    except PilotConfigurationError:
        recorder.finalize({"status": "configuration_error", "execution_mode": "live"})
    except Exception as exc:
        recorder.event("execution_error", {"category": "infrastructure_error", "message": str(exc)})
        recorder.finalize({"status": "infrastructure_error", "execution_mode": "live"})
    return recorder


def resume(
    config: PilotConfig, root: Path, runs_dir: Path, run_id: str, *, confirmed: bool,
) -> RunRecorder:
    if not confirmed or not config.task_ids or not os.environ.get(config.api_key_env):
        raise ValueError("resume requires --confirm-paid-run, tasks, and the configured API key")
    recorder = RunRecorder.resume(
        runs_dir, run_id, build_manifest(config, root, run_id=run_id),
        secrets=_runtime_secrets(config),
    )
    try:
        statuses = _run_trials(config, root, recorder)
        status = _aggregate_status(statuses) if statuses else (
            recorder.previous_status or "infrastructure_error"
        )
        recorder.finalize({"status": status, "execution_mode": "live", "resumed": True})
    except asyncio.CancelledError:
        recorder.finalize({"status": "cancelled", "execution_mode": "live", "resumed": True})
        raise
    except PilotConfigurationError:
        recorder.finalize({"status": "configuration_error", "execution_mode": "live", "resumed": True})
    except Exception as exc:
        recorder.event("execution_error", {"category": "infrastructure_error", "message": str(exc)})
        recorder.finalize({"status": "infrastructure_error", "execution_mode": "live", "resumed": True})
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
            recorder = resume(
                config, Path.cwd(), args.runs_dir, args.run_id,
                confirmed=args.confirm_paid_run,
            )
        else:
            recorder = execute(
                config, Path.cwd(), args.runs_dir, confirmed=args.confirm_paid_run,
                run_id=args.run_id,
            )
        print(recorder.path)
        result = json.loads((recorder.path / "result.json").read_text(encoding="utf-8"))
        return _EXIT_CODES[str(result["status"])]
    except (ValueError, OSError, yaml.YAMLError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Minimal, zero-cost bootstrap for native Goal 3 SWE-bench-Live controls.

This module deliberately does not implement paid inference.  It isolates Goal 3
artifacts, validates the required native host, and runs only evaluator controls.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

from evals.benchmark import RunManifest, RunRecorder, canonical_hash, current_git_commit
from evals.swe_bench_live import (
    instance_payload_hash,
    load_jsonl,
    run_official_evaluator,
    select_pilot_instances,
)


DEFAULT_ENVIRONMENT = Path("evals/goal3/swe_official_environment.json")
DEFAULT_PILOT_TEMPLATE = Path("evals/goal3/pilot.template.json")
DEFAULT_RUNS_DIR = Path("evals/.runs/goal3-swe")
DEFAULT_CONTROL_RUNS_DIR = Path("evals/.runs/goal3-control")
GOAL3_BUDGET_AUTHORIZATION = DEFAULT_CONTROL_RUNS_DIR / "budget-authorization.json"
GOAL3_BUDGET_LEDGER = DEFAULT_CONTROL_RUNS_DIR / "budget-ledger.json"
GOAL3_BUDGET_ALLOCATION = DEFAULT_CONTROL_RUNS_DIR / "budget-allocation.json"
NATIVE_ARCHITECTURES = {"x86_64", "amd64"}
QEMU_MARKERS = ("qemu", "tcg", "virtual cpu")
ENVIRONMENT = {
    "schema_version": 1,
    "repository": "https://github.com/microsoft/SWE-bench-Live",
    "branch": "python-only",
    "commit": "ad79b850f15e33992e96f03f6e97f05ddf9aa0be",
    "dataset": "SWE-bench-Live/SWE-bench-Live",
    "split": "lite",
    "evaluator_namespace": "starryzhang",
    "installation": "isolated-editable-checkout",
    "docker_required": True,
    "native_host_required": True,
    "allowed_architectures": ["x86_64", "amd64"],
}


def load_goal3_environment(path: Path = DEFAULT_ENVIRONMENT) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Goal 3 official environment must be a JSON object")
    for key, value in ENVIRONMENT.items():
        if payload.get(key) != value:
            raise ValueError(f"Goal 3 official environment changed: {key}")
    if set(payload) != {*ENVIRONMENT, "notes"}:
        raise ValueError("Goal 3 official environment has unknown fields")
    if not isinstance(payload["notes"], list) or not all(isinstance(item, str) for item in payload["notes"]):
        raise ValueError("Goal 3 official environment notes must be strings")
    return payload


def load_goal3_pilot_template(path: Path = DEFAULT_PILOT_TEMPLATE) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema_version", "status", "experiment_kind", "provider", "model_id",
        "dataset_revision", "pricing_snapshot", "instance_ids", "instance_count",
        "fallback_enabled", "retry_budget", "serial", "model_parameters",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("Goal 3 Pilot template has an invalid schema")
    if payload["schema_version"] != 1 or payload["status"] != "candidate_unverified":
        raise ValueError("Goal 3 Pilot template must remain an unverified candidate")
    if payload["experiment_kind"] != "goal3-swe-bench-live-pilot":
        raise ValueError("Goal 3 Pilot template has an invalid Run identity")
    if payload["provider"] is not None or payload["model_id"] is not None:
        raise ValueError("Goal 3 Pilot provider and model require explicit later verification")
    if payload["dataset_revision"] is not None or payload["pricing_snapshot"] is not None:
        raise ValueError("Goal 3 Pilot dataset and pricing require explicit later verification")
    if payload["instance_ids"] != [] or payload["instance_count"] != 3:
        raise ValueError("Goal 3 Pilot template must reserve exactly three later-frozen instances")
    if payload["fallback_enabled"] is not False or payload["retry_budget"] != 0 or payload["serial"] is not True:
        raise ValueError("Goal 3 Pilot template requires no fallback, no retry, and serial execution")
    if payload["model_parameters"] != {"temperature": None, "top_p": None, "max_output_tokens": None}:
        raise ValueError("Goal 3 Pilot template has unrecognized model parameters")
    return payload


def goal3_budget_paths() -> dict[str, Path]:
    """Name the future Goal 3 accounting files without creating any of them."""
    return {
        "authorization": GOAL3_BUDGET_AUTHORIZATION,
        "ledger": GOAL3_BUDGET_LEDGER,
        "allocation": GOAL3_BUDGET_ALLOCATION,
    }


def freeze_pilot_config(
    *, dataset_jsonl: Path, output: Path, dataset_revision: str,
    provider: str, model_id: str, model_parameters: dict[str, Any],
    pricing_snapshot_hash: str,
) -> dict[str, Any]:
    """Freeze three official instances only after real execution inputs are known.

    This creates configuration metadata, not an authorization, allocation, ledger,
    prediction, Claim, or Provider request.
    """
    if not all(isinstance(value, str) and value.strip() for value in (
        dataset_revision, provider, model_id,
    )):
        raise ValueError("Goal 3 Pilot freeze requires explicit dataset, Provider, and model identities")
    if not re.fullmatch(r"[0-9a-f]{64}", pricing_snapshot_hash):
        raise ValueError("Goal 3 Pilot freeze requires an explicit pricing snapshot hash")
    if set(model_parameters) != {"temperature", "top_p", "max_output_tokens"}:
        raise ValueError("Goal 3 Pilot freeze has unrecognized model parameters")
    selected = select_pilot_instances(load_jsonl(dataset_jsonl))
    instance_ids = [str(item["instance_id"]) for item in selected]
    if len(instance_ids) != 3 or len(set(instance_ids)) != 3:
        raise ValueError("Goal 3 Pilot freeze requires exactly three unique instances")
    payload = {
        "schema_version": 1,
        "status": "frozen",
        "experiment_kind": "goal3-swe-bench-live-pilot",
        "provider": provider,
        "model_id": model_id,
        "dataset_revision": dataset_revision,
        "pricing_snapshot_hash": pricing_snapshot_hash,
        "instance_ids": instance_ids,
        "instance_payload_hashes": {
            str(instance["instance_id"]): instance_payload_hash(instance)
            for instance in selected
        },
        "fallback_enabled": False,
        "retry_budget": 0,
        "serial": True,
        "model_parameters": model_parameters,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def _run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def _installed_evaluator_commit(module_origin: str | None) -> str | None:
    if not module_origin:
        return None
    origin = Path(module_origin).resolve()
    for checkout in (origin.parent, *origin.parents):
        if (checkout / ".git").exists():
            result = _run(["git", "-C", str(checkout), "rev-parse", "HEAD"])
            return result.stdout.strip() if result is not None and result.returncode == 0 else None
    return None


def _cpuinfo() -> str:
    try:
        return Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return ""


def native_preflight(
    *, root: Path, environment_path: Path = DEFAULT_ENVIRONMENT,
) -> dict[str, Any]:
    """Report, but do not soften, the native Linux x86_64 requirements."""
    environment = load_goal3_environment(environment_path)
    reported_system = platform.system()
    reported_machine = platform.machine().lower()
    uname = os.uname()
    kernel_system = uname.sysname
    kernel_machine = uname.machine.lower()
    host_is_linux = reported_system == "Linux" and kernel_system == "Linux"
    architecture_matches_kernel = reported_machine == kernel_machine
    architecture_is_native = (
        reported_machine in NATIVE_ARCHITECTURES
        and kernel_machine in NATIVE_ARCHITECTURES
        and architecture_matches_kernel
    )
    cpuinfo = _cpuinfo()
    qemu_detected = any(marker in cpuinfo for marker in QEMU_MARKERS)
    docker_cli_present = shutil.which("docker") is not None
    docker = _run(["docker", "info", "--format", "{{.ServerVersion}}"])
    docker_daemon_available = docker is not None and docker.returncode == 0
    try:
        module = importlib.util.find_spec("swebench.harness.run_evaluation")
    except ModuleNotFoundError:
        module = None
    installed_commit = _installed_evaluator_commit(
        str(module.origin) if module is not None and module.origin else None,
    )
    evaluator_commit_matches = installed_commit == environment["commit"]
    git_status = _run(["git", "-C", str(root), "status", "--porcelain"])
    git_revision = _run(["git", "-C", str(root), "rev-parse", "HEAD"])
    worktree_clean = git_status is not None and git_status.returncode == 0 and not git_status.stdout.strip()
    commit = (
        git_revision.stdout.strip()
        if git_revision is not None and git_revision.returncode == 0
        and re.fullmatch(r"[0-9a-f]{40}", git_revision.stdout.strip())
        else None
    )
    valid = all((
        host_is_linux, architecture_is_native, not qemu_detected,
        docker_cli_present, docker_daemon_available, module is not None,
        evaluator_commit_matches, worktree_clean, commit is not None,
    ))
    return {
        "valid": valid,
        "host_system": reported_system,
        "kernel_system": kernel_system,
        "host_architecture": reported_machine,
        "kernel_architecture": kernel_machine,
        "architecture_matches_kernel": architecture_matches_kernel,
        "native_linux_x86_64": host_is_linux and architecture_is_native and not qemu_detected,
        "qemu_detected": qemu_detected,
        "docker_cli_present": docker_cli_present,
        "docker_daemon_available": docker_daemon_available,
        "docker_server_version": docker.stdout.strip() if docker_daemon_available and docker else None,
        "official_evaluator_module_available": module is not None,
        "expected_evaluator_commit": environment["commit"],
        "installed_evaluator_commit": installed_commit,
        "evaluator_commit_matches": evaluator_commit_matches,
        "worktree_clean": worktree_clean,
        "git_commit": commit,
    }


def require_native_preflight(*, root: Path, environment_path: Path = DEFAULT_ENVIRONMENT) -> dict[str, Any]:
    payload = native_preflight(root=root, environment_path=environment_path)
    if not payload["valid"]:
        raise ValueError("Goal 3 requires a clean native Linux x86_64 host, Docker, and the frozen official evaluator")
    return payload


def _require_goal3_path(path: Path) -> None:
    if any(part.startswith("goal2") for part in path.resolve().parts):
        raise ValueError("Goal 3 may not write into a Goal 2 path")


def collect_official_outcomes(report_root: Path, required_ids: set[str]) -> dict[str, bool]:
    """Read the official evaluator reports without inferring an outcome from text."""
    outcomes: dict[str, bool] = {}
    for path in report_root.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        for instance_id in required_ids:
            value = payload.get(instance_id)
            if isinstance(value, dict) and isinstance(value.get("resolved"), bool):
                previous = outcomes.setdefault(instance_id, value["resolved"])
                if previous != value["resolved"]:
                    raise ValueError(f"conflicting official outcomes for {instance_id}")
        for key, resolved in (
            ("resolved_ids", True), ("resolved_instances", True),
            ("unresolved_ids", False), ("unresolved_instances", False),
            ("empty_patch_ids", False),
        ):
            values = payload.get(key)
            if isinstance(values, list):
                for instance_id in set(map(str, values)) & required_ids:
                    previous = outcomes.setdefault(instance_id, resolved)
                    if previous != resolved:
                        raise ValueError(f"conflicting official outcomes for {instance_id}")
    if set(outcomes) != required_ids:
        raise ValueError(f"official evaluator reports are incomplete: {sorted(required_ids - set(outcomes))}")
    return outcomes


def ensure_new_paid_pilot_run(*, runs_dir: Path, run_id: str, ledger_path: Path) -> None:
    """Fail closed before a future paid Pilot invocation can repeat evidence."""
    _require_goal3_path(runs_dir)
    run_path = RunRecorder._run_path(runs_dir, run_id)
    if run_path.exists():
        if (run_path / "result.json").exists():
            raise ValueError("a terminal Goal 3 Run already exists; Provider execution is forbidden")
        raise ValueError("existing Goal 3 Run has ambiguous Provider state; manual verification is required")
    if ledger_path.exists():
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Goal 3 ledger cannot be read; manual verification is required") from exc
        if not isinstance(ledger, dict) or "active_reservation" not in ledger:
            raise ValueError("Goal 3 ledger is ambiguous; manual verification is required")
        if ledger["active_reservation"] is not None:
            raise ValueError("Goal 3 ledger has an active reservation; Provider execution is forbidden")


def build_pilot_manifest(*, root: Path, template_path: Path = DEFAULT_PILOT_TEMPLATE) -> RunManifest:
    template = load_goal3_pilot_template(template_path)
    return RunManifest(
        experiment_kind="goal3-swe-bench-live-pilot",
        provider="unverified", model_id="unverified", protocol="unverified",
        git_commit=current_git_commit(root), prompt_version="swe-bench-live-inference-v1",
        feature_flags={}, swe_evaluator_architecture="native", task_ids=[],
        retry_budget=0, fallback_enabled=False, max_iterations=50,
        model_parameters=template["model_parameters"],
        experiment_config_hash=canonical_hash(template),
        benchmark_asset_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    )


def dry_run(*, root: Path, runs_dir: Path = DEFAULT_RUNS_DIR, run_id: str = "goal3-dry") -> RunRecorder:
    _require_goal3_path(runs_dir)
    recorder = RunRecorder(runs_dir, build_pilot_manifest(root=root), run_id=run_id, repo_root=root)
    recorder.event("dry_run", {
        "model_called": False, "network_called": False,
        "provider_network_called": False, "paid_execution_enabled": False,
    })
    recorder.finalize({"status": "dry_run", "execution_mode": "dry_run", "scorable": False})
    return recorder


def _control_manifest(*, root: Path, control: Literal["empty", "gold"], instance_id: str) -> RunManifest:
    return RunManifest(
        experiment_kind=f"goal3-swe-bench-live-control-{control}",
        provider="none", model_id="none", protocol="none", git_commit=current_git_commit(root),
        prompt_version="swe-bench-live-control-v1", feature_flags={},
        swe_evaluator_architecture="native", task_ids=[instance_id], repetitions=1,
        retry_budget=0, fallback_enabled=False,
    )


def _instance(dataset_jsonl: Path, instance_id: str) -> dict[str, Any]:
    matches = [item for item in load_jsonl(dataset_jsonl) if str(item.get("instance_id")) == instance_id]
    if len(matches) != 1:
        raise ValueError("Goal 3 control requires one exact official instance")
    return matches[0]


def write_control_instance(*, output: Path, instance: dict[str, Any], instance_id: str) -> None:
    """Write one official dataset row for controls without changing its task data."""
    if instance.get("instance_id") != instance_id:
        raise ValueError("official control instance identity mismatch")
    if not isinstance(instance.get("patch"), str):
        raise ValueError("official control instance has no gold patch")
    output.write_text(json.dumps(instance, default=str) + "\n", encoding="utf-8")


def run_control(
    *, root: Path, dataset_jsonl: Path, instance_id: str,
    control: Literal["empty", "gold"], runs_dir: Path = DEFAULT_CONTROL_RUNS_DIR,
    run_id: str, environment_path: Path = DEFAULT_ENVIRONMENT,
) -> RunRecorder:
    """Run one evaluator-only control; no model or Provider path is imported."""
    _require_goal3_path(runs_dir)
    preflight = require_native_preflight(root=root, environment_path=environment_path)
    instance = _instance(dataset_jsonl, instance_id)
    gold_patch = instance.get("patch")
    if control == "gold" and (not isinstance(gold_patch, str) or not gold_patch.strip()):
        raise ValueError("Goal 3 gold control requires the official gold patch")
    patch = "" if control == "empty" else gold_patch
    expected_resolved = control == "gold"
    recorder = RunRecorder(
        runs_dir, _control_manifest(root=root, control=control, instance_id=instance_id),
        run_id=run_id, repo_root=root,
    )
    prediction = [{
        "instance_id": instance_id,
        "model_name_or_path": f"goal3-control-{control}",
        "model_patch": patch,
    }]
    recorder.write_json("predictions.json", prediction)
    recorder.event("control_started", {
        "control": control, "instance_id": instance_id, "model_called": False,
        "network_called": False, "provider_network_called": False,
        "expected_resolved": expected_resolved, "evaluator_commit": preflight["installed_evaluator_commit"],
    })
    report_dir = recorder.path / "evaluation_results"
    report_dir.mkdir()
    try:
        result = run_official_evaluator(
            dataset_name="SWE-bench-Live/SWE-bench-Live", split="lite",
            predictions_path=recorder.path / "predictions.json", instance_ids=[instance_id],
            max_workers=1, run_id=run_id, namespace="starryzhang", report_dir=report_dir,
            cwd=recorder.path, evaluator_architecture="native",
        )
        recorder.write_artifact("test-output.txt", (result.stdout or "") + "\n" + (result.stderr or ""))
        if result.returncode != 0:
            raise ValueError(f"official evaluator failed with exit status {result.returncode}")
        resolved = collect_official_outcomes(recorder.path, {instance_id})[instance_id]
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        recorder.event("control_completed", {
            "control": control, "instance_id": instance_id, "evaluator_completed": False,
            "expected_resolved": expected_resolved, "error": str(exc),
        })
        recorder.finalize({"status": "infrastructure_error", "execution_mode": "control", "scorable": False})
        return recorder
    expectation_met = resolved == expected_resolved
    recorder.event("trial_completed", {
        "task_id": instance_id, "repetition_id": "1", "attempt_id": 1,
        "status": "success" if resolved else "task_failure", "official_outcome": resolved,
        "control": control, "control_expectation_met": expectation_met,
    })
    recorder.event("control_completed", {
        "control": control, "instance_id": instance_id, "evaluator_completed": True,
        "expected_resolved": expected_resolved, "resolved": resolved,
        "model_called": False, "network_called": False, "provider_network_called": False,
        "host_os": preflight["host_system"], "host_architecture": preflight["host_architecture"],
        "docker_version": preflight["docker_server_version"],
        "evaluator_commit": preflight["installed_evaluator_commit"],
    })
    recorder.finalize({
        "status": "success" if expectation_met else "task_failure", "execution_mode": "control",
        "scorable": False, "official_evaluator_completed": True, "resolved": resolved,
        "expected_resolved": expected_resolved,
    })
    return recorder


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Goal 3 native SWE-bench-Live bootstrap")
    parser.add_argument("command", choices=["preflight", "validate", "dry-run", "control-empty", "control-gold"])
    parser.add_argument("--environment", type=Path, default=DEFAULT_ENVIRONMENT)
    parser.add_argument("--pilot-template", type=Path, default=DEFAULT_PILOT_TEMPLATE)
    parser.add_argument("--dataset-jsonl", type=Path)
    parser.add_argument("--instance-id")
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    parser.add_argument("--control-runs-dir", type=Path, default=DEFAULT_CONTROL_RUNS_DIR)
    parser.add_argument("--run-id", default="goal3-dry")
    args = parser.parse_args(argv)
    root = Path.cwd()
    try:
        if args.command == "preflight":
            payload = require_native_preflight(root=root, environment_path=args.environment)
        elif args.command == "validate":
            environment = load_goal3_environment(args.environment)
            template = load_goal3_pilot_template(args.pilot_template)
            payload = {"valid": True, "environment_commit": environment["commit"], "pilot_status": template["status"]}
        elif args.command == "dry-run":
            payload = {"valid": True, "run_path": str(dry_run(root=root, runs_dir=args.runs_dir, run_id=args.run_id).path)}
        else:
            if args.dataset_jsonl is None or not args.instance_id:
                raise ValueError("control requires --dataset-jsonl and --instance-id")
            control = "empty" if args.command == "control-empty" else "gold"
            payload = {"valid": True, "run_path": str(run_control(
                root=root, dataset_jsonl=args.dataset_jsonl, instance_id=args.instance_id,
                control=control, runs_dir=args.control_runs_dir, run_id=args.run_id,
                environment_path=args.environment,
            ).path)}
        print(json.dumps(payload, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"Goal 3 SWE error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

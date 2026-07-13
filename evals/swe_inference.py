"""Guarded CodePaceX inference and official evaluation for Goal 2 SWE-bench-Live."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Literal

import yaml

from codepacex.experiments import ExperimentProfile
from evals.benchmark import RunManifest, RunRecorder, canonical_hash, current_git_commit, sanitize_origin
from evals.costing import load_pricing, pricing_snapshot_hash
from evals.goal2_studies import load_studies
from evals.paid_gate import PaidRunGate
from evals.permission_study import trace_usage
from evals.pilot import (
    _child_environment, _ingest_trace, _provider_payload, _runtime_secrets,
    load_config as load_pilot_config,
)
from evals.swe_bench_live import (
    instance_payload_hash,
    load_jsonl,
    run_official_evaluator,
    select_formal_instances,
    select_pilot_instances,
    select_repeated_subset,
    validate_predictions,
    write_goal2_manifest,
)

REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MAXIMUM_REQUESTS_PER_INSTANCE = 50
MAXIMUM_INPUT_TOKENS_PER_REQUEST = 128_000
MAXIMUM_OUTPUT_TOKENS_PER_REQUEST = 8192
DEFAULT_OFFICIAL_ENVIRONMENT = Path("evals/goal2/swe_official_environment.json")
OFFICIAL_ENVIRONMENT = {
    "schema_version": 1,
    "repository": "https://github.com/microsoft/SWE-bench-Live",
    "branch": "python-only",
    "commit": "ad79b850f15e33992e96f03f6e97f05ddf9aa0be",
    "dataset": "SWE-bench-Live/SWE-bench-Live",
    "split": "lite",
    "evaluator_namespace": "starryzhang",
    "installation": "isolated-editable-checkout",
    "docker_required": True,
    "arm64_support_is_experimental": True,
}


def frozen_profile() -> ExperimentProfile:
    return ExperimentProfile(
        tool_loading="deferred", compression_profile="recovery_v1",
        permission_strategy="session_allow", agent_mode="single",
    )


def freeze_matrix(
    *, dataset_jsonl: Path, output: Path, dataset_revision: str,
    codepacex_commit: str, model: str, provider: str,
) -> dict[str, Any]:
    if not dataset_revision:
        raise ValueError("an immutable official dataset revision is required")
    instances = load_jsonl(dataset_jsonl)
    pilot = select_pilot_instances(instances)
    formal = select_formal_instances(
        instances,
        pilot_instance_ids={str(item["instance_id"]) for item in pilot},
    )
    repeated = select_repeated_subset(formal)
    dataset_hash = hashlib.sha256(dataset_jsonl.read_bytes()).hexdigest()
    write_goal2_manifest(
        pilot_instances=pilot, formal_instances=formal,
        repeated_instances=repeated, path=output,
        dataset_name="SWE-bench-Live/SWE-bench-Live",
        revision=dataset_revision, codepacex_commit=codepacex_commit,
        model=model, provider=provider, dataset_jsonl_sha256=dataset_hash,
    )
    return json.loads(output.read_text(encoding="utf-8"))


def load_validated_matrix(
    *, matrix_path: Path, dataset_jsonl: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    if matrix.get("schema_version") != 2:
        raise ValueError("Goal 2 SWE matrix must use schema version 2")
    if matrix.get("dataset_branch") != "python-only" or matrix.get("split") != "lite":
        raise ValueError("Goal 2 SWE matrix branch or split changed")
    if matrix.get("evaluator_namespace") != "starryzhang":
        raise ValueError("Goal 2 SWE evaluator namespace changed")
    dataset_hash = hashlib.sha256(dataset_jsonl.read_bytes()).hexdigest()
    if matrix.get("dataset_jsonl_sha256") != dataset_hash:
        raise ValueError("official dataset JSONL hash does not match frozen matrix")
    instances = load_jsonl(dataset_jsonl)
    by_id = {str(item.get("instance_id", "")): item for item in instances}
    selected = set(matrix.get("pilot_instances", []))
    selected.update(str(item.get("instance_id", "")) for item in matrix.get("formal_instances", []))
    if len(selected) != 23 or not selected <= set(by_id):
        raise ValueError("frozen SWE matrix does not resolve to exactly 23 source instances")
    expected_hashes = matrix.get("instance_payload_hashes")
    if not isinstance(expected_hashes, dict) or set(expected_hashes) != selected:
        raise ValueError("frozen SWE instance payload hash set is incomplete")
    for instance_id in selected:
        if expected_hashes[instance_id] != instance_payload_hash(by_id[instance_id]):
            raise ValueError(f"official instance payload changed: {instance_id}")
    return matrix, by_id


def stage_instance_ids(
    matrix: dict[str, Any], *, stage: Literal["pilot", "formal", "repeat"],
) -> list[str]:
    if stage == "pilot":
        ids = [str(item) for item in matrix["pilot_instances"]]
    elif stage == "formal":
        ids = [str(item["instance_id"]) for item in matrix["formal_instances"]]
    else:
        ids = [str(item) for item in matrix["repeated_instances"]]
    expected = {"pilot": 3, "formal": 20, "repeat": 5}[stage]
    if len(ids) != expected or len(set(ids)) != expected:
        raise ValueError(f"SWE {stage} stage has an invalid frozen instance count")
    return ids


def load_official_environment(path: Path = DEFAULT_OFFICIAL_ENVIRONMENT) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("official SWE environment must be a JSON object")
    for key, value in OFFICIAL_ENVIRONMENT.items():
        if payload.get(key) != value:
            raise ValueError(f"official SWE environment changed: {key}")
    notes = payload.get("notes")
    if not isinstance(notes, list) or not all(isinstance(item, str) for item in notes):
        raise ValueError("official SWE environment notes must be strings")
    if set(payload) != {*OFFICIAL_ENVIRONMENT, "notes"}:
        raise ValueError("official SWE environment has unknown fields")
    return payload


def _installed_evaluator_commit(module_origin: str | None) -> str | None:
    if not module_origin:
        return None
    origin = Path(module_origin).resolve()
    for checkout in (origin.parent, *origin.parents):
        if (checkout / ".git").exists():
            revision = subprocess.run(
                ["git", "-C", str(checkout), "rev-parse", "HEAD"],
                text=True, capture_output=True, timeout=20, check=False,
            )
            return revision.stdout.strip() if revision.returncode == 0 else None
    return None


def official_evaluator_preflight(
    environment_path: Path = DEFAULT_OFFICIAL_ENVIRONMENT,
) -> dict[str, Any]:
    environment = load_official_environment(environment_path)
    try:
        module = importlib.util.find_spec("swebench.harness.run_evaluation")
    except ModuleNotFoundError:
        module = None
    installed_commit = _installed_evaluator_commit(
        str(module.origin) if module is not None and module.origin else None,
    )
    revision_matches = installed_commit == environment["commit"]
    docker = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        text=True, capture_output=True, timeout=20, check=False,
    )
    return {
        "official_evaluator_module_available": module is not None,
        "expected_evaluator_commit": environment["commit"],
        "installed_evaluator_commit": installed_commit,
        "evaluator_revision_matches": revision_matches,
        "official_evaluator_available": module is not None and revision_matches,
        "docker_daemon_available": docker.returncode == 0,
        "docker_server_version": docker.stdout.strip() if docker.returncode == 0 else None,
        "architecture": platform.machine(),
        "arm64_support_is_experimental": platform.machine() in {"arm64", "aarch64"},
    }


def _git_dirty(root: Path) -> bool | None:
    result = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        text=True, capture_output=True, check=False,
    )
    return bool(result.stdout) if result.returncode == 0 else None


def build_manifest(
    *, root: Path, matrix_path: Path, matrix: dict[str, Any],
    stage: Literal["pilot", "formal", "repeat"], repeat_index: int,
) -> RunManifest:
    pilot = load_pilot_config(root / "evals" / "pilot.qwen.yaml")
    profile = frozen_profile()
    ids = stage_instance_ids(matrix, stage=stage)
    return RunManifest(
        experiment_kind=f"goal2-swe-bench-live-{stage}",
        provider=pilot.provider, protocol=pilot.protocol,
        base_url_origin=sanitize_origin(pilot.base_url),
        api_key_env=pilot.api_key_env, model_id=pilot.model_id,
        git_commit=current_git_commit(root), dirty_worktree=_git_dirty(root),
        prompt_version="swe-bench-live-inference-v1", feature_flags={},
        experiment_profile=profile.canonical_payload(),
        experiment_profile_hash=profile.profile_hash(),
        runtime_contract_hash=profile.runtime_contract_hash(),
        benchmark_asset_hash=canonical_hash({
            "matrix_sha256": hashlib.sha256(matrix_path.read_bytes()).hexdigest(),
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }),
        task_ids=ids, repetitions=1,
        model_parameters=pilot.model_parameters.model_dump(mode="json"),
        max_output_tokens=pilot.model_parameters.max_output_tokens,
        retry_budget=0, fallback_enabled=False, max_iterations=50,
        experiment_config_hash=canonical_hash({
            "matrix": matrix, "stage": stage, "repeat_index": repeat_index,
            "profile": profile.canonical_payload(),
        }),
    )


def _write_child_config(*, pilot: Any, home: Path) -> None:
    payload = _provider_payload(pilot)
    payload["sandbox"] = {
        "enabled": False, "auto_allow": False, "network_enabled": False,
    }
    config_dir = home / ".codepacex"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=True), encoding="utf-8",
    )


def materialize_instance(instance: dict[str, Any], workspace: Path) -> None:
    repository = str(instance.get("repo", ""))
    commit = str(instance.get("base_commit", ""))
    if not REPOSITORY_RE.fullmatch(repository) or not re.fullmatch(r"[0-9a-fA-F]{7,40}", commit):
        raise ValueError("SWE instance has an unsafe repository or base commit")
    clone = subprocess.run(
        [
            "git", "clone", "--quiet", "--filter=blob:none", "--no-checkout",
            f"https://github.com/{repository}.git", str(workspace),
        ], text=True, capture_output=True, timeout=600, check=False,
    )
    if clone.returncode != 0:
        raise ValueError(f"failed to clone frozen SWE repository: {repository}")
    switch = subprocess.run(
        ["git", "-C", str(workspace), "switch", "--detach", commit],
        text=True, capture_output=True, timeout=300, check=False,
    )
    if switch.returncode != 0:
        raise ValueError(f"failed to materialize frozen SWE base commit: {commit}")
    head = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        text=True, capture_output=True, check=False,
    ).stdout.strip()
    if not head.lower().startswith(commit.lower()):
        raise ValueError("materialized SWE repository HEAD mismatch")


def extract_patch(workspace: Path) -> str:
    result = subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff"], cwd=workspace,
        text=True, capture_output=True, timeout=120, check=False,
    )
    if result.returncode != 0:
        raise ValueError("cannot extract SWE model patch")
    return result.stdout


def inference_prompt(instance: dict[str, Any]) -> str:
    problem = str(instance.get("problem_statement", "")).strip()
    if not problem:
        raise ValueError("SWE instance has no problem statement")
    return (
        "Solve the following SWE-bench-Live issue in the current repository. "
        "Inspect the code and tests, implement the smallest correct fix, and run relevant tests. "
        "Do not modify tests to hide failures. Do not merely describe a patch; edit the workspace.\n\n"
        + problem
    )


def collect_official_outcomes(
    report_root: Path, required_ids: set[str],
) -> dict[str, bool]:
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
        ):
            values = payload.get(key)
            if isinstance(values, list):
                for instance_id in set(map(str, values)) & required_ids:
                    previous = outcomes.setdefault(instance_id, resolved)
                    if previous != resolved:
                        raise ValueError(f"conflicting official outcomes for {instance_id}")
    if set(outcomes) != required_ids:
        missing = sorted(required_ids - set(outcomes))
        raise ValueError(f"official evaluator reports are incomplete: {missing}")
    return outcomes


def dry_run(
    *, root: Path, matrix_path: Path, dataset_jsonl: Path,
    runs_dir: Path, run_id: str, stage: Literal["pilot", "formal", "repeat"],
    repeat_index: int,
) -> RunRecorder:
    matrix, _ = load_validated_matrix(
        matrix_path=matrix_path, dataset_jsonl=dataset_jsonl,
    )
    recorder = RunRecorder(
        runs_dir,
        build_manifest(
            root=root, matrix_path=matrix_path, matrix=matrix,
            stage=stage, repeat_index=repeat_index,
        ),
        run_id=run_id, repo_root=root,
    )
    recorder.event("dry_run", {
        "network_called": False, "model_called": False,
        "official_evaluator_called": False,
        "instance_count": len(stage_instance_ids(matrix, stage=stage)),
        "preflight": official_evaluator_preflight(),
    })
    recorder.finalize({
        "status": "dry_run", "execution_mode": "dry_run", "scorable": False,
    })
    return recorder


def execute(
    *, root: Path, matrix_path: Path, dataset_jsonl: Path,
    runs_dir: Path, run_id: str, stage: Literal["pilot", "formal", "repeat"],
    repeat_index: int, pricing_snapshot: Path,
    budget_authorization: Path, budget_ledger: Path, confirmed: bool,
) -> RunRecorder:
    matrix, by_id = load_validated_matrix(
        matrix_path=matrix_path, dataset_jsonl=dataset_jsonl,
    )
    ids = stage_instance_ids(matrix, stage=stage)
    if stage != "repeat" and repeat_index != 0:
        raise ValueError("repeat index is only valid for the repeat stage")
    if stage == "repeat" and repeat_index not in {1, 2}:
        raise ValueError("repeat stage requires repeat index 1 or 2")
    preflight = official_evaluator_preflight()
    if not preflight["official_evaluator_available"] or not preflight["docker_daemon_available"]:
        raise ValueError("official SWE evaluator and Docker daemon must pass before paid inference")
    pilot = load_pilot_config(root / "evals" / "pilot.qwen.yaml")
    if not confirmed or not os.environ.get(pilot.api_key_env):
        raise ValueError("execute requires --confirm-paid-run and the configured API key")
    pricing = load_pricing(pricing_snapshot)
    gate = PaidRunGate(
        root=root, authorization_path=budget_authorization,
        ledger_path=budget_ledger, pricing=pricing,
    )
    manifest = build_manifest(
        root=root, matrix_path=matrix_path, matrix=matrix,
        stage=stage, repeat_index=repeat_index,
    )
    manifest.pricing_snapshot_hash = pricing_snapshot_hash(pricing)
    recorder = RunRecorder(
        runs_dir, manifest, run_id=run_id, repo_root=root,
        secrets=_runtime_secrets(pilot),
    )
    predictions: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    profile = frozen_profile()
    with gate.locked(), tempfile.TemporaryDirectory(prefix="codepacex-swe-home-") as home_text:
        home = Path(home_text)
        _write_child_config(pilot=pilot, home=home)
        profile_path = home / "profile.yaml"
        profile_path.write_text(
            yaml.safe_dump(profile.canonical_payload(), sort_keys=True), encoding="utf-8",
        )
        environment = _child_environment(pilot, home_text)
        for instance_id in ids:
            instance = by_id[instance_id]
            with tempfile.TemporaryDirectory(prefix=f"codepacex-swe-{instance_id}-") as text:
                workspace = Path(text) / "repo"
                materialize_instance(instance, workspace)
                reservation = gate.reserve(
                    f"swe/{stage}/{repeat_index}/{instance_id}",
                    maximum_requests=MAXIMUM_REQUESTS_PER_INSTANCE,
                    maximum_input_tokens_per_request=MAXIMUM_INPUT_TOKENS_PER_REQUEST,
                    maximum_output_tokens_per_request=MAXIMUM_OUTPUT_TOKENS_PER_REQUEST,
                )
                recorder.event("trial_started", {
                    "task_id": instance_id, "repetition_id": str(repeat_index or 1),
                    "attempt_id": 1, "budget_reservation_id": reservation.reservation_id,
                })
                started = time.monotonic()
                try:
                    process = subprocess.run(
                        [
                            sys.executable, "-m", "codepacex", "-p",
                            inference_prompt(instance), "--output-format", "stream-json",
                            "--experiment-profile", str(profile_path),
                        ], cwd=workspace, env=environment,
                        text=True, capture_output=True, timeout=1800, check=False,
                    )
                except subprocess.TimeoutExpired:
                    recorder.event("trial_completed", {
                        "task_id": instance_id, "repetition_id": str(repeat_index or 1),
                        "attempt_id": 1, "status": "timeout",
                        "budget_reconciliation_required": True,
                    })
                    recorder.finalize({
                        "status": "timeout", "execution_mode": "live",
                        "official_evaluator_completed": False,
                    })
                    return recorder
                requests, input_tokens, output_tokens = trace_usage(process.stdout or "")
                if requests == 0:
                    recorder.finalize({
                        "status": "infrastructure_error", "execution_mode": "live",
                        "official_evaluator_completed": False,
                    })
                    return recorder
                settlement = gate.settle(
                    reservation, requests=requests,
                    input_tokens=input_tokens, output_tokens=output_tokens,
                )
                with tempfile.NamedTemporaryFile("w", suffix=".ndjson", encoding="utf-8") as trace:
                    trace.write(process.stdout or "")
                    trace.flush()
                    _ingest_trace(
                        recorder, Path(trace.name), instance_id,
                        str(repeat_index or 1), 1,
                    )
                patch = extract_patch(workspace)
                predictions.append({
                    "instance_id": instance_id,
                    "model_name_or_path": pilot.model_id,
                    "model_patch": patch,
                })
                pending.append({
                    "instance_id": instance_id,
                    "duration_seconds": time.monotonic() - started,
                    "actual_cny": str(settlement.actual_cny),
                    "process_returncode": process.returncode,
                    "empty_patch": not bool(patch.strip()),
                })
                recorder.write_json("predictions.json", predictions)
                if process.returncode != 0 or not patch.strip():
                    recorder.event("trial_completed", {
                        "task_id": instance_id, "repetition_id": str(repeat_index or 1),
                        "attempt_id": 1, "status": "task_failure",
                        "empty_patch": not bool(patch.strip()),
                        "official_outcome": None,
                    })
                    recorder.finalize({
                        "status": "task_failure", "execution_mode": "live",
                        "official_evaluator_completed": False,
                        "empty_patch_is_failure": True,
                    })
                    return recorder
        validate_predictions(predictions, required_instance_ids=set(ids))
        predictions_path = recorder.path / "predictions.json"
        report_dir = recorder.path / "evaluation_results"
        report_dir.mkdir()
        evaluator = run_official_evaluator(
            dataset_name=str(matrix["dataset_name"]), split=str(matrix["split"]),
            predictions_path=predictions_path, instance_ids=ids, max_workers=1,
            run_id=run_id, namespace=str(matrix["evaluator_namespace"]),
            report_dir=report_dir, cwd=recorder.path,
        )
        recorder.write_artifact(
            "test-output.txt", (evaluator.stdout or "") + "\n" + (evaluator.stderr or ""),
        )
        if evaluator.returncode != 0:
            recorder.finalize({
                "status": "infrastructure_error", "execution_mode": "live",
                "official_evaluator_completed": False,
                "official_evaluator_returncode": evaluator.returncode,
            })
            return recorder
        outcomes = collect_official_outcomes(recorder.path, set(ids))
        for trial in pending:
            instance_id = str(trial["instance_id"])
            resolved = outcomes[instance_id]
            recorder.event("trial_completed", {
                "task_id": instance_id, "repetition_id": str(repeat_index or 1),
                "attempt_id": 1,
                "status": "success" if resolved else "task_failure",
                "duration_seconds": trial["duration_seconds"],
                "actual_cny": trial["actual_cny"],
                "empty_patch": False, "official_outcome": resolved,
            })
        resolved_count = sum(outcomes.values())
        recorder.finalize({
            "status": "success" if resolved_count == len(ids) else "task_failure",
            "execution_mode": "live", "official_evaluator_completed": True,
            "official_evaluator_returncode": 0,
            "resolved_count": resolved_count, "evaluated_count": len(ids),
            "arm64_support_is_experimental": preflight["arm64_support_is_experimental"],
        })
    return recorder


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Goal 2 SWE-bench-Live inference")
    parser.add_argument("command", choices=["preflight", "freeze", "validate", "dry-run", "execute"])
    parser.add_argument("--studies", type=Path, default=Path("evals/goal2/studies.yaml"))
    parser.add_argument("--dataset-jsonl", type=Path)
    parser.add_argument("--matrix", type=Path, default=Path("evals/goal2/swe_matrix.json"))
    parser.add_argument("--dataset-revision")
    parser.add_argument("--runs-dir", type=Path, default=Path("evals/.runs/goal2-swe"))
    parser.add_argument("--run-id", default="swe-dry")
    parser.add_argument("--stage", choices=["pilot", "formal", "repeat"], default="pilot")
    parser.add_argument("--repeat-index", type=int, default=0)
    parser.add_argument("--pricing-snapshot", type=Path)
    parser.add_argument("--budget-authorization", type=Path)
    parser.add_argument("--budget-ledger", type=Path)
    parser.add_argument("--confirm-paid-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        root = Path.cwd()
        studies = load_studies(args.studies)
        if args.command == "preflight":
            print(json.dumps(official_evaluator_preflight(), sort_keys=True))
            return 0
        if args.dataset_jsonl is None:
            raise ValueError("this command requires --dataset-jsonl")
        if args.command == "freeze":
            matrix = freeze_matrix(
                dataset_jsonl=args.dataset_jsonl, output=args.matrix,
                dataset_revision=args.dataset_revision or "",
                codepacex_commit=current_git_commit(root),
                model="qwen3.7-max-2026-06-08", provider="bailian-qwen37-max",
            )
            print(json.dumps({
                "valid": True, "matrix": str(args.matrix),
                "pilot_count": len(matrix["pilot_instances"]),
                "formal_count": len(matrix["formal_instances"]),
                "repeat_count": len(matrix["repeated_instances"]),
            }, sort_keys=True))
            return 0
        matrix, _ = load_validated_matrix(
            matrix_path=args.matrix, dataset_jsonl=args.dataset_jsonl,
        )
        payload: dict[str, Any] = {
            "valid": True,
            "stage_instance_count": len(stage_instance_ids(matrix, stage=args.stage)),
            "preflight": official_evaluator_preflight(),
            "official_evaluator_required_before_paid_inference": True,
            "study_repository": studies.swe_bench.repository,
        }
        if args.command == "dry-run":
            payload["run_path"] = str(dry_run(
                root=root, matrix_path=args.matrix, dataset_jsonl=args.dataset_jsonl,
                runs_dir=args.runs_dir, run_id=args.run_id, stage=args.stage,
                repeat_index=args.repeat_index,
            ).path)
        elif args.command == "execute":
            required = [args.pricing_snapshot, args.budget_authorization, args.budget_ledger]
            if any(item is None for item in required):
                raise ValueError("execute requires pricing, budget authorization, and ledger paths")
            payload["run_path"] = str(execute(
                root=root, matrix_path=args.matrix, dataset_jsonl=args.dataset_jsonl,
                runs_dir=args.runs_dir, run_id=args.run_id, stage=args.stage,
                repeat_index=args.repeat_index, pricing_snapshot=args.pricing_snapshot,
                budget_authorization=args.budget_authorization,
                budget_ledger=args.budget_ledger, confirmed=args.confirm_paid_run,
            ).path)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"SWE inference error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

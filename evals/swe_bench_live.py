"""Deterministic SWE-bench-Live instance selection and external runner adapter."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shlex
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal


SELECTION_ALGORITHM_VERSION = "linux-per-repo-v1"
FORMAL_SELECTION_ALGORITHM_VERSION = "python-lite-size-stratified-v1"
FORMAL_SIZE_TARGETS = {"one_file": 8, "two_to_four_files": 8, "five_plus_files": 4}
REPEAT_SIZE_TARGETS = {"one_file": 2, "two_to_four_files": 2, "five_plus_files": 1}
PILOT_SIZE_TARGETS = {"one_file": 1, "two_to_four_files": 1, "five_plus_files": 1}


def patch_file_count(patch: str) -> int:
    """Count distinct destination paths in a unified diff without applying it."""
    paths = {
        match.group(1)
        for line in patch.splitlines()
        if (match := re.match(r"^\+\+\+ b/(.+)$", line))
        and match.group(1) != "/dev/null"
    }
    return len(paths)


def size_bucket(instance: dict[str, Any]) -> str:
    count = patch_file_count(str(instance.get("patch", "")))
    if count == 1:
        return "one_file"
    if 2 <= count <= 4:
        return "two_to_four_files"
    if count >= 5:
        return "five_plus_files"
    raise ValueError(f"instance has no measurable gold patch: {instance.get('instance_id')}")


def select_formal_instances(
    instances: list[dict[str, Any]], *, pilot_instance_ids: set[str],
) -> list[dict[str, Any]]:
    """Select the frozen 20-instance Python-lite size-stratified matrix."""
    selected: list[dict[str, Any]] = []
    counts: dict[str, int] = defaultdict(int)
    by_repo: dict[str, int] = defaultdict(int)
    for instance in sorted(instances, key=lambda item: str(item.get("instance_id", ""))):
        instance_id = str(instance.get("instance_id", ""))
        repo = str(instance.get("repo", ""))
        if not instance_id or instance_id in pilot_instance_ids or not repo:
            continue
        if instance.get("platform", "linux") != "linux" or by_repo[repo] >= 2:
            continue
        bucket = size_bucket(instance)
        if counts[bucket] >= FORMAL_SIZE_TARGETS[bucket]:
            continue
        selected.append(instance)
        counts[bucket] += 1
        by_repo[repo] += 1
    if counts != FORMAL_SIZE_TARGETS:
        raise ValueError(
            f"dataset cannot satisfy frozen formal size buckets: {dict(counts)}"
        )
    return selected


def select_pilot_instances(instances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select one deterministic smoke instance from each gold patch-size bucket."""
    selected: list[dict[str, Any]] = []
    counts: dict[str, int] = defaultdict(int)
    repositories: set[str] = set()
    for instance in sorted(instances, key=lambda item: str(item.get("instance_id", ""))):
        instance_id = str(instance.get("instance_id", ""))
        repo = str(instance.get("repo", ""))
        if not instance_id or not repo or repo in repositories:
            continue
        if instance.get("platform", "linux") != "linux":
            continue
        bucket = size_bucket(instance)
        if counts[bucket] >= PILOT_SIZE_TARGETS[bucket]:
            continue
        selected.append(instance)
        counts[bucket] += 1
        repositories.add(repo)
    if counts != PILOT_SIZE_TARGETS:
        raise ValueError(f"dataset cannot satisfy frozen pilot size buckets: {dict(counts)}")
    return selected


def instance_payload_hash(instance: dict[str, Any]) -> str:
    """Bind selection to the exact official task payload used for inference."""
    fields = {
        key: instance.get(key)
        for key in (
            "instance_id", "repo", "base_commit", "problem_statement",
            "patch", "test_patch", "version", "environment_setup_commit",
        )
    }
    encoded = json.dumps(
        fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def select_repeated_subset(
    formal_instances: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    counts: dict[str, int] = defaultdict(int)
    for instance in formal_instances:
        bucket = size_bucket(instance)
        if counts[bucket] < REPEAT_SIZE_TARGETS[bucket]:
            selected.append(instance)
            counts[bucket] += 1
    if counts != REPEAT_SIZE_TARGETS:
        raise ValueError("formal selection cannot satisfy repeated subset buckets")
    return selected


def validate_predictions(
    predictions: list[dict[str, Any]], *, required_instance_ids: set[str],
) -> None:
    by_id = {str(item.get("instance_id", "")): item for item in predictions}
    if set(by_id) != required_instance_ids:
        raise ValueError("predictions do not exactly match frozen instance IDs")
    for instance_id, prediction in by_id.items():
        patch = prediction.get("model_patch")
        if not isinstance(patch, str) or not patch.strip():
            raise ValueError(f"empty model patch is not scorable: {instance_id}")


def select_instances(
    instances: list[dict[str, Any]], limit: int = 20, *, language_field: str | None = None
) -> list[dict[str, Any]]:
    """Select a deterministic Linux sample with no more than two tasks per repo."""
    selected: list[dict[str, Any]] = []
    by_repo: dict[str, int] = defaultdict(int)
    for instance in sorted(instances, key=lambda item: str(item.get("instance_id", ""))):
        repo = str(instance.get("repo", ""))
        if not repo or by_repo[repo] >= 2:
            continue
        if instance.get("platform", "linux") != "linux":
            continue
        if language_field and str(instance.get(language_field, "")).lower() != "python":
            continue
        selected.append(instance)
        by_repo[repo] += 1
        if len(selected) == limit:
            break
    return selected


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_frozen_manifest(
    instances: list[dict[str, Any]], path: Path, *, dataset_name: str = "",
    split: str = "test", revision: str = "", source: str = "",
    codepacex_commit: str = "unknown", model: str = "", provider: str = "",
) -> None:
    payload = {
        "dataset_name": dataset_name, "split": split, "revision": revision,
        "source": source, "selection_algorithm": SELECTION_ALGORITHM_VERSION,
        "codepacex_commit": codepacex_commit, "model": model, "provider": provider,
        "instances": [{"instance_id": item.get("instance_id"), "repo": item.get("repo")} for item in instances],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_goal2_manifest(
    *, pilot_instances: list[dict[str, Any]],
    formal_instances: list[dict[str, Any]],
    repeated_instances: list[dict[str, Any]], path: Path,
    dataset_name: str, revision: str, codepacex_commit: str,
    model: str, provider: str, dataset_jsonl_sha256: str = "",
) -> None:
    pilot_ids = {str(item.get("instance_id", "")) for item in pilot_instances}
    formal_ids = {str(item.get("instance_id", "")) for item in formal_instances}
    repeated_ids = {str(item.get("instance_id", "")) for item in repeated_instances}
    if len(pilot_ids) != 3 or len(formal_ids) != 20 or len(repeated_ids) != 5:
        raise ValueError("Goal 2 manifest requires 3 pilot, 20 formal, and 5 repeated IDs")
    if pilot_ids & formal_ids or not repeated_ids <= formal_ids:
        raise ValueError("pilot/formal must be disjoint and repeats must be formal instances")
    payload = {
        "schema_version": 2,
        "dataset_name": dataset_name,
        "dataset_revision": revision,
        "dataset_branch": "python-only",
        "split": "lite",
        "selection_algorithm": FORMAL_SELECTION_ALGORITHM_VERSION,
        "codepacex_commit": codepacex_commit,
        "model": model,
        "provider": provider,
        "source_repository": "https://github.com/microsoft/SWE-bench-Live",
        "evaluator_namespace": "starryzhang",
        "dataset_jsonl_sha256": dataset_jsonl_sha256,
        "pilot_instances": sorted(pilot_ids),
        "formal_instances": [{
            "instance_id": item.get("instance_id"),
            "repo": item.get("repo"),
            "gold_file_count": patch_file_count(str(item.get("patch", ""))),
            "size_bucket": size_bucket(item),
        } for item in formal_instances],
        "repeated_instances": sorted(repeated_ids),
        "instance_payload_hashes": {
            str(item.get("instance_id")): instance_payload_hash(item)
            for item in [*pilot_instances, *formal_instances]
        },
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_evaluator_command(
    *, dataset_name: str, split: str, predictions_path: Path,
    instance_ids: list[str], max_workers: int, run_id: str, namespace: str,
    python_executable: str = sys.executable, report_dir: Path | None = None,
    evaluator_architecture: Literal["native", "x86_64"] = "native",
) -> list[str]:
    if not dataset_name or not run_id or max_workers < 1:
        raise ValueError("dataset_name, run_id and positive max_workers are required")
    arguments = ["--dataset_name", dataset_name, "--split", split,
        "--predictions_path", str(predictions_path), "--max_workers", str(max_workers),
        "--run_id", run_id, "--namespace", namespace]
    if report_dir is not None:
        arguments.extend(["--report_dir", str(report_dir)])
    if instance_ids:
        arguments.extend(["--instance_ids", *instance_ids])
    if evaluator_architecture == "x86_64":
        command = [
            python_executable,
            "-c",
            (
                "import platform,runpy;"
                "platform.machine=lambda:'x86_64';"
                "runpy.run_module('swebench.harness.run_evaluation',run_name='__main__')"
            ),
        ]
    else:
        command = [python_executable, "-m", "swebench.harness.run_evaluation"]
    return [*command, *arguments]


def run_official_evaluator(
    *, cwd: Path | None = None, **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    predictions_path = Path(kwargs["predictions_path"])
    if not predictions_path.is_file():
        raise FileNotFoundError(f"predictions file not found: {predictions_path}")
    try:
        available = importlib.util.find_spec("swebench.harness.run_evaluation") is not None
    except ModuleNotFoundError:
        available = False
    if not available:
        raise RuntimeError("official swebench evaluator is not installed")
    return subprocess.run(
        build_evaluator_command(**kwargs), cwd=cwd,
        text=True, capture_output=True, check=False,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or run the official SWE-bench evaluator command")
    parser.add_argument("--dataset-name", required=True)
    parser.add_argument("--split", default="test")
    parser.add_argument("--predictions-path", type=Path, required=True)
    parser.add_argument("--instance-ids", nargs="*", default=[])
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    kwargs = vars(args)
    dry_run = kwargs.pop("dry_run")
    if dry_run:
        print(shlex.join(build_evaluator_command(**kwargs)))
        return 0
    result = run_official_evaluator(**kwargs)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())

"""Deterministic SWE-bench-Live instance selection and external runner adapter."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shlex
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


SELECTION_ALGORITHM_VERSION = "linux-per-repo-v1"


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


def build_evaluator_command(
    *, dataset_name: str, split: str, predictions_path: Path,
    instance_ids: list[str], max_workers: int, run_id: str, namespace: str,
    python_executable: str = sys.executable,
) -> list[str]:
    if not dataset_name or not run_id or not namespace or max_workers < 1:
        raise ValueError("dataset_name, run_id, namespace and positive max_workers are required")
    command = [python_executable, "-m", "swebench.harness.run_evaluation",
        "--dataset_name", dataset_name, "--split", split,
        "--predictions_path", str(predictions_path), "--max_workers", str(max_workers),
        "--run_id", run_id, "--namespace", namespace]
    if instance_ids:
        command.extend(["--instance_ids", *instance_ids])
    return command


def run_official_evaluator(**kwargs: Any) -> subprocess.CompletedProcess[str]:
    predictions_path = Path(kwargs["predictions_path"])
    if not predictions_path.is_file():
        raise FileNotFoundError(f"predictions file not found: {predictions_path}")
    try:
        available = importlib.util.find_spec("swebench.harness.run_evaluation") is not None
    except ModuleNotFoundError:
        available = False
    if not available:
        raise RuntimeError("official swebench evaluator is not installed")
    return subprocess.run(build_evaluator_command(**kwargs), text=True, capture_output=True, check=False)


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

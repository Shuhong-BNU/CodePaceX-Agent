"""Deterministic SWE-bench-Live instance selection and external runner adapter."""

from __future__ import annotations

import json
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any


def select_instances(instances: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    """Select a frozen Python/Linux sample with no more than two tasks per repo."""
    selected: list[dict[str, Any]] = []
    by_repo: dict[str, int] = defaultdict(int)
    for instance in sorted(instances, key=lambda item: str(item.get("instance_id", ""))):
        repo = str(instance.get("repo", ""))
        if not repo or by_repo[repo] >= 2:
            continue
        if instance.get("platform", "linux") != "linux":
            continue
        selected.append(instance)
        by_repo[repo] += 1
        if len(selected) == limit:
            break
    return selected


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_frozen_manifest(instances: list[dict[str, Any]], path: Path) -> None:
    payload = [{"instance_id": item.get("instance_id"), "repo": item.get("repo")} for item in instances]
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def run_official_evaluator(dataset: str, patch_dir: Path, output_dir: Path, instance_ids: list[str], workers: int = 1) -> subprocess.CompletedProcess[str]:
    """Run the official evaluator after the user installs SWE-bench-Live separately."""
    command = [
        "python", "-m", "evaluation.evaluation", "--dataset", dataset,
        "--platform", "linux", "--patch_dir", str(patch_dir),
        "--output_dir", str(output_dir), "--workers", str(workers),
        "--overwrite", "0",
    ]
    if instance_ids:
        command.extend(["--instance_ids", *instance_ids])
    return subprocess.run(command, text=True, capture_output=True, check=False)

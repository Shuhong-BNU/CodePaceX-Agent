"""Versioned benchmark artifacts and resume-metric calculations."""

from __future__ import annotations

import json
import platform
import re
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import median
from typing import Any, Iterable

SECRET_KEY_RE = re.compile(r"(api[_-]?key|authorization|token|secret|password)", re.I)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: "[REDACTED]" if SECRET_KEY_RE.search(key) else _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


@dataclass
class RunManifest:
    kind: str
    model: str
    provider: str
    task_ids: list[str] = field(default_factory=list)
    feature_flags: dict[str, bool] = field(default_factory=dict)
    prompt_version: str = "unknown"
    git_commit: str = "unknown"
    created_at: float = field(default_factory=time.time)


class RunRecorder:
    def __init__(self, root: Path, manifest: RunManifest) -> None:
        self.run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
        self.path = root / self.run_id
        self.path.mkdir(parents=True, exist_ok=False)
        self.write_json("manifest.json", asdict(manifest))
        self.write_json("environment.json", {
            "python": sys.version,
            "platform": platform.platform(),
            "git_commit": manifest.git_commit,
        })

    def write_json(self, name: str, value: Any) -> None:
        (self.path / name).write_text(json.dumps(_redact(value), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def event(self, name: str, value: dict[str, Any]) -> None:
        record = {"timestamp": time.time(), "type": name, **_redact(value)}
        with (self.path / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def finalize(self, result: dict[str, Any]) -> None:
        self.write_json("result.json", result)
        lines = ["# Benchmark Run", "", f"- Run ID: `{self.run_id}`"]
        for key, value in sorted(result.items()):
            lines.append(f"- {key}: {value}")
        (self.path / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def percentile(values: Iterable[float], fraction: float) -> float | None:
    data = sorted(values)
    if not data:
        return None
    index = round((len(data) - 1) * fraction)
    return data[index]


def summarize(values: Iterable[float]) -> dict[str, float | int | None]:
    data = list(values)
    return {"n": len(data), "mean": sum(data) / len(data) if data else None, "median": median(data) if data else None, "p95": percentile(data, 0.95)}


def reduction_percent(baseline: float, improved: float) -> float | None:
    return None if baseline <= 0 else (1 - improved / baseline) * 100


def current_git_commit(repo_root: Path) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(repo_root), "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"

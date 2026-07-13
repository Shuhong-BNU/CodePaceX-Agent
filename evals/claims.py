"""Compile auditable resume claims exclusively from completed Run artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from evals.benchmark import canonical_hash, reduction_percent

REGISTERED_METRICS = {
    "provider_input_tokens",
    "permission_hitl_count",
    "successful_task_wall_time_seconds",
    "rate",
    "ab_reduction_percent",
}


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    metric_name: str
    unit: str
    sample_size: int = Field(gt=0)
    experiment_conditions: dict[str, Any] = Field(default_factory=dict)
    source_run_ids: list[str] = Field(min_length=1)
    generated_value: float | None = None
    generation_command: str | None = None
    status: Literal["draft", "verified", "insufficient-data"] = "draft"
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_metric(self) -> Claim:
        if self.metric_name not in REGISTERED_METRICS:
            raise ValueError(f"unregistered metric_name: {self.metric_name}")
        return self


class ClaimDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    claims: list[Claim]


def load_claims(path: Path) -> ClaimDocument:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("claims document must be a mapping")
    return ClaimDocument.model_validate(raw)


def _read_json(path: Path) -> dict[str, Any]:
    decoded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError(f"expected JSON object: {path}")
    return decoded


def _load_run(runs_dir: Path, run_id: str) -> tuple[dict[str, Any], dict[str, Any], Path] | None:
    run = runs_dir / run_id
    manifest = run / "manifest.json"
    result = run / "result.json"
    if not manifest.exists() or not result.exists():
        return None
    return _read_json(manifest), _read_json(result), run


def _compatible(runs: list[tuple[dict[str, Any], dict[str, Any], Path]]) -> bool:
    if not runs:
        return False
    fields = ("provider", "model_id")
    initial = runs[0][0]
    def non_study_flags(manifest: dict[str, Any]) -> dict[str, Any]:
        flags = manifest.get("feature_flags")
        return {key: value for key, value in flags.items() if key != "study_feature"} if isinstance(flags, dict) else {}
    return all(
        all(manifest.get(field) == initial.get(field) for field in fields)
        and non_study_flags(manifest) == non_study_flags(initial)
        for manifest, _, _ in runs[1:]
    )


def _usage_input_tokens(run: Path) -> list[float]:
    usage_path = run / "usage.json"
    if not usage_path.exists():
        return []
    usage = _read_json(usage_path)
    requests = usage.get("requests")
    if not isinstance(requests, list):
        return []
    values: list[float] = []
    for item in requests:
        if not isinstance(item, dict):
            return []
        raw = item.get("provider_usage")
        if not isinstance(raw, dict) or not isinstance(raw.get("prompt_tokens"), (int, float)):
            return []
        values.append(float(raw["prompt_tokens"]))
    return values


def _permission_hitl_count(run: Path) -> float | None:
    path = run / "permission-events.jsonl"
    if not path.exists():
        return None
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return None
        if event.get("hitl_required") is True:
            count += 1
    return float(count)


def _successful_wall_times(run: Path) -> list[float]:
    times: list[float] = []
    events = run / "events.jsonl"
    if not events.exists():
        return times
    for line in events.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return []
        if event.get("type") == "trial_completed" and event.get("status") == "success":
            duration = event.get("duration_seconds")
            if not isinstance(duration, (int, float)):
                return []
            times.append(float(duration))
    return times


def _rate(run: Path) -> float | None:
    result = _read_json(run / "result.json")
    metrics = result.get("metrics")
    if not isinstance(metrics, dict):
        return None
    numerator, denominator = metrics.get("numerator"), metrics.get("denominator")
    if not isinstance(numerator, (int, float)) or not isinstance(denominator, (int, float)) or denominator <= 0:
        return None
    return float(numerator) / float(denominator)


def _calculate(claim: Claim, runs: list[tuple[dict[str, Any], dict[str, Any], Path]]) -> float | None:
    paths = [run for _, _, run in runs]
    if claim.metric_name == "provider_input_tokens":
        values = [value for path in paths for value in _usage_input_tokens(path)]
        return sum(values) if values else None
    if claim.metric_name == "permission_hitl_count":
        values = [_permission_hitl_count(path) for path in paths]
        return sum(values) if values and all(value is not None for value in values) else None
    if claim.metric_name == "successful_task_wall_time_seconds":
        values = [value for path in paths for value in _successful_wall_times(path)]
        return sum(values) if values else None
    if claim.metric_name == "rate":
        values = [_rate(path) for path in paths]
        return sum(values) / len(values) if values and all(value is not None for value in values) else None
    if claim.metric_name == "ab_reduction_percent":
        baseline_ids = claim.experiment_conditions.get("baseline_run_ids")
        improved_ids = claim.experiment_conditions.get("improved_run_ids")
        if not isinstance(baseline_ids, list) or not isinstance(improved_ids, list):
            return None
        baseline = [item for item in runs if item[0].get("run_id") in baseline_ids]
        improved = [item for item in runs if item[0].get("run_id") in improved_ids]
        if not baseline or not improved:
            return None
        before = _calculate(claim.model_copy(update={"metric_name": "provider_input_tokens"}), baseline)
        after = _calculate(claim.model_copy(update={"metric_name": "provider_input_tokens"}), improved)
        return reduction_percent(before, after) if before is not None and after is not None else None
    return None


def compile_claims(document: ClaimDocument, runs_dir: Path) -> dict[str, Any]:
    compiled: list[dict[str, Any]] = []
    for claim in document.claims:
        runs = [_load_run(runs_dir, run_id) for run_id in claim.source_run_ids]
        usable = [run for run in runs if run is not None]
        output = claim.model_dump()
        output["generated_value"] = None
        output["generation_command"] = "python -m evals.claims compile"
        output["status"] = "insufficient-data"
        output["limitations"] = list(claim.limitations)
        if len(usable) != len(runs):
            output["limitations"].append("One or more source Run IDs do not exist or are incomplete.")
        elif any(result.get("status") != "success" for _, result, _ in usable):
            output["limitations"].append("Source Runs must be completed successes; dry-runs and failed runs are not evidence.")
        elif not _compatible(usable):
            output["limitations"].append("Provider, model, configuration hash, or feature flags differ across source Runs.")
        else:
            value = _calculate(claim, usable)
            if value is None:
                output["limitations"].append("Required measured fields are unavailable in the source artifacts.")
            elif len(usable) < claim.sample_size:
                output["limitations"].append("Source Run count is below the declared sample size.")
            else:
                output["generated_value"] = value
                output["status"] = "verified"
        compiled.append(output)
    return {"schema_version": 1, "claims": compiled, "document_hash": canonical_hash(document.model_dump(mode="json"))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile CodePaceX claims from run artifacts")
    parser.add_argument("command", choices=["compile", "validate"])
    parser.add_argument("--claims", type=Path, default=Path("evals/claims.example.yaml"))
    parser.add_argument("--runs-dir", type=Path, default=Path("evals/.runs/pilot"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        document = load_claims(args.claims)
        compiled = compile_claims(document, args.runs_dir)
        if args.command == "validate":
            print(json.dumps({"valid": True, "registered_metrics": sorted(REGISTERED_METRICS)}))
        else:
            payload = yaml.safe_dump(compiled, allow_unicode=True, sort_keys=False)
            if args.output:
                args.output.write_text(payload, encoding="utf-8")
            else:
                print(payload, end="")
        return 0
    except (ValueError, OSError, yaml.YAMLError) as exc:
        print(f"claims error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

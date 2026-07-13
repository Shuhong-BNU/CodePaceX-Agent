"""Compile auditable claims exclusively from measured, compatible trial artifacts."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from statistics import median
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evals.benchmark import RUN_ID_RE, canonical_hash, reduction_percent

REGISTERED_METRICS = {
    "provider_input_tokens",
    "permission_hitl_count",
    "successful_task_wall_time_seconds",
    "rate",
    "ab_reduction_percent",
}
REGISTERED_ALLOWED_DIFFERENCES = {
    "manifest.provider", "manifest.protocol", "manifest.model_id",
    "manifest.base_url_origin", "manifest.git_commit", "manifest.prompt_version",
    "manifest.model_parameters", "manifest.timeout_seconds", "manifest.retry_budget",
    "manifest.fallback_enabled", "manifest.task_ids", "manifest.repetitions",
    "manifest.feature_flags.study_feature",
    "manifest.feature_flags.deferred_tools",
    "manifest.feature_flags.compression_strategy",
    "manifest.feature_flags.permission_policy",
    "manifest.feature_flags.multi_agent",
    "runtime.provider", "runtime.protocol", "runtime.model_id",
    "runtime.system_sha256", "runtime.tools_sha256", "runtime.messages_sha256",
}
_MANIFEST_FIELDS = {
    "manifest.provider": "provider",
    "manifest.protocol": "protocol",
    "manifest.model_id": "model_id",
    "manifest.base_url_origin": "base_url_origin",
    "manifest.git_commit": "git_commit",
    "manifest.prompt_version": "prompt_version",
    "manifest.model_parameters": "model_parameters",
    "manifest.timeout_seconds": "timeout_seconds",
    "manifest.retry_budget": "retry_budget",
    "manifest.fallback_enabled": "fallback_enabled",
    "manifest.task_ids": "task_ids",
    "manifest.repetitions": "repetitions",
}
_RUNTIME_FIELDS = (
    "provider", "protocol", "model_id", "system_sha256", "tools_sha256",
    "messages_sha256",
)


class ExperimentConditions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    protocol: str
    model_id: str
    base_url_origin: str | None
    git_commit: str
    prompt_version: str
    model_parameters: dict[str, Any]
    timeout_seconds: int | None
    retry_budget: int | None
    fallback_enabled: bool
    task_ids: list[str]
    repetitions: int = Field(ge=1)
    feature_flags: dict[str, Any]
    allowed_differences: list[str] = Field(
        description="Exact registered identity paths allowed to differ; no wildcards."
    )
    baseline_run_ids: list[str] = Field(default_factory=list)
    improved_run_ids: list[str] = Field(default_factory=list)

    @field_validator("allowed_differences")
    @classmethod
    def validate_allowed_differences(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("allowed_differences contains duplicates")
        unknown = sorted(set(values) - REGISTERED_ALLOWED_DIFFERENCES)
        if unknown:
            raise ValueError(f"unregistered allowed_differences: {', '.join(unknown)}")
        return values

    @field_validator("baseline_run_ids", "improved_run_ids")
    @classmethod
    def validate_condition_run_ids(cls, values: list[str]) -> list[str]:
        if any(not RUN_ID_RE.fullmatch(value) for value in values):
            raise ValueError("experiment condition contains an invalid Run ID")
        return values


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    description_zh: str
    description_en: str
    metric_name: str
    aggregation: Literal["sum", "mean", "median", "p95", "pooled_rate"] = Field(
        description=(
            "Aggregation over measured trials or pairs. p95 uses nearest-rank: "
            "sorted_values[ceil(0.95 * n) - 1]."
        )
    )
    unit: str
    sample_size: int = Field(gt=0)
    experiment_conditions: ExperimentConditions
    source_run_ids: list[str] = Field(min_length=1)
    generated_value: float | None = None
    generation_command: str | None = None
    status: Literal["draft", "verified", "insufficient-data"] = "draft"
    limitations: list[str] = Field(default_factory=list)

    @field_validator("claim_id")
    @classmethod
    def validate_claim_id(cls, value: str) -> str:
        if not RUN_ID_RE.fullmatch(value):
            raise ValueError("invalid claim_id")
        return value

    @field_validator("source_run_ids")
    @classmethod
    def validate_source_run_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("source_run_ids contains duplicates")
        if any(not RUN_ID_RE.fullmatch(value) for value in values):
            raise ValueError("source_run_ids contains an invalid Run ID")
        return values

    @model_validator(mode="after")
    def validate_metric(self) -> Claim:
        if self.metric_name not in REGISTERED_METRICS:
            raise ValueError(f"unregistered metric_name: {self.metric_name}")
        allowed = {
            "rate": {"pooled_rate"},
            "ab_reduction_percent": {"mean", "median", "p95"},
        }.get(self.metric_name, {"sum", "mean", "median", "p95"})
        if self.aggregation not in allowed:
            raise ValueError(
                f"aggregation {self.aggregation} is invalid for {self.metric_name}"
            )
        return self


class ClaimDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    claims: list[Claim]


def claims_schema() -> dict[str, Any]:
    schema = ClaimDocument.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return schema


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


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        decoded = json.loads(line)
        if not isinstance(decoded, dict):
            raise ValueError(f"expected JSONL objects: {path}")
        records.append(decoded)
    return records


def _safe_run_path(runs_dir: Path, run_id: str) -> Path:
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("invalid Run ID")
    root = runs_dir.resolve()
    run = (root / run_id).resolve(strict=False)
    try:
        run.relative_to(root)
    except ValueError as exc:
        raise ValueError("Run path escapes runs root") from exc
    return run


def _load_run(
    runs_dir: Path, run_id: str,
) -> tuple[dict[str, Any], dict[str, Any], Path] | None:
    run = _safe_run_path(runs_dir, run_id)
    manifest = run / "manifest.json"
    result = run / "result.json"
    if not manifest.exists() or not result.exists():
        return None
    return _read_json(manifest), _read_json(result), run


def _runtime_records(run: Path) -> list[dict[str, Any]]:
    records = _read_jsonl(run / "runtime-events.jsonl")
    return sorted(records, key=lambda item: (
        str(item.get("task_id")), str(item.get("repetition_id")),
        int(item.get("request_index", 0)),
    ))


def _identity(manifest: dict[str, Any], run: Path) -> dict[str, Any]:
    identity = {
        path: manifest.get(field) for path, field in _MANIFEST_FIELDS.items()
    }
    flags = manifest.get("feature_flags")
    if isinstance(flags, dict):
        for key, value in flags.items():
            identity[f"manifest.feature_flags.{key}"] = value
    runtime = _runtime_records(run)
    for field in _RUNTIME_FIELDS:
        identity[f"runtime.{field}"] = [item.get(field) for item in runtime]
    return identity


def _expected_conditions(conditions: ExperimentConditions) -> dict[str, Any]:
    expected = {
        path: getattr(conditions, field) for path, field in _MANIFEST_FIELDS.items()
    }
    for key, value in conditions.feature_flags.items():
        expected[f"manifest.feature_flags.{key}"] = value
    return expected


def _compatibility(
    claim: Claim,
    runs: list[tuple[dict[str, Any], dict[str, Any], Path]],
) -> tuple[bool, list[str], list[str]]:
    if not runs:
        return False, [], ["No source Runs were available."]
    allowed = set(claim.experiment_conditions.allowed_differences)
    identities = [_identity(manifest, run) for manifest, _, run in runs]
    expected = _expected_conditions(claim.experiment_conditions)
    problems: list[str] = []
    observed: list[str] = []
    for identity in identities:
        if not identity["runtime.provider"]:
            problems.append("Runtime telemetry is missing from one or more source Runs.")
            break
        undeclared_flags = {
            path for path in identity
            if path.startswith("manifest.feature_flags.")
            and path not in expected and path not in allowed
        }
        if undeclared_flags:
            problems.append(
                "Experiment conditions omit feature flags: "
                + ", ".join(sorted(undeclared_flags)) + "."
            )
        for path, value in expected.items():
            if path not in allowed and identity.get(path) != value:
                problems.append(f"Experiment condition does not match {path}.")
    all_paths = set().union(*(identity.keys() for identity in identities))
    first = identities[0]
    for path in sorted(all_paths):
        values = [identity.get(path) for identity in identities]
        if any(value != values[0] for value in values[1:]):
            observed.append(path)
            if path not in allowed:
                problems.append(f"Unapproved identity difference: {path}.")
    for identity in identities:
        manifest_provider = identity.get("manifest.provider")
        runtime_providers = identity.get("runtime.provider", [])
        if (
            "runtime.provider" not in allowed
            and any(provider != manifest_provider for provider in runtime_providers)
        ):
            problems.append("Runtime Provider does not match the effective Manifest Provider.")
    return not problems, observed, list(dict.fromkeys(problems))


def _trial_key(item: dict[str, Any]) -> tuple[str, str] | None:
    task_id, repetition_id = item.get("task_id"), item.get("repetition_id")
    if not isinstance(task_id, str) or not isinstance(repetition_id, str):
        return None
    return task_id, repetition_id


def _completed_trials(run: Path) -> dict[tuple[str, str], dict[str, Any]]:
    completed: dict[tuple[str, str], dict[str, Any]] = {}
    for event in _read_jsonl(run / "events.jsonl"):
        if event.get("type") == "trial_completed":
            key = _trial_key(event)
            if key is None or key in completed:
                raise ValueError("trial terminal events are missing identity or duplicated")
            completed[key] = event
    return completed


def _provider_token_trials(run: Path) -> dict[tuple[str, str], float]:
    usage_path = run / "usage.json"
    if not usage_path.exists():
        return {}
    requests = _read_json(usage_path).get("requests")
    if not isinstance(requests, list):
        return {}
    values: dict[tuple[str, str], float] = {}
    for item in requests:
        if not isinstance(item, dict):
            return {}
        key = _trial_key(item)
        raw = item.get("provider_usage")
        if key is None or not isinstance(raw, dict):
            return {}
        tokens = raw.get("prompt_tokens", raw.get("input_tokens"))
        if not isinstance(tokens, (int, float)):
            return {}
        values[key] = values.get(key, 0.0) + float(tokens)
    return values


def _permission_trials(run: Path) -> dict[tuple[str, str], float]:
    completed = _completed_trials(run)
    values = {key: 0.0 for key in completed}
    for event in _read_jsonl(run / "permission-events.jsonl"):
        key = _trial_key(event)
        if key is None or key not in values:
            return {}
        if event.get("hitl_required") is True:
            values[key] += 1.0
    return values


def _successful_wall_trials(run: Path) -> dict[tuple[str, str], float]:
    values: dict[tuple[str, str], float] = {}
    for key, event in _completed_trials(run).items():
        duration = event.get("duration_seconds")
        if event.get("status") == "success" and isinstance(duration, (int, float)):
            values[key] = float(duration)
    return values


def _rate_trials(run: Path) -> dict[tuple[str, str], tuple[float, float]]:
    values: dict[tuple[str, str], tuple[float, float]] = {}
    for key, event in _completed_trials(run).items():
        numerator, denominator = event.get("numerator"), event.get("denominator")
        if (
            isinstance(numerator, (int, float))
            and isinstance(denominator, (int, float)) and denominator > 0
        ):
            values[key] = float(numerator), float(denominator)
    return values


def nearest_rank_p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def _aggregate(values: list[float], aggregation: str) -> float | None:
    if not values:
        return None
    if aggregation == "sum":
        return sum(values)
    if aggregation == "mean":
        return sum(values) / len(values)
    if aggregation == "median":
        return float(median(values))
    if aggregation == "p95":
        return nearest_rank_p95(values)
    return None


def _all_trial_values(
    runs: list[tuple[dict[str, Any], dict[str, Any], Path]],
    loader,
) -> list[float] | None:
    values: list[float] = []
    for _, _, run in runs:
        measured = loader(run)
        if not measured:
            return None
        values.extend(measured.values())
    return values


def _paired_reductions(
    claim: Claim,
    runs: list[tuple[dict[str, Any], dict[str, Any], Path]],
) -> list[float] | None:
    baseline_ids = set(claim.experiment_conditions.baseline_run_ids)
    improved_ids = set(claim.experiment_conditions.improved_run_ids)
    if not baseline_ids or not improved_ids or baseline_ids & improved_ids:
        return None
    if baseline_ids | improved_ids != set(claim.source_run_ids):
        return None

    def group_values(ids: set[str]) -> dict[tuple[str, str], float] | None:
        grouped: dict[tuple[str, str], float] = {}
        for manifest, _, run in runs:
            if manifest.get("run_id") not in ids:
                continue
            for key, value in _provider_token_trials(run).items():
                if key in grouped:
                    return None
                grouped[key] = value
        return grouped or None

    baseline, improved = group_values(baseline_ids), group_values(improved_ids)
    if baseline is None or improved is None or set(baseline) != set(improved):
        return None
    values: list[float] = []
    for key in sorted(baseline):
        reduced = reduction_percent(baseline[key], improved[key])
        if reduced is None:
            return None
        values.append(reduced)
    return values


def _calculate(
    claim: Claim,
    runs: list[tuple[dict[str, Any], dict[str, Any], Path]],
) -> tuple[float | None, int]:
    if claim.metric_name == "provider_input_tokens":
        values = _all_trial_values(runs, _provider_token_trials)
        return (_aggregate(values, claim.aggregation), len(values)) if values else (None, 0)
    if claim.metric_name == "permission_hitl_count":
        values = _all_trial_values(runs, _permission_trials)
        return (_aggregate(values, claim.aggregation), len(values)) if values else (None, 0)
    if claim.metric_name == "successful_task_wall_time_seconds":
        values = _all_trial_values(runs, _successful_wall_trials)
        return (_aggregate(values, claim.aggregation), len(values)) if values else (None, 0)
    if claim.metric_name == "rate":
        pairs = [
            pair for _, _, run in runs for pair in _rate_trials(run).values()
        ]
        denominator = sum(pair[1] for pair in pairs)
        return (
            (sum(pair[0] for pair in pairs) / denominator if denominator else None),
            len(pairs),
        )
    if claim.metric_name == "ab_reduction_percent":
        values = _paired_reductions(claim, runs)
        return (_aggregate(values, claim.aggregation), len(values)) if values else (None, 0)
    return None, 0


def compile_claims(document: ClaimDocument, runs_dir: Path) -> dict[str, Any]:
    compiled: list[dict[str, Any]] = []
    for claim in document.claims:
        minimum_samples = claim.sample_size
        loaded = [_load_run(runs_dir, run_id) for run_id in claim.source_run_ids]
        usable = [run for run in loaded if run is not None]
        output = claim.model_dump(mode="json")
        output.update({
            "generated_value": None,
            "generation_command": "python -m evals.claims compile",
            "status": "insufficient-data",
            "limitations": list(claim.limitations),
        })
        observed: list[str] = []
        actual_samples = 0
        if len(usable) != len(loaded):
            output["limitations"].append(
                "One or more source Run IDs do not exist or are incomplete."
            )
        elif any(result.get("scorable") is not True for _, result, _ in usable):
            output["limitations"].append(
                "Dry-run, cancelled, infrastructure, Provider, timeout, and configuration Runs are not scorable evidence."
            )
        else:
            compatible, observed, problems = _compatibility(claim, usable)
            output["limitations"].extend(problems)
            if compatible:
                try:
                    value, actual_samples = _calculate(claim, usable)
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    value = None
                    output["limitations"].append(f"Measured artifacts are invalid: {exc}")
                if value is None:
                    output["limitations"].append(
                        "Required measured trial fields are unavailable or unbalanced."
                    )
                elif actual_samples < minimum_samples:
                    output["limitations"].append(
                        "Measured trial or pair count is below the declared minimum sample size."
                    )
                else:
                    output["generated_value"] = value
                    output["status"] = "verified"
        output["sample_size"] = actual_samples
        output["evidence_summary"] = {
            "source_run_count": len(usable),
            "measured_sample_size": actual_samples,
            "allowed_differences": claim.experiment_conditions.allowed_differences,
            "observed_differences": observed,
        }
        if observed:
            output["limitations"].append(
                "Approved identity differences remain visible in evidence_summary."
            )
        output["limitations"] = list(dict.fromkeys(output["limitations"]))
        compiled.append(output)
    return {
        "schema_version": 1,
        "claims": compiled,
        "document_hash": canonical_hash(document.model_dump(mode="json")),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile CodePaceX claims from Run artifacts")
    parser.add_argument("command", choices=["compile", "validate"])
    parser.add_argument("--claims", type=Path, default=Path("evals/claims.example.yaml"))
    parser.add_argument("--runs-dir", type=Path, default=Path("evals/.runs/pilot"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        document = load_claims(args.claims)
        compiled = compile_claims(document, args.runs_dir)
        if args.command == "validate":
            print(json.dumps({
                "valid": True, "registered_metrics": sorted(REGISTERED_METRICS),
                "schema_hash": canonical_hash(claims_schema()),
            }))
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

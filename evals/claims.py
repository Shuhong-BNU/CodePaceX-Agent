"""Compile auditable claims exclusively from measured, compatible trial artifacts."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from statistics import median
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from codepacex.experiments import ExperimentProfile, combined_runtime_hash
from evals.benchmark import RUN_ID_RE, SCORABLE_STATUSES, canonical_hash, reduction_percent

REGISTERED_METRICS = {
    "provider_input_tokens",
    "provider_output_tokens",
    "provider_cache_tokens",
    "runtime_tool_schema_bytes",
    "actual_cost_cny",
    "trial_input_tokens",
    "trial_output_tokens",
    "provider_request_count",
    "integration_conflict_count",
    "maximum_parallel_children",
    "permission_hitl_count",
    "successful_task_wall_time_seconds",
    "rate",
    "ab_reduction_percent",
    "canary_retention_rate",
    "dangerous_command_interception_rate",
    "multi_agent_task_success_rate",
    "hook_consistency_rate",
    "checkpoint_recovery_rate",
}
REGISTERED_ALLOWED_DIFFERENCES = {
    "manifest.provider", "manifest.protocol", "manifest.model_id",
    "manifest.base_url_origin", "manifest.git_commit", "manifest.prompt_version",
    "manifest.model_parameters", "manifest.timeout_seconds", "manifest.retry_budget",
    "manifest.fallback_enabled", "manifest.task_ids", "manifest.repetitions",
    "manifest.experiment_profile", "manifest.experiment_profile_hash",
    "manifest.runtime_contract_hash", "manifest.benchmark_asset_hash",
    "manifest.max_iterations",
    "runtime.provider", "runtime.protocol", "runtime.model_id",
    "runtime.system_sha256", "runtime.tools_sha256", "runtime.messages_sha256",
    "runtime.tools_bytes",
    "runtime.experiment_profile_hash", "runtime.runtime_contract_hash",
    "runtime.combined_runtime_hash",
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
    "manifest.experiment_profile": "experiment_profile",
    "manifest.experiment_profile_hash": "experiment_profile_hash",
    "manifest.runtime_contract_hash": "runtime_contract_hash",
    "manifest.benchmark_asset_hash": "benchmark_asset_hash",
    "manifest.max_iterations": "max_iterations",
}
_RUNTIME_FIELDS = (
    "provider", "protocol", "model_id", "system_sha256", "tools_sha256",
    "messages_sha256", "tools_bytes", "experiment_profile_hash", "runtime_contract_hash",
    "combined_runtime_hash",
)
_RUNTIME_SET_FIELDS = {
    "provider", "protocol", "model_id", "experiment_profile_hash",
    "runtime_contract_hash",
}
_PRICING_SNAPSHOT_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


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
    experiment_profile: dict[str, Any]
    experiment_profile_hash: str
    runtime_contract_hash: str
    benchmark_asset_hash: str
    max_iterations: int = Field(gt=0)
    pricing_snapshot_hash: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$",
        description="Required provenance for actual_cost_cny Claims only.",
    )
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
    experiment_conditions: ExperimentConditions | None = None
    source_run_ids: list[str] = Field(default_factory=list)
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
            "canary_retention_rate": {"pooled_rate"},
            "dangerous_command_interception_rate": {"pooled_rate"},
            "multi_agent_task_success_rate": {"pooled_rate"},
            "hook_consistency_rate": {"pooled_rate"},
            "checkpoint_recovery_rate": {"pooled_rate"},
            "ab_reduction_percent": {"mean", "median", "p95"},
        }.get(self.metric_name, {"sum", "mean", "median", "p95"})
        if self.aggregation not in allowed:
            raise ValueError(
                f"aggregation {self.aggregation} is invalid for {self.metric_name}"
            )
        if self.status == "insufficient-data" and not self.source_run_ids:
            if self.experiment_conditions is not None:
                raise ValueError("no-evidence insufficient-data claims cannot declare conditions")
            if self.generated_value is not None:
                raise ValueError("no-evidence insufficient-data claims cannot have a value")
            return self
        if self.experiment_conditions is None or not self.source_run_ids:
            raise ValueError("measured claims require conditions and source Run IDs")
        return self


class ClaimDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
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
        values = [item.get(field) for item in runtime]
        identity[f"runtime.{field}"] = (
            sorted(set(values), key=lambda value: str(value))
            if field in _RUNTIME_SET_FIELDS else values
        )
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
    for manifest, result, run in runs:
        if manifest.get("schema_version") != 2 or result.get("schema_version") != 2:
            problems.append(
                "Schema v1 and unversioned Runs are inspection-only and cannot verify Goal 2 claims."
            )
            continue
        if result.get("scorable") is not True:
            problems.append(
                "Source Run is unscorable; infrastructure-error trials cannot enter formal Claims."
            )
            continue
        flags = manifest.get("feature_flags")
        if flags:
            problems.append("Legacy feature_flags are not eligible for Goal 2 claims.")
        try:
            profile = ExperimentProfile.model_validate(manifest.get("experiment_profile"))
        except (ValueError, TypeError):
            problems.append("Manifest experiment_profile is missing or invalid.")
            continue
        if profile.profile_hash() != manifest.get("experiment_profile_hash"):
            problems.append("Manifest experiment_profile_hash is invalid.")
        if profile.runtime_contract_hash() != manifest.get("runtime_contract_hash"):
            problems.append("Manifest runtime_contract_hash is invalid.")
        for record in _runtime_records(run):
            if record.get("experiment_profile_hash") != manifest.get("experiment_profile_hash"):
                problems.append("Runtime experiment_profile_hash does not match Manifest.")
            if record.get("runtime_contract_hash") != manifest.get("runtime_contract_hash"):
                problems.append("Runtime contract hash does not match Manifest.")
            expected_combined = combined_runtime_hash(
                profile_hash=profile.profile_hash(),
                system_sha256=str(record.get("system_sha256")),
                tools_sha256=str(record.get("tools_sha256")),
            )
            if record.get("combined_runtime_hash") != expected_combined:
                problems.append("Combined runtime hash is invalid.")
    for identity in identities:
        if not identity["runtime.provider"]:
            problems.append("Runtime telemetry is missing from one or more source Runs.")
            break
        if any(path.startswith("manifest.feature_flags.") for path in identity):
            problems.append(
                "Non-empty feature_flags have no approved runtime mapping and cannot verify a claim."
            )
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
    if claim.metric_name == "actual_cost_cny":
        pricing_hashes = [manifest.get("pricing_snapshot_hash") for manifest, _, _ in runs]
        if any(
            not isinstance(value, str)
            or _PRICING_SNAPSHOT_HASH_RE.fullmatch(value) is None
            for value in pricing_hashes
        ):
            problems.append(
                "Cost Claim source Runs are missing valid pricing_snapshot_hash provenance."
            )
        elif len(set(pricing_hashes)) != 1:
            observed.append("manifest.pricing_snapshot_hash")
            problems.append(
                "Cost Claim source Runs use different pricing_snapshot_hash values."
            )
        elif (
            claim.experiment_conditions.pricing_snapshot_hash is not None
            and claim.experiment_conditions.pricing_snapshot_hash != pricing_hashes[0]
        ):
            problems.append(
                "Cost Claim conditions do not match manifest.pricing_snapshot_hash."
            )
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


def _provider_usage_trials(
    run: Path, metric: Literal["input", "output", "cache"],
) -> dict[tuple[str, str], float]:
    usage_path = run / "usage.json"
    if not usage_path.exists():
        return {}
    requests = _read_json(usage_path).get("requests")
    if not isinstance(requests, list):
        return {}
    values: dict[tuple[str, str], float] = {}
    terminals = _completed_trials(run)
    for item in requests:
        if not isinstance(item, dict):
            return {}
        key = _trial_key(item)
        raw = item.get("provider_usage")
        if key is None or not isinstance(raw, dict):
            return {}
        terminal = terminals.get(key)
        if terminal is not None and terminal.get("status") == "infrastructure_error":
            # A missing-Usage reconciliation cannot be paired with token data.
            # Keep its cost in the ledger, but exclude it from token metrics.
            continue
        if metric == "input":
            tokens = raw.get("prompt_tokens", raw.get("input_tokens"))
        elif metric == "output":
            tokens = raw.get("completion_tokens", raw.get("output_tokens"))
        else:
            details = raw.get("prompt_tokens_details", raw.get("input_tokens_details", {}))
            tokens = raw.get("cache_read_input_tokens")
            if tokens is None and isinstance(details, dict):
                tokens = details.get("cached_tokens", details.get("cache_read_tokens"))
            if tokens is None:
                tokens = 0
        if not isinstance(tokens, (int, float)):
            return {}
        values[key] = values.get(key, 0.0) + float(tokens)
    return values


def _provider_token_trials(run: Path) -> dict[tuple[str, str], float]:
    return _provider_usage_trials(run, "input")


def _runtime_tool_schema_trials(run: Path) -> dict[tuple[str, str], float]:
    values: dict[tuple[str, str], float] = {}
    for item in _runtime_records(run):
        key = _trial_key(item)
        value = item.get("tools_bytes")
        if key is None or not isinstance(value, int) or value < 0:
            return {}
        values.setdefault(key, float(value))
    return values


def _actual_cost_trials(run: Path) -> dict[tuple[str, str], float]:
    values: dict[tuple[str, str], float] = {}
    for key, event in _completed_trials(run).items():
        try:
            value = float(event["actual_cny"])
        except (KeyError, TypeError, ValueError):
            return {}
        if value < 0:
            return {}
        values[key] = value
    return values


def _terminal_numeric_trials(
    run: Path, field: str, *, grade_field: bool = False,
) -> dict[tuple[str, str], float]:
    values: dict[tuple[str, str], float] = {}
    for key, event in _completed_trials(run).items():
        source = event.get("grade") if grade_field else event
        if not isinstance(source, dict):
            return {}
        raw = source.get(field)
        if isinstance(raw, bool):
            raw = int(raw)
        if not isinstance(raw, (int, float)) or raw < 0:
            return {}
        values[key] = float(raw)
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
        if event.get("status") not in SCORABLE_STATUSES:
            continue
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
            try:
                completed = _completed_trials(run)
            except (OSError, ValueError, json.JSONDecodeError):
                return None
            values = _provider_token_trials(run)
            if set(completed) != set(values):
                return None
            for key, terminal in completed.items():
                # The conservative Pilot v1 selection rule is deliberately
                # simple: one terminal, successful Attempt per pair.  Resumed
                # Trials with multiple terminal attempts are insufficient data
                # until a later study defines an explicit selection protocol.
                if terminal.get("status") != "success" or key in grouped:
                    return None
            for key, value in values.items():
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
    if claim.metric_name == "provider_output_tokens":
        values = _all_trial_values(runs, lambda run: _provider_usage_trials(run, "output"))
        return (_aggregate(values, claim.aggregation), len(values)) if values else (None, 0)
    if claim.metric_name == "provider_cache_tokens":
        values = _all_trial_values(runs, lambda run: _provider_usage_trials(run, "cache"))
        return (_aggregate(values, claim.aggregation), len(values)) if values else (None, 0)
    if claim.metric_name == "runtime_tool_schema_bytes":
        values = _all_trial_values(runs, _runtime_tool_schema_trials)
        return (_aggregate(values, claim.aggregation), len(values)) if values else (None, 0)
    if claim.metric_name == "actual_cost_cny":
        values = _all_trial_values(runs, _actual_cost_trials)
        return (_aggregate(values, claim.aggregation), len(values)) if values else (None, 0)
    terminal_metrics = {
        "trial_input_tokens": ("input_tokens", False),
        "trial_output_tokens": ("output_tokens", False),
        "provider_request_count": ("provider_request_count", False),
        "integration_conflict_count": ("integration_conflict_markers", True),
        "maximum_parallel_children": ("maximum_parallel_children", True),
    }
    if claim.metric_name in terminal_metrics:
        field, grade_field = terminal_metrics[claim.metric_name]
        values = _all_trial_values(
            runs, lambda run: _terminal_numeric_trials(
                run, field, grade_field=grade_field,
            ),
        )
        return (_aggregate(values, claim.aggregation), len(values)) if values else (None, 0)
    if claim.metric_name == "permission_hitl_count":
        values = _all_trial_values(runs, _permission_trials)
        return (_aggregate(values, claim.aggregation), len(values)) if values else (None, 0)
    if claim.metric_name == "successful_task_wall_time_seconds":
        values = _all_trial_values(runs, _successful_wall_trials)
        return (_aggregate(values, claim.aggregation), len(values)) if values else (None, 0)
    if claim.metric_name in {
        "rate", "canary_retention_rate", "dangerous_command_interception_rate",
        "multi_agent_task_success_rate", "hook_consistency_rate",
        "checkpoint_recovery_rate",
    }:
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
        if claim.status == "insufficient-data" and not claim.source_run_ids:
            output = claim.model_dump(mode="json")
            output.update({
                "generated_value": None,
                "generation_command": "python -m evals.claims compile",
                "status": "insufficient-data",
                "limitations": list(dict.fromkeys(claim.limitations)),
                "evidence_summary": {
                    "source_run_count": 0,
                    "measured_sample_size": 0,
                    "allowed_differences": [],
                    "observed_differences": [],
                },
            })
            compiled.append(output)
            continue
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
                if claim.metric_name == "actual_cost_cny":
                    output["experiment_conditions"]["pricing_snapshot_hash"] = usable[0][0]["pricing_snapshot_hash"]
                try:
                    value, actual_samples = _calculate(claim, usable)
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    value = None
                    output["limitations"].append(f"Measured artifacts are invalid: {exc}")
                if value is None:
                    output["limitations"].append(
                        "Required measured trial fields are unavailable or unbalanced."
                    )
                elif actual_samples != minimum_samples:
                    output["limitations"].append(
                        "Declared sample_size does not exactly match the measured trial or pair count."
                    )
                else:
                    output["generated_value"] = value
                    output["status"] = "verified"
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
        "schema_version": 2,
        "claims": compiled,
        "document_hash": canonical_hash(document.model_dump(mode="json")),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile CodePaceX claims from Run artifacts")
    parser.add_argument("command", choices=["compile", "validate", "schema"])
    parser.add_argument("--claims", type=Path, default=Path("evals/claims.example.yaml"))
    parser.add_argument("--runs-dir", type=Path, default=Path("evals/.runs/pilot"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "schema":
            payload = json.dumps(claims_schema(), ensure_ascii=False, indent=2) + "\n"
            if args.output:
                args.output.write_text(payload, encoding="utf-8")
            else:
                print(payload, end="")
            return 0
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

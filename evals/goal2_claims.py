"""Generate fail-closed Claims declarations from frozen Goal 2 Run manifests."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from evals.claims import ClaimDocument
from evals.mcp_cohort import load_mcp_cohort, summarize_mcp_cohort


@dataclass(frozen=True)
class ClaimSpec:
    claim_id: str
    metric: str
    aggregation: str
    unit: str
    sample_size: int
    run_ids: tuple[str, ...]
    description: str
    allowed_differences: tuple[str, ...] = ()
    baseline_run_ids: tuple[str, ...] = ()
    improved_run_ids: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


MCP_RUNTIME_DIFFERENCES = (
    "manifest.experiment_profile", "manifest.experiment_profile_hash",
    "manifest.runtime_contract_hash", "runtime.system_sha256",
    "runtime.tools_sha256", "runtime.messages_sha256", "runtime.tools_bytes",
    "runtime.experiment_profile_hash", "runtime.runtime_contract_hash",
    "runtime.combined_runtime_hash",
)
def _specs(*, include_multi: bool = True) -> list[ClaimSpec]:
    specs: list[ClaimSpec] = []
    mcp_runs = ("mcp-formal-eager", "mcp-formal-deferred")
    for aggregation in ("median", "p95"):
        specs.append(ClaimSpec(
            f"mcp-input-reduction-{aggregation}", "ab_reduction_percent",
            aggregation, "percent", 150, mcp_runs,
            f"MCP deferred tool loading input-token reduction ({aggregation})",
            MCP_RUNTIME_DIFFERENCES,
            ("mcp-formal-eager",), ("mcp-formal-deferred",),
            ("Controlled local MCP corpus; no production MCP traffic.",),
        ))
    for arm in ("eager", "deferred"):
        run = (f"mcp-formal-{arm}",)
        for metric, aggregation, unit in (
            ("provider_input_tokens", "sum", "tokens"),
            ("provider_output_tokens", "sum", "tokens"),
            ("provider_cache_tokens", "sum", "tokens"),
            ("runtime_tool_schema_bytes", "median", "bytes"),
            ("rate", "pooled_rate", "ratio"),
            ("actual_cost_cny", "sum", "CNY"),
        ):
            specs.append(ClaimSpec(
                f"mcp-{arm}-{metric}", metric, aggregation, unit, 150, run,
                f"MCP {arm} measured {metric}",
                limitations=("Controlled local MCP corpus.",),
            ))
    for arm in ("summary_only", "recovery_v1"):
        specs.append(ClaimSpec(
            f"retention-{arm}-exact-rate", "canary_retention_rate",
            "pooled_rate", "ratio", 10, (f"retention-formal-{arm}",),
            f"Exact ordered canary retention for {arm}",
            limitations=("Transcript load is deterministic synthetic filler.",),
        ))
    for strategy in ("default", "session_allow", "explicit_rules", "sandbox_auto_allow"):
        run = (f"permission-formal-{strategy}",)
        for aggregation in ("mean", "median", "p95"):
            specs.append(ClaimSpec(
                f"permission-{strategy}-hitl-{aggregation}", "permission_hitl_count",
                aggregation, "count", 50, run,
                f"Permission HITL count for {strategy} ({aggregation})",
            ))
        specs.append(ClaimSpec(
            f"permission-{strategy}-dangerous-interception",
            "dangerous_command_interception_rate", "pooled_rate", "ratio", 15, run,
            f"Dangerous-operation interception rate for {strategy}",
        ))
    if include_multi:
        for mode in ("single", "multi"):
            run = (f"multi-formal-{mode}",)
            for metric, aggregation, unit in (
                ("multi_agent_task_success_rate", "pooled_rate", "ratio"),
                ("successful_task_wall_time_seconds", "median", "seconds"),
                ("successful_task_wall_time_seconds", "p95", "seconds"),
                ("trial_input_tokens", "sum", "tokens"),
                ("trial_output_tokens", "sum", "tokens"),
                ("provider_request_count", "sum", "requests"),
                ("actual_cost_cny", "sum", "CNY"),
                ("integration_conflict_count", "sum", "count"),
                ("maximum_parallel_children", "p95", "workers"),
            ):
                specs.append(ClaimSpec(
                    f"multi-{mode}-{metric}-{aggregation}", metric, aggregation, unit, 25,
                    run, f"Cross-file {mode} mode measured {metric} ({aggregation})",
                    limitations=("Controlled five-task cross-file fixture corpus.",),
                ))
    return specs


def _manifest(runs_dir: Path, run_id: str) -> dict[str, Any]:
    path = (runs_dir / run_id / "manifest.json").resolve()
    try:
        path.relative_to(runs_dir.resolve())
    except ValueError as exc:
        raise ValueError("Goal 2 Run path escapes runs root") from exc
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 2:
        raise ValueError(f"Goal 2 Run manifest is missing or unsupported: {run_id}")
    if payload.get("run_id") != run_id:
        raise ValueError(f"Goal 2 Run ID mismatch: {run_id}")
    return payload


def _conditions(
    manifest: dict[str, Any], spec: ClaimSpec,
) -> dict[str, Any]:
    return {
        "provider": manifest.get("provider"), "protocol": manifest.get("protocol"),
        "model_id": manifest.get("model_id"),
        "base_url_origin": manifest.get("base_url_origin"),
        "git_commit": manifest.get("git_commit"),
        "prompt_version": manifest.get("prompt_version"),
        "model_parameters": manifest.get("model_parameters"),
        "timeout_seconds": manifest.get("timeout_seconds"),
        "retry_budget": manifest.get("retry_budget"),
        "fallback_enabled": manifest.get("fallback_enabled"),
        "task_ids": manifest.get("task_ids"), "repetitions": manifest.get("repetitions"),
        "feature_flags": manifest.get("feature_flags"),
        "experiment_profile": manifest.get("experiment_profile"),
        "experiment_profile_hash": manifest.get("experiment_profile_hash"),
        "runtime_contract_hash": manifest.get("runtime_contract_hash"),
        "benchmark_asset_hash": manifest.get("benchmark_asset_hash"),
        "max_iterations": manifest.get("max_iterations"),
        "allowed_differences": list(spec.allowed_differences),
        "baseline_run_ids": list(spec.baseline_run_ids),
        "improved_run_ids": list(spec.improved_run_ids),
    }


def generate_claim_document(
    runs_dir: Path, *, include_multi: bool = True,
) -> ClaimDocument:
    claims: list[dict[str, Any]] = []
    for spec in _specs(include_multi=include_multi):
        manifests = [_manifest(runs_dir, run_id) for run_id in spec.run_ids]
        claims.append({
            "claim_id": spec.claim_id,
            "description_zh": spec.description,
            "description_en": spec.description,
            "metric_name": spec.metric, "aggregation": spec.aggregation,
            "unit": spec.unit, "sample_size": spec.sample_size,
            "experiment_conditions": _conditions(manifests[0], spec),
            "source_run_ids": list(spec.run_ids), "status": "draft",
            "limitations": list(spec.limitations),
        })
    claims.append({
        "claim_id": "long-session-formal-checkpoint-recovery",
        "description_zh": "三次 8 小时正式长会话 checkpoint recovery",
        "description_en": "Three 8-hour formal long-session checkpoint recoveries",
        "metric_name": "checkpoint_recovery_rate", "aggregation": "pooled_rate",
        "unit": "ratio", "sample_size": 3, "experiment_conditions": None,
        "source_run_ids": [], "status": "insufficient-data",
        "limitations": [
            "Deferred to a follow-up Goal; no 8-hour formal session was run.",
            "long-pilot-1 is a 2-hour diagnostic Pilot and is excluded from this formal Claim.",
        ],
    })
    return ClaimDocument.model_validate({"schema_version": 2, "claims": claims})


def generate_mcp_evidence(*, cohort_index: Path, runs_dir: Path) -> dict[str, Any]:
    """Compile MCP Claim evidence from the frozen Trial-level cohort.

    This intentionally does not use the monolithic deferred Run's top-level
    ``scorable`` flag: the retained mcp_one_08/1 infrastructure error excludes
    only its Token pair while remaining visible in counts and cost.
    """
    cohort = load_mcp_cohort(cohort_index)
    metrics = summarize_mcp_cohort(cohort, runs_dir)
    return {
        "schema_version": 1,
        "evidence_kind": "goal2-mcp-trial-level-cohort",
        "cohort_index_sha256": cohort["sha256"],
        "source_manifest_sha256": cohort["source_manifest_sha256"],
        "ledger_sha256": cohort["ledger_sha256"],
        "summary": cohort["summary"],
        "metrics": metrics,
        "limitations": [
            "Controlled local MCP corpus; no production MCP traffic.",
            "mcp_one_08/1 remains an infrastructure error with unknown final Provider Usage; it is retained in counts/cost and excluded from Token pairs.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Goal 2 Claims from Run manifests")
    parser.add_argument("command", choices=["generate", "mcp-evidence"])
    parser.add_argument("--runs-dir", type=Path, default=Path("evals/.runs/goal2"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exclude-multi", action="store_true")
    parser.add_argument("--cohort-index", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "mcp-evidence":
            if args.cohort_index is None:
                raise ValueError("mcp-evidence requires --cohort-index")
            evidence = generate_mcp_evidence(
                cohort_index=args.cohort_index, runs_dir=args.runs_dir,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            print(args.output)
            return 0
        document = generate_claim_document(args.runs_dir, include_multi=not args.exclude_multi)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            yaml.safe_dump(document.model_dump(mode="json"), sort_keys=False),
            encoding="utf-8",
        )
        print(args.output)
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Goal 2 Claims generation error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

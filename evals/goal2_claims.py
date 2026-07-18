"""Generate fail-closed Claims declarations from frozen Goal 2 Run manifests."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from evals.claims import ClaimDocument, compile_claims, load_claims
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


def _mcp_measurement(claim_id: str, metrics: dict[str, Any]) -> tuple[float | None, int]:
    """Return the exact Trial-level measurement for a registered MCP Claim."""
    if claim_id == "mcp-input-reduction-median":
        return float(metrics["input_reduction_percent"]["median"]), int(metrics["valid_matched_pairs"])
    if claim_id == "mcp-input-reduction-p95":
        return float(metrics["input_reduction_percent"]["p95"]), int(metrics["valid_matched_pairs"])
    parts = claim_id.split("-")
    if len(parts) < 3 or parts[0] != "mcp" or parts[1] not in {"eager", "deferred"}:
        return None, 0
    arm, metric = parts[1], "-".join(parts[2:])
    summary = metrics["arms"][arm]
    if metric == "provider_input_tokens":
        return float(summary["input_tokens"]), int(summary["usage_complete_trials"])
    if metric == "provider_output_tokens":
        return float(summary["output_tokens"]), int(summary["usage_complete_trials"])
    if metric == "provider_cache_tokens":
        return float(summary["cache_tokens"]), int(summary["usage_complete_trials"])
    if metric == "runtime_tool_schema_bytes":
        value = summary["runtime_tool_schema_bytes_median"]
        return (float(value), int(summary["runtime_tool_schema_sample_size"])) if value is not None else (None, 0)
    if metric == "rate":
        value = summary["rate"]
        return (float(value), int(summary["rate_trial_count"])) if value is not None else (None, 0)
    if metric == "actual_cost_cny":
        return float(summary["settled_cny"]), int(summary["terminal_trials"])
    return None, 0


def _mcp_source_summary(cohort: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    source_runs = metrics["source_runs"]
    source_run_arms: dict[str, str] = {}
    for entry in cohort["entries"]:
        run_id, arm = str(entry["run_id"]), str(entry["arm"])
        previous = source_run_arms.setdefault(run_id, arm)
        if previous != arm:
            raise ValueError("MCP cohort source Run occurs in more than one arm")
    def values_by_arm(field: str) -> dict[str, list[str]]:
        return {
            arm: sorted({str(source_runs[run_id][field]) for run_id, value in source_run_arms.items() if value == arm})
            for arm in ("eager", "deferred")
        }
    return {
        "trial_level_cohort": True,
        "cohort_index_sha256": cohort["sha256"],
        "source_manifest_sha256": cohort["source_manifest_sha256"],
        "ledger_sha256": cohort["ledger_sha256"],
        "cohort_source_run_ids": sorted(source_runs),
        "cohort_source_run_arms": source_run_arms,
        "cohort_execution_commits": sorted({str(item["git_commit"]) for item in source_runs.values()}),
        "cohort_profile_hashes": sorted({str(item["experiment_profile_hash"]) for item in source_runs.values()}),
        "cohort_profile_hashes_by_arm": values_by_arm("experiment_profile_hash"),
        "cohort_runtime_contract_hashes": sorted({str(item["runtime_contract_hash"]) for item in source_runs.values()}),
        "cohort_runtime_contract_hashes_by_arm": values_by_arm("runtime_contract_hash"),
        "cohort_benchmark_asset_hashes": sorted({str(item["benchmark_asset_hash"]) for item in source_runs.values()}),
    }


def _mcp_claim_compatibility_problems(
    declared: Any, source_summary: dict[str, Any],
) -> list[str]:
    """Check that a MCP Claim still names the cohort it is allowed to use."""
    claim_id = declared.claim_id
    if claim_id.startswith("mcp-input-reduction-"):
        expected_run_ids = {
            "mcp-formal-run-scoped-eager", "mcp-formal-run-scoped-deferred",
        }
        required_arms = {"eager"}
    else:
        parts = claim_id.split("-", 2)
        if len(parts) != 3 or parts[1] not in {"eager", "deferred"}:
            return ["MCP Claim ID is not registered for Trial-level compilation."]
        arm = parts[1]
        expected_run_ids = {f"mcp-formal-run-scoped-{arm}"}
        required_arms = {arm}
    if set(declared.source_run_ids) != expected_run_ids:
        return ["MCP Claim source Run IDs do not match the frozen cohort aliases."]
    source_runs = source_summary["cohort_source_run_arms"]
    selected = [
        run_id for run_id, arm in source_runs.items() if arm in required_arms
    ]
    if not selected:
        return ["MCP Claim has no selected Trial-level cohort sources."]
    conditions = declared.experiment_conditions
    if any(commit != conditions.git_commit for commit in source_summary["cohort_execution_commits"]):
        return ["MCP cohort execution commit does not match Claim conditions."]
    source_profiles = source_summary["cohort_profile_hashes_by_arm"]
    source_contracts = source_summary["cohort_runtime_contract_hashes_by_arm"]
    problems: list[str] = []
    for arm in required_arms:
        if source_profiles.get(arm) != [conditions.experiment_profile_hash]:
            problems.append("MCP cohort profile hash does not match Claim conditions.")
        if source_contracts.get(arm) != [conditions.runtime_contract_hash]:
            problems.append("MCP cohort runtime contract hash does not match Claim conditions.")
    if source_summary["cohort_benchmark_asset_hashes"] != [conditions.benchmark_asset_hash]:
        problems.append("MCP cohort benchmark asset hash does not match Claim conditions.")
    return problems


def compile_goal2_claims(
    document: ClaimDocument, runs_dir: Path, *, cohort_index: Path,
) -> dict[str, Any]:
    """Compile Goal 2 Claims, replacing only MCP with its frozen trial cohort.

    Generic Claims remain Run-level and fail closed on an unscorable Run.  The
    MCP formal cohort deliberately has one terminal infrastructure error in a
    mixed-status deferred collection, so it is validated Trial-by-Trial here.
    """
    compiled = compile_claims(document, runs_dir)
    cohort = load_mcp_cohort(cohort_index)
    metrics = summarize_mcp_cohort(cohort, runs_dir)
    source_summary = _mcp_source_summary(cohort, metrics)
    for declared, output in zip(document.claims, compiled["claims"], strict=True):
        if not declared.claim_id.startswith("mcp-"):
            continue
        output.update({
            "generated_value": None,
            "generation_command": "python -m evals.goal2_claims compile",
            "status": "insufficient-data",
            "limitations": list(declared.limitations),
            "evidence_summary": {
                "source_run_count": len(source_summary["cohort_source_run_ids"]),
                "measured_sample_size": 0,
                "allowed_differences": declared.experiment_conditions.allowed_differences,
                "observed_differences": [],
                **source_summary,
            },
        })
        problems = _mcp_claim_compatibility_problems(declared, source_summary)
        value, sample_size = _mcp_measurement(declared.claim_id, metrics)
        output["evidence_summary"]["measured_sample_size"] = sample_size
        if problems:
            output["limitations"].extend(problems)
        elif value is None:
            output["limitations"].append(
                "Required Trial-level runtime telemetry is unavailable."
            )
        elif sample_size != declared.sample_size:
            output["limitations"].append(
                "Declared sample_size does not exactly match the measured Trial or pair count."
            )
        else:
            output["generated_value"] = value
            output["status"] = "verified"
        output["limitations"] = list(dict.fromkeys(output["limitations"]))
    return compiled
def _specs(
    *, include_multi: bool = True,
    mcp_run_ids: tuple[str, str] = ("mcp-formal-eager", "mcp-formal-deferred"),
    mcp_summary: dict[str, Any] | None = None,
) -> list[ClaimSpec]:
    specs: list[ClaimSpec] = []
    mcp_runs = mcp_run_ids
    mcp_pair_count = int((mcp_summary or {}).get("valid_matched_pairs", 150))
    mcp_usage_complete = int((mcp_summary or {}).get("usage_complete_trials", 300))
    for aggregation in ("median", "p95"):
        specs.append(ClaimSpec(
            f"mcp-input-reduction-{aggregation}", "ab_reduction_percent",
            aggregation, "percent", mcp_pair_count, mcp_runs,
            f"MCP deferred tool loading input-token reduction ({aggregation})",
            MCP_RUNTIME_DIFFERENCES,
            (mcp_runs[0],), (mcp_runs[1],),
            ("Controlled local MCP corpus; no production MCP traffic.",),
        ))
    for arm, run_id in zip(("eager", "deferred"), mcp_runs, strict=True):
        run = (run_id,)
        for metric, aggregation, unit in (
            ("provider_input_tokens", "sum", "tokens"),
            ("provider_output_tokens", "sum", "tokens"),
            ("provider_cache_tokens", "sum", "tokens"),
            ("runtime_tool_schema_bytes", "median", "bytes"),
            ("rate", "pooled_rate", "ratio"),
            ("actual_cost_cny", "sum", "CNY"),
        ):
            sample_size = 150
            if mcp_summary is not None and metric in {
                "provider_input_tokens", "provider_output_tokens", "provider_cache_tokens",
            }:
                # The one deferred infrastructure error is retained in cost/counts
                # but excluded from Usage-dependent Token metrics.
                sample_size = 150 if arm == "eager" else mcp_usage_complete - 150
            specs.append(ClaimSpec(
                f"mcp-{arm}-{metric}", metric, aggregation, unit, sample_size, run,
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
    cohort_index: Path | None = None,
) -> ClaimDocument:
    mcp_run_ids = ("mcp-formal-eager", "mcp-formal-deferred")
    mcp_summary: dict[str, Any] | None = None
    if cohort_index is not None:
        cohort = load_mcp_cohort(cohort_index)
        mcp_run_ids = (
            "mcp-formal-run-scoped-eager",
            "mcp-formal-run-scoped-deferred",
        )
        mcp_summary = dict(cohort["summary"])
    claims: list[dict[str, Any]] = []
    retention_partial = any(
        not (runs_dir / run_id / "manifest.json").is_file()
        for run_id in ("retention-formal-summary_only", "retention-formal-recovery_v1")
    )
    for spec in _specs(
        include_multi=include_multi, mcp_run_ids=mcp_run_ids,
        mcp_summary=mcp_summary,
    ):
        if retention_partial and spec.claim_id.startswith("retention-"):
            continue
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
    if retention_partial:
        retained_run = "retention-formal-summary_only"
        retained_manifest = _manifest(runs_dir, retained_run)
        retained_spec = ClaimSpec(
            "retention-formal-partial", "canary_retention_rate", "pooled_rate",
            "ratio", 1, (retained_run,), "Retention formal partial evidence",
        )
        claims.append({
            "claim_id": "retention-formal-partial",
            "description_zh": "Retention 正式实验为可审计 partial，不能声明 profile 比较结果",
            "description_en": "Retention formal evidence is partial; no profile comparison is claimed.",
            "metric_name": "canary_retention_rate", "aggregation": "pooled_rate",
            "unit": "ratio", "sample_size": 1,
            "experiment_conditions": _conditions(retained_manifest, retained_spec),
            "source_run_ids": [retained_run], "status": "insufficient-data",
            "limitations": [
                "recovery_v1 formal Run was not executed.",
                "summary_only session-01 is terminal infrastructure_error with unknown final Provider Usage and is retained for audit only.",
            ],
        })
    if not include_multi:
        claims.append({
            "claim_id": "multi-agent-formal-no-go",
            "description_zh": "Multi-Agent 正式实验缺少可复核的零模型 gate 证据",
            "description_en": "Multi-Agent formal study lacks reviewable zero-model gate evidence.",
            "metric_name": "multi_agent_task_success_rate", "aggregation": "pooled_rate",
            "unit": "ratio", "sample_size": 50, "experiment_conditions": None,
            "source_run_ids": [], "status": "insufficient-data",
            "limitations": [
                "The historical zero-model Multi-Agent gate is evidence_insufficient after the runtime-artifact scope erratum.",
                "No formal Multi-Agent Provider Trial was run; no comparative effect is claimed.",
            ],
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
    parser.add_argument("command", choices=["generate", "compile", "mcp-evidence"])
    parser.add_argument("--runs-dir", type=Path, default=Path("evals/.runs/goal2"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--claims", type=Path)
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
        if args.command == "compile":
            if args.cohort_index is None:
                raise ValueError("compile requires --cohort-index")
            if args.claims is None:
                raise ValueError("compile requires --claims")
            compiled = compile_goal2_claims(
                load_claims(args.claims), args.runs_dir,
                cohort_index=args.cohort_index,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                yaml.safe_dump(compiled, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            print(args.output)
            return 0
        document = generate_claim_document(
            args.runs_dir, include_multi=not args.exclude_multi,
            cohort_index=args.cohort_index,
        )
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

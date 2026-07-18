"""Validate the Trial-level MCP formal cohort used by Goal 2 Claims."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"expected JSONL object: {path}")
        records.append(value)
    return records


def _run_path(runs_dir: Path, run_id: str) -> Path:
    root = runs_dir.resolve()
    run = (root / run_id).resolve(strict=False)
    try:
        run.relative_to(root)
    except ValueError as exc:
        raise ValueError("MCP cohort Run path escapes runs root") from exc
    return run


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _nearest_rank_p95(values: list[float]) -> float:
    if not values:
        raise ValueError("cannot calculate p95 from no values")
    return sorted(values)[math.ceil(0.95 * len(values)) - 1]


def load_mcp_cohort(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("MCP cohort index has an unsupported schema")
    expected = value.get("sha256")
    payload = dict(value)
    payload.pop("sha256", None)
    if expected != canonical_hash(payload):
        raise ValueError("MCP cohort index hash mismatch")
    entries = value.get("entries")
    if not isinstance(entries, list) or len(entries) != 300:
        raise ValueError("MCP cohort must contain exactly 300 Trial entries")
    identities = {(item.get("arm"), item.get("task_id"), item.get("repetition_id")) for item in entries}
    if len(identities) != len(entries):
        raise ValueError("MCP cohort contains duplicate arm/Trial identities")
    deferred_error = [item for item in entries if (
        item.get("arm"), item.get("task_id"), item.get("repetition_id")
    ) == ("deferred", "mcp_one_08", "1")]
    if len(deferred_error) != 1 or deferred_error[0].get("terminal_status") != "infrastructure_error":
        raise ValueError("MCP cohort does not retain mcp_one_08/1 as infrastructure_error")
    if deferred_error[0].get("usage_complete") is not False:
        raise ValueError("MCP cohort incorrectly treats mcp_one_08/1 Usage as complete")
    if deferred_error[0].get("token_pair_exclusion") != "infrastructure_error_usage_unknown":
        raise ValueError("MCP cohort lacks the required mcp_one_08/1 token exclusion")
    if deferred_error[0].get("request_charge_count") != 2 or deferred_error[0].get("settlement_count") != 3:
        raise ValueError("MCP cohort does not retain the conservative mcp_one_08/1 settlement")
    summary = value.get("summary")
    if not isinstance(summary, dict) or summary.get("valid_matched_pairs") != 149:
        raise ValueError("MCP cohort valid matched-pair count is not 149")
    if summary.get("usage_complete_trials") != 299:
        raise ValueError("MCP cohort Usage-complete Trial count is not 299")
    return value


def summarize_mcp_cohort(cohort: dict[str, Any], runs_dir: Path) -> dict[str, Any]:
    """Calculate MCP evidence from Trial entries, never a Run-level verdict.

    The index is a frozen selection list, not a substitute for the source
    artifacts.  This function re-reads every selected Trial and fails closed
    if its terminal event, Usage, amount, or provenance no longer agrees with
    that list.  In particular, it does not inherit the old deferred Run's
    aggregate ``scorable`` flag: one retained infrastructure error excludes
    only the measurements it cannot support.
    """
    by_arm: dict[str, dict[str, Any]] = {
        arm: {"planned_trials": 0, "terminal_trials": 0, "success": 0,
              "task_failure": 0, "infrastructure_error": 0,
              "usage_complete_trials": 0, "input_tokens": 0,
              "output_tokens": 0, "cache_tokens": 0, "reasoning_tokens": 0,
              "settled_cny": 0.0, "runtime_tool_schema_bytes": [],
              "rate_numerator": 0.0, "rate_denominator": 0.0,
              "rate_trial_count": 0}
        for arm in ("eager", "deferred")
    }
    trial_tokens: dict[tuple[str, str, str], int] = {}
    source_runs: dict[str, dict[str, Any]] = {}
    for entry in cohort["entries"]:
        arm = str(entry["arm"])
        summary = by_arm[arm]
        task_id, repetition_id = str(entry["task_id"]), str(entry["repetition_id"])
        run_id = str(entry["run_id"])
        run = _run_path(runs_dir, run_id)
        manifest_path = run / "manifest.json"
        events_path = run / "events.jsonl"
        runtime_path = run / "runtime-events.jsonl"
        usage_path = run / "usage.json"
        if not all(path.is_file() for path in (manifest_path, events_path, runtime_path, usage_path)):
            raise ValueError("MCP cohort source Run is incomplete")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("run_id") != run_id:
            raise ValueError("MCP cohort source Manifest is invalid")
        provenance_path = run / "provenance.json"
        provenance_source = provenance_path if provenance_path.is_file() else manifest_path
        if _file_sha256(provenance_source) != entry.get("provenance_sha256"):
            raise ValueError("MCP cohort provenance hash differs from its frozen index")
        source_runs.setdefault(run_id, {
            "git_commit": manifest.get("git_commit"),
            "experiment_profile_hash": manifest.get("experiment_profile_hash"),
            "runtime_contract_hash": manifest.get("runtime_contract_hash"),
            "benchmark_asset_hash": manifest.get("benchmark_asset_hash"),
            "pricing_snapshot_hash": manifest.get("pricing_snapshot_hash"),
        })

        completed = [item for item in _read_jsonl(events_path) if (
            item.get("type") == "trial_completed"
            and str(item.get("task_id")) == task_id
            and str(item.get("repetition_id")) == repetition_id
        )]
        if len(completed) != 1:
            raise ValueError("MCP cohort Trial does not have exactly one terminal event")
        terminal = completed[0]
        if terminal.get("status") != entry.get("terminal_status"):
            raise ValueError("MCP cohort terminal status differs from its frozen index")
        try:
            actual_cny = float(terminal["actual_cny"])
        except (KeyError, TypeError, ValueError):
            actual_cny = None
        settled_cny = float(entry["settled_cny"])
        if actual_cny is not None and not math.isclose(actual_cny, settled_cny, abs_tol=0.0000005):
            raise ValueError("MCP cohort terminal amount differs from frozen settlement")
        summary["planned_trials"] += 1
        summary["terminal_trials"] += 1
        status = str(entry["terminal_status"])
        if status not in {"success", "task_failure", "infrastructure_error"}:
            raise ValueError(f"unexpected MCP terminal status: {status}")
        summary[status] += 1
        summary["settled_cny"] += settled_cny
        numerator, denominator = terminal.get("numerator"), terminal.get("denominator")
        if isinstance(numerator, (int, float)) and isinstance(denominator, (int, float)) and denominator > 0:
            summary["rate_numerator"] += float(numerator)
            summary["rate_denominator"] += float(denominator)
            summary["rate_trial_count"] += 1
        runtime = [item for item in _read_jsonl(runtime_path) if (
            str(item.get("task_id")) == task_id
            and str(item.get("repetition_id")) == repetition_id
        )]
        if runtime:
            measured_schema_bytes = [item.get("tools_bytes") for item in runtime if "tools_bytes" in item]
            if measured_schema_bytes:
                if any(not isinstance(value, int) or value < 0 for value in measured_schema_bytes):
                    raise ValueError("MCP cohort runtime tool schema bytes are invalid")
                # Runtime records can contain one entry per Provider request;
                # the first measured schema is the Trial-level value used by
                # the generic Claims compiler as well.
                summary["runtime_tool_schema_bytes"].append(measured_schema_bytes[0])
        usage = json.loads(usage_path.read_text(encoding="utf-8")).get("requests", [])
        requests = [item for item in usage if (
            str(item.get("task_id")), str(item.get("repetition_id"))
        ) == (task_id, repetition_id)]
        if len(requests) != entry["usage_request_count"]:
            raise ValueError("MCP cohort Usage count differs from its frozen index")
        if not entry["usage_complete"]:
            continue
        input_tokens = output_tokens = cache_tokens = reasoning_tokens = 0
        for request in requests:
            provider = request.get("provider_usage")
            if not isinstance(provider, dict):
                raise ValueError("MCP cohort has incomplete Provider Usage")
            prompt = provider.get("prompt_tokens")
            completion = provider.get("completion_tokens")
            details = provider.get("completion_tokens_details", {})
            reasoning = details.get("reasoning_tokens", 0) if isinstance(details, dict) else 0
            prompt_details = provider.get("prompt_tokens_details", {})
            cache = prompt_details.get("cached_tokens", 0) if isinstance(prompt_details, dict) else 0
            if not all(isinstance(value, int) for value in (prompt, completion, cache, reasoning)):
                raise ValueError("MCP cohort has non-numeric Provider Usage")
            input_tokens += prompt
            output_tokens += completion
            cache_tokens += cache
            reasoning_tokens += reasoning
        summary["usage_complete_trials"] += 1
        summary["input_tokens"] += input_tokens
        summary["output_tokens"] += output_tokens
        summary["cache_tokens"] += cache_tokens
        summary["reasoning_tokens"] += reasoning_tokens
        trial_tokens[(arm, task_id, repetition_id)] = input_tokens
    reductions = []
    for (_arm, task_id, repetition_id), eager_tokens in trial_tokens.items():
        if _arm != "eager":
            continue
        deferred_tokens = trial_tokens.get(("deferred", task_id, repetition_id))
        if deferred_tokens is not None and eager_tokens:
            reductions.append((eager_tokens - deferred_tokens) / eager_tokens * 100)
    if len(reductions) != 149:
        raise ValueError("MCP valid matched-pair count differs from frozen cohort")
    for summary in by_arm.values():
        values = summary["runtime_tool_schema_bytes"]
        summary["runtime_tool_schema_sample_size"] = len(values)
        summary["runtime_tool_schema_bytes_median"] = (
            sorted(values)[len(values) // 2] if values else None
        )
        summary["rate"] = (
            summary["rate_numerator"] / summary["rate_denominator"]
            if summary["rate_denominator"] else None
        )
        summary["settled_cny"] = round(summary["settled_cny"], 6)
    return {
        "schema_version": 1, "arms": by_arm,
        "source_runs": source_runs,
        "valid_matched_pairs": len(reductions),
        "input_reduction_percent": {
            "median": sorted(reductions)[len(reductions) // 2],
            "mean": sum(reductions) / len(reductions),
            "p95": _nearest_rank_p95(reductions),
        },
        "excluded_pairs": cohort["summary"]["excluded_pair_reasons"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("validate")
    parser.add_argument("--cohort-index", type=Path, required=True)
    parser.add_argument("--runs-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        cohort = load_mcp_cohort(args.cohort_index)
        metrics = None if args.runs_dir is None else summarize_mcp_cohort(cohort, args.runs_dir)
        print(json.dumps({
            "valid": True, "network_called": False, "model_called": False,
            "entry_count": cohort["entry_count"], "summary": cohort["summary"],
            "sha256": cohort["sha256"], "metrics": metrics,
        }, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"MCP cohort error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

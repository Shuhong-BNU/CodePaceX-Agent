"""Validate the Trial-level MCP formal cohort used by Goal 2 Claims."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


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
    """Calculate MCP evidence from Trial entries, never a Run-level verdict."""
    by_arm: dict[str, dict[str, Any]] = {
        arm: {"planned_trials": 0, "terminal_trials": 0, "success": 0,
              "task_failure": 0, "infrastructure_error": 0,
              "usage_complete_trials": 0, "input_tokens": 0,
              "output_tokens": 0, "reasoning_tokens": 0, "settled_cny": 0.0}
        for arm in ("eager", "deferred")
    }
    trial_tokens: dict[tuple[str, str, str], int] = {}
    for entry in cohort["entries"]:
        arm = str(entry["arm"])
        summary = by_arm[arm]
        summary["planned_trials"] += 1
        summary["terminal_trials"] += 1
        status = str(entry["terminal_status"])
        if status not in {"success", "task_failure", "infrastructure_error"}:
            raise ValueError(f"unexpected MCP terminal status: {status}")
        summary[status] += 1
        summary["settled_cny"] += float(entry["settled_cny"])
        usage_path = runs_dir / str(entry["run_id"]) / "usage.json"
        usage = json.loads(usage_path.read_text(encoding="utf-8")).get("requests", [])
        requests = [item for item in usage if (
            str(item.get("task_id")), str(item.get("repetition_id"))
        ) == (str(entry["task_id"]), str(entry["repetition_id"]))]
        if len(requests) != entry["usage_request_count"]:
            raise ValueError("MCP cohort Usage count differs from its frozen index")
        if not entry["usage_complete"]:
            continue
        input_tokens = output_tokens = reasoning_tokens = 0
        for request in requests:
            provider = request.get("provider_usage")
            if not isinstance(provider, dict):
                raise ValueError("MCP cohort has incomplete Provider Usage")
            prompt = provider.get("prompt_tokens")
            completion = provider.get("completion_tokens")
            details = provider.get("completion_tokens_details", {})
            reasoning = details.get("reasoning_tokens", 0) if isinstance(details, dict) else 0
            if not all(isinstance(value, int) for value in (prompt, completion, reasoning)):
                raise ValueError("MCP cohort has non-numeric Provider Usage")
            input_tokens += prompt
            output_tokens += completion
            reasoning_tokens += reasoning
        summary["usage_complete_trials"] += 1
        summary["input_tokens"] += input_tokens
        summary["output_tokens"] += output_tokens
        summary["reasoning_tokens"] += reasoning_tokens
        trial_tokens[(arm, str(entry["task_id"]), str(entry["repetition_id"]))] = input_tokens
    reductions = []
    for (_arm, task_id, repetition_id), eager_tokens in trial_tokens.items():
        if _arm != "eager":
            continue
        deferred_tokens = trial_tokens.get(("deferred", task_id, repetition_id))
        if deferred_tokens is not None and eager_tokens:
            reductions.append((eager_tokens - deferred_tokens) / eager_tokens * 100)
    if len(reductions) != 149:
        raise ValueError("MCP valid matched-pair count differs from frozen cohort")
    return {
        "schema_version": 1, "arms": by_arm,
        "valid_matched_pairs": len(reductions),
        "input_reduction_percent": {"median": sorted(reductions)[len(reductions) // 2],
                                    "mean": sum(reductions) / len(reductions)},
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

import json
from pathlib import Path

import pytest

from evals.mcp_cohort import canonical_hash, load_mcp_cohort


def _cohort() -> dict:
    entries = [
        {"arm": "eager", "task_id": f"task-{index}", "repetition_id": "1"}
        for index in range(150)
    ]
    entries.append({
        "arm": "deferred", "task_id": "mcp_one_08", "repetition_id": "1",
        "terminal_status": "infrastructure_error", "usage_complete": False,
        "token_pair_exclusion": "infrastructure_error_usage_unknown",
        "request_charge_count": 2, "settlement_count": 3,
    })
    entries.extend(
        {"arm": "deferred", "task_id": f"task-{index}", "repetition_id": "1"}
        for index in range(149)
    )
    payload = {"schema_version": 1, "entry_count": 300, "entries": entries,
        "summary": {"usage_complete_trials": 299, "valid_matched_pairs": 149}}
    payload["sha256"] = canonical_hash(payload)
    return payload


def test_mcp_cohort_requires_hash_unique_trials_and_retained_unknown_usage(tmp_path: Path) -> None:
    path = tmp_path / "cohort.json"
    path.write_text(json.dumps(_cohort()), encoding="utf-8")
    assert load_mcp_cohort(path)["summary"]["valid_matched_pairs"] == 149
    payload = _cohort()
    payload["entries"][0]["task_id"] = "tampered"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_mcp_cohort(path)

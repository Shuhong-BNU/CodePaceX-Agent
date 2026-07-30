from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.evaluation_v2 import full_replay, p3a_paired_pilot as p3a


ROOT = Path(__file__).resolve().parents[1]


def test_freeze_has_exactly_eight_interleaved_unique_runs_and_formal_identities() -> None:
    frozen = p3a.freeze_payload(ROOT)
    runs = frozen["task_runs"]
    assert frozen["bound_main_commit"] == p3a.BOUND_MAIN_COMMIT
    assert [item["treatment"] for item in runs] == [
        "V2_CONTROL", "V3_CORE", "V3_CORE", "V2_CONTROL",
        "V2_CONTROL", "V3_CORE", "V3_CORE", "V2_CONTROL",
    ]
    assert [item["instance_id"] for item in runs][::2] == [item[0] for item in p3a.P3A_TASK_ORDER]
    assert len(runs) == len({item["task_run_id"] for item in runs}) == 8
    assert frozen["execution_contract"] == {
        "strict_serial": True, "request_ceiling_per_run": 40, "retry": 0,
        "fallback": False, "only_treatment_difference": "treatment",
        "paid_execution": False, "automatic_retry_rerun_or_continuation": False,
    }
    formal = full_replay.freeze_payload(ROOT)
    assert frozen["frozen_identities"]["provider"] == formal["provider_contract"]
    assert frozen["frozen_identities"]["official_evaluator"] == formal["official_evaluator"]
    assert frozen["frozen_identities"]["model"]["model_id"] == formal["provider_contract"]["model_id"]
    assert frozen["frozen_identities"]["prompt"]["system_instruction_sha256"] == formal["runtime_contract"]["system_instruction_sha256"]


def test_pairs_only_differ_by_treatment_and_have_unique_draft_allocations() -> None:
    frozen = p3a.freeze_payload(ROOT)
    for first, second in zip(frozen["task_runs"][::2], frozen["task_runs"][1::2]):
        assert {key: value for key, value in first.items() if key not in {"ordinal", "task_run_id", "treatment", "expected_artifact_path"}} == {
            key: value for key, value in second.items() if key not in {"ordinal", "task_run_id", "treatment", "expected_artifact_path"}
        }
    children = frozen["child_allocation_drafts"]
    assert frozen["parent_authorization_draft"]["status"] == "draft_not_authorized"
    assert len({item["child_allocation_id"] for item in children}) == 8
    assert len({item["child_allocation_sha256"] for item in children}) == 8
    assert all(item["status"] == "draft_not_authorized" for item in children)


def test_zero_provider_artifact_is_deterministic_and_wires_paired_merge(tmp_path: Path) -> None:
    output = tmp_path / "artifact"
    identities = p3a.write_artifacts(ROOT, output)
    assert set(path.name for path in output.iterdir()) == {
        p3a.FREEZE_NAME, p3a.MANIFEST_NAME, p3a.TREATMENT_ORDER_NAME, p3a.BUDGET_NAME,
        p3a.PARENT_NAME, p3a.CHILDREN_NAME, p3a.SCHEMA_NAME, p3a.READINESS_NAME,
    }
    frozen = json.loads((output / p3a.FREEZE_NAME).read_text())
    readiness = json.loads((output / p3a.READINESS_NAME).read_text())
    p3a.validate_freeze(ROOT, frozen)
    assert identities["freeze_sha256"]
    assert readiness["status"] == "passed_zero_provider_readiness"
    assert readiness["provider_requests"] == readiness["usage"] == 0
    assert readiness["charge_cny"] == "0" and readiness["provider_secret_read"] is False
    assert readiness["paid_jobs"] == "skipped"
    assert readiness["task_run_count"] == readiness["unique_task_run_count"] == 8
    assert readiness["paired_result_merge_count"] == 4
    v2 = [item for item in readiness["run_records"] if item["treatment"] == "V2_CONTROL"]
    v3 = [item for item in readiness["run_records"] if item["treatment"] == "V3_CORE"]
    assert all(not item["v3_advice_expected"] and not item["v3_activation_artifact_required"] for item in v2)
    assert all(item["v3_advice_expected"] and item["v3_activation_artifact_required"] for item in v3)
    with pytest.raises(ValueError, match="overwrite"):
        p3a.write_artifacts(ROOT, output)


def test_paired_merge_rejects_cross_pair_or_missing_treatment() -> None:
    frozen = p3a.freeze_payload(ROOT)
    records = p3a.readiness_payload(ROOT, frozen)["run_records"]
    assert len(p3a.merge_paired_results(records)) == 4
    with pytest.raises(ValueError, match="missing"):
        p3a.merge_paired_results(records[:-1])
    incorrect = [dict(item) for item in records]
    incorrect[1]["base_commit"] = "0" * 40
    with pytest.raises(ValueError, match="missing"):
        p3a.merge_paired_results(incorrect)

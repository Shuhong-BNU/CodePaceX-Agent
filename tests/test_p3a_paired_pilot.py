from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from evals.evaluation_v2 import full_replay, p3a_paired_pilot as p3a


ROOT = Path(__file__).resolve().parents[1]


def _records(frozen: dict) -> list[dict]:
    return [
        {
            **run,
            "artifact_path": run["expected_artifact_path"],
            "terminal_status": "zero_provider_rehearsal_only",
            "provider_requests": 0,
            "usage": 0,
            "charge_cny": "0",
            "provider_secret_read": False,
        }
        for run in frozen["task_runs"]
    ]


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


def test_paired_merge_requires_the_complete_frozen_four_pairs() -> None:
    frozen = p3a.freeze_payload(ROOT)
    records = _records(frozen)
    assert len(p3a.merge_paired_results(frozen, records)) == 4
    with pytest.raises(ValueError, match="exactly eight"):
        p3a.merge_paired_results(frozen, records[:6])  # Missing an entire pair.
    with pytest.raises(ValueError, match="exactly eight"):
        p3a.merge_paired_results(frozen, records[:-1])  # Missing one treatment.
    duplicate = records[:-1] + [dict(records[0])]
    with pytest.raises(ValueError, match="duplicate task-run"):
        p3a.merge_paired_results(frozen, duplicate)
    unexpected = [dict(item) for item in records]
    unexpected[0]["task_run_id"] = "p3a-unexpected"
    with pytest.raises(ValueError, match="unexpected"):
        p3a.merge_paired_results(frozen, unexpected)
    mismatched = [dict(item) for item in records]
    mismatched[1]["pair_index"] = 4
    with pytest.raises(ValueError, match="frozen pair key"):
        p3a.merge_paired_results(frozen, mismatched)


def test_readiness_rejects_an_unexecuted_or_invalid_rehearsal(tmp_path: Path) -> None:
    frozen = p3a.freeze_payload(ROOT)
    freeze_path = tmp_path / p3a.FREEZE_NAME
    freeze_path.write_text(json.dumps(frozen), encoding="utf-8")
    for rehearsal in (
        {},
        {"executed": True, "ledger_closed": True, "active_reservation": None, "provider_requests": 1, "usage": 0, "charge_cny": "0", "provider_secret_read": False, "agent_dispatch_count": 8, "recording_fake_transport_requests": 8},
        {"executed": True, "ledger_closed": False, "active_reservation": None, "provider_requests": 0, "usage": 0, "charge_cny": "0", "provider_secret_read": False, "agent_dispatch_count": 8, "recording_fake_transport_requests": 8},
    ):
        with pytest.raises(ValueError, match="rehearsal"):
            p3a.readiness_payload(ROOT, frozen, freeze_path=freeze_path, rehearsal=rehearsal)


def test_actual_zero_provider_rehearsal_binds_runner_artifacts_evaluator_ledger_and_hashes(tmp_path: Path) -> None:
    output = tmp_path / "artifact"
    identities = p3a.write_artifacts(ROOT, output)
    assert {path.name for path in output.iterdir()} == {
        p3a.FREEZE_NAME, p3a.MANIFEST_NAME, p3a.TREATMENT_ORDER_NAME, p3a.BUDGET_NAME,
        p3a.PARENT_NAME, p3a.CHILDREN_NAME, p3a.SCHEMA_NAME, p3a.READINESS_NAME,
        p3a.REHEARSAL_DIRECTORY,
    }
    frozen = json.loads((output / p3a.FREEZE_NAME).read_text())
    readiness = json.loads((output / p3a.READINESS_NAME).read_text())
    p3a.validate_freeze(ROOT, frozen)
    assert readiness["freeze_sha256"] == identities["freeze_sha256"] == hashlib.sha256((output / p3a.FREEZE_NAME).read_bytes()).hexdigest()
    assert readiness["freeze_canonical_sha256"] == identities["freeze_canonical_sha256"] == p3a.canonical_hash(frozen)
    assert readiness["freeze_sha256"] != readiness["freeze_canonical_sha256"]
    assert readiness["status"] == "passed_zero_provider_readiness"
    assert readiness["provider_requests"] == readiness["usage"] == 0
    assert readiness["charge_cny"] == "0" and readiness["provider_secret_read"] is False
    rehearsal = readiness["rehearsal"]
    assert rehearsal["executed"] is True and rehearsal["runner"] == "full_replay._full_task_executor"
    assert rehearsal["ledger_closed"] is True and rehearsal["active_reservation"] is None
    assert rehearsal["agent_dispatch_count"] == 8 and rehearsal["recording_fake_transport_requests"] >= 8
    assert rehearsal["simulated_provider_requests"] >= 8 and rehearsal["simulated_usage"] > 0
    assert float(rehearsal["simulated_charge_cny"]) > 0
    assert readiness["task_run_count"] == readiness["unique_task_run_count"] == 8
    assert readiness["paired_result_merge_count"] == 4
    for record in rehearsal["run_records"]:
        task_root = output / p3a.REHEARSAL_DIRECTORY / record["artifact_path"]
        assert (task_root / "task-result.json").is_file() and (task_root / "official-report.json").is_file()
        if record["treatment"] == "V2_CONTROL":
            assert record["v3_advice_present"] is False and record["v3_activation_schema_present"] is False
            assert not (task_root / "capability-v3").exists()
        else:
            assert record["v3_advice_present"] is True and record["v3_activation_schema_present"] is True
            assert record["treatment_fidelity"]["valid"] is True
            assert all((task_root / "capability-v3" / name).is_file() for name in ("summary.json", "events.jsonl", "final.patch"))
    with pytest.raises(ValueError, match="overwrite"):
        p3a.write_artifacts(ROOT, output)

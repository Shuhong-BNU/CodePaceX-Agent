from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import evals.stage_c as stage_c


ROOT = Path(".")


def _bundle() -> tuple[dict, dict, dict]:
    return (
        json.loads(Path("evals/stage_c/stage_c_matrix.json").read_text(encoding="utf-8")),
        json.loads(Path("evals/stage_c/stage_c_baseline.json").read_text(encoding="utf-8")),
        json.loads(Path("evals/stage_c/stage_c_freeze.json").read_text(encoding="utf-8")),
    )


def test_frozen_matrix_is_the_exact_goal4_20_task_order_and_6_14_split() -> None:
    matrix, _baseline, _freeze = _bundle()
    stage_c.validate_matrix(matrix)
    assert matrix["source_goal4_matrix_sha256"] == stage_c.SOURCE_GOAL4_MATRIX_SHA256
    assert [task["instance_id"] for task in matrix["tasks"]] == list(stage_c.ALL_IDS)
    assert [task["instance_id"] for task in matrix["tasks"][:6]] == list(stage_c.PHASE_1_IDS)
    assert [task["instance_id"] for task in matrix["tasks"][6:]] == list(stage_c.PHASE_2_IDS)


def test_baseline_is_derived_from_published_goal4_evidence_and_preserves_identity() -> None:
    _matrix, baseline, freeze = _bundle()
    rebuilt = stage_c.baseline_payload(ROOT)
    assert baseline == rebuilt
    assert baseline["source"]["artifact_id"] == stage_c.GOAL4_ARTIFACT_ID
    assert baseline["source"]["source_goal4_matrix_sha256"] == stage_c.SOURCE_GOAL4_MATRIX_SHA256
    assert freeze["source_goal4_baseline"] == baseline["source"]


def test_stage_b_treatment_is_hash_bound_without_changing_goal4_identity() -> None:
    _matrix, _baseline, freeze = _bundle()
    profile = stage_c.stage_c_profile()
    assert freeze["experiment_profile"]["validation_mode"] == "stage_b"
    assert freeze["experiment_profile_hash"] == profile.profile_hash()
    assert freeze["runtime_contract_hash"] == profile.runtime_contract_hash()
    assert freeze["stage_b_merge_commit"] == stage_c.STAGE_B_MERGE_COMMIT
    assert freeze["fallback_enabled"] is False
    assert freeze["automatic_retry"] == 0
    assert freeze["budget"]["maximum_requests_per_instance"] == 40


def test_rolling_reservation_is_derived_per_request_and_full_path_is_risk_only() -> None:
    _matrix, _baseline, freeze = _bundle()
    budget = stage_c.budget_contract(ROOT)
    assert budget == freeze["budget"]
    assert budget["per_request_maximum_reservation_cny"] == "1.830912"
    assert Decimal(budget["per_request_maximum_reservation_cny"]) <= Decimal("80")
    assert Decimal(budget["theoretical_full_path_maximum_phase_1_cny"]) > Decimal("80")
    assert budget["reservation_granularity"] == "one_provider_request"
    assert budget["completion_guarantee"] is False


def test_task_admission_is_serial_and_fails_closed_before_transport() -> None:
    maximum = Decimal("1.830912")
    assert stage_c.admit_task(
        phase="phase_1", completed_terminal_ids=(), phase_conservative_consumption=Decimal("0"),
        active_reservation=False, authorization_cap=Decimal("80"), next_request_maximum=maximum,
    ) == (True, "admitted")
    assert stage_c.admit_task(
        phase="phase_1", completed_terminal_ids=(), phase_conservative_consumption=Decimal("79"),
        active_reservation=False, authorization_cap=Decimal("80"), next_request_maximum=maximum,
    ) == (False, "budget_blocked_before_transport")
    assert stage_c.admit_task(
        phase="phase_1", completed_terminal_ids=(), phase_conservative_consumption=Decimal("0"),
        active_reservation=True, authorization_cap=Decimal("80"), next_request_maximum=maximum,
    ) == (False, "active_reservation_present")
    assert stage_c.admit_task(
        phase="phase_2", completed_terminal_ids=(), phase_conservative_consumption=Decimal("0"),
        active_reservation=False, authorization_cap=Decimal("250"), next_request_maximum=maximum,
    ) == (False, "phase_1_terminal_evidence_incomplete")


def test_partial_claim_preserves_not_run_and_never_emits_full_result() -> None:
    matrix, baseline, _freeze = _bundle()
    claim = stage_c.compile_paired_claim(
        matrix=matrix, baseline=baseline,
        stage_results={stage_c.PHASE_1_IDS[0]: "budget_blocked"},
    )
    assert claim["full_claim"] is False
    assert claim["partial"] is True
    assert claim["scorable_denominator"] == 0
    assert claim["budget_blocked"] == [stage_c.PHASE_1_IDS[0]]
    assert len(claim["not_run"]) == 19


def test_complete_synthetic_results_are_the_only_path_to_full_paired_claim() -> None:
    matrix, baseline, _freeze = _bundle()
    claim = stage_c.compile_paired_claim(
        matrix=matrix, baseline=baseline,
        stage_results={instance_id: "unresolved" for instance_id in stage_c.ALL_IDS},
    )
    assert claim["full_claim"] is True
    assert claim["scorable_denominator"] == 20
    assert claim["registered_denominator"] == 20


def test_terminal_evidence_distinguishes_budget_blocks_from_scorable_outcomes() -> None:
    stage_c.validate_terminal_evidence({
        "status": "budget_blocked", "active_reservation": None,
        "provider_requests": 0, "transport_started": False, "budget_blocked": True,
    })
    stage_c.validate_terminal_evidence({"status": "not_run", "provider_requests": 0, "active_reservation": None})
    try:
        stage_c.validate_terminal_evidence({
            "status": "budget_blocked", "active_reservation": None,
            "provider_requests": 1, "transport_started": True, "budget_blocked": True,
        })
    except ValueError as error:
        assert "before transport" in str(error)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("post-transport budget block was accepted")


def test_agent_payload_excludes_goal4_outcome_cost_and_failure_taxonomy() -> None:
    _matrix, baseline, _freeze = _bundle()
    payload = stage_c.agent_safe_task_payload({"instance_id": stage_c.PHASE_1_IDS[0]})
    rendered = json.dumps(payload, sort_keys=True)
    assert payload == {"instance_id": stage_c.PHASE_1_IDS[0]}
    for forbidden in ("goal4_status", "goal4_requests", "cost", "failure", "taxonomy"):
        assert forbidden not in rendered
    assert "goal4_status" in json.dumps(baseline, sort_keys=True)


def test_stage_c_freeze_does_not_import_provider_or_agent_runtime() -> None:
    source = Path("evals/stage_c.py").read_text(encoding="utf-8")
    for forbidden in ("codepacex.client", "create_client", "swe_inference", "run_official_evaluator"):
        assert forbidden not in source


def test_freeze_and_dry_run_are_zero_provider_and_do_not_rewrite_goal4_evidence(tmp_path: Path) -> None:
    goal4_paths = [
        Path("evals/GOAL4_EVIDENCE_INDEX.md"), Path("evals/GOAL4_FINAL_REPORT.md"),
        Path("evals/claims.goal4.json"),
    ]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in goal4_paths}
    output = tmp_path / "freeze"
    stage_c.write_freeze_bundle(ROOT, output)
    dry_run = stage_c.zero_provider_dry_run(ROOT, tmp_path / "dry", phase="phase_1")
    assert dry_run["provider_requests"] == 0
    assert dry_run["paid_execution"] is False
    assert dry_run["formal_stage_c_trial"] is False
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in goal4_paths} == before


def test_committed_bundle_is_reproducible_and_phase_1_artifact_contract_is_strict(tmp_path: Path) -> None:
    assert stage_c.validate_frozen_bundle(ROOT, Path("evals/stage_c"))["valid"] is True
    artifact = {
        "artifact_id": "123456", "artifact_archive_sha256": "a" * 64,
        "report_sha256": "b" * 64, "ledger_sha256": "c" * 64,
        "active_reservation": None,
        "phase_1_instance_ids": list(stage_c.PHASE_1_IDS),
        "phase_2_instance_ids": list(stage_c.PHASE_2_IDS),
        "terminal_statuses": {instance_id: "unresolved" for instance_id in stage_c.PHASE_1_IDS},
    }
    stage_c.validate_phase_1_artifact(artifact)
    artifact["active_reservation"] = {"reservation_id": "still-open"}
    try:
        stage_c.validate_phase_1_artifact(artifact)
    except ValueError as error:
        assert "active reservation" in str(error)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("continuation accepted an active reservation")


def test_phase_2_budget_is_cumulative_less_phase_1_consumption() -> None:
    phase_1_consumption = Decimal("54.250000")
    remaining = Decimal(stage_c.CUMULATIVE_CAP) - phase_1_consumption
    assert remaining == Decimal("195.750000")
    assert stage_c.admit_task(
        phase="phase_2", completed_terminal_ids=stage_c.PHASE_1_IDS,
        phase_conservative_consumption=Decimal("0"),
        active_reservation=False, authorization_cap=remaining,
        next_request_maximum=Decimal("1.830912"),
    ) == (True, "admitted")

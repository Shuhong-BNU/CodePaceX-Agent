from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from evals.evaluation_v2 import capability_v3_pilot as pilot
from evals.paid_gate import ProviderRequestBudget, provider_request_budget_environment


ROOT = Path(__file__).resolve().parents[1]


def test_frozen_pilot_has_six_adjacent_pairs_and_only_flag_differs() -> None:
    payload = pilot.freeze_payload(ROOT, run_id="unit-controlled-pilot")
    assert payload["treatments"] == ["V2_CONTROL", "V3_CORE"]
    assert len(payload["task_runs"]) == 12
    assert [item["capability_v3_flag"] for item in payload["task_runs"]] == [
        "V2_CONTROL", "V3_CORE",
    ] * 6
    assert [item["instance_id"] for item in payload["task_runs"]][::2] == list(pilot.PILOT_TASK_IDS)
    assert payload["fairness_contract"]["only_treatment_difference"] == "capability_v3_flag"
    assert payload["goal4_source"]["accepted_baseline_modified"] is False
    assert payload["allocation_binding"]["internal_run_id"] == "unit-controlled-pilot"
    assert payload["allocation_binding"]["allocation_id"] == "capability-v3-unit-controlled-pilot-stage-c"


def test_frozen_budget_is_exact_and_hard_limited() -> None:
    budget = pilot.budget_contract(ROOT)
    assert budget["provider_request_ceiling_per_run"] == 40
    assert Decimal(budget["per_request_theoretical_exposure_cny"]) == Decimal("1.830912")
    assert Decimal(budget["per_run_theoretical_exposure_cny"]) == Decimal("73.236480")
    assert Decimal(budget["total_theoretical_exposure_cny"]) == Decimal("878.837760")


def test_frozen_task_run_allocations_are_unique_and_remain_parent_ceiling_bindings() -> None:
    payload = pilot.freeze_payload(ROOT, run_id="unit-controlled-pilot")
    binding = payload["allocation_binding"]
    allocations = binding["task_run_allocations"]
    assert len(allocations) == 12
    assert {item["task_run_id"] for item in allocations} == {
        item["task_run_id"] for item in payload["task_runs"]
    }
    assert len({item["task_run_allocation_id"] for item in allocations}) == 12
    assert len({item["task_run_allocation_hash"] for item in allocations}) == 12
    assert all(
        Decimal(item["theoretical_ceiling_cny"]) == Decimal("73.236480")
        for item in allocations
    )
    assert sum(Decimal(item["theoretical_ceiling_cny"]) for item in allocations) == Decimal("878.837760")
    assert Decimal(binding["spendable_total_cny"]) == Decimal("878.837759")
    assert Decimal(binding["safety_reserve_cny"]) == Decimal("0.000001")
    assert "not a separate ledger" in binding["task_run_binding"]


def test_freeze_validation_and_zero_provider_rehearsal_bind_both_flags(tmp_path: Path) -> None:
    freeze = tmp_path / "freeze"
    identities = pilot.write_freeze(ROOT, freeze, run_id="unit-controlled-pilot")
    assert pilot.validate_freeze(ROOT, freeze)["freeze_sha256"] == identities["freeze_sha256"]
    preflight = tmp_path / "preflight.json"
    preflight.write_text(json.dumps({"passed": True, "ready_count": 6}), encoding="utf-8")
    rehearsal = pilot.rehearse(ROOT, freeze, preflight, tmp_path / "rehearsal")
    assert rehearsal["completed"] is True
    assert rehearsal["provider_requests"] == rehearsal["usage"] == 0
    assert rehearsal["provider_secret_read"] is False
    records = list((tmp_path / "rehearsal").glob("runs/*/tasks/*/task-run-contract.json"))
    assert len(records) == 12
    assert {json.loads(path.read_text())["capability_v3_flag"] for path in records} == {"V2_CONTROL", "V3_CORE"}


def test_execution_entry_rehearsal_binds_unique_allocation_without_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("evals.paid_gate._git_is_clean", lambda _root: True)
    freeze = tmp_path / "freeze"
    identities = pilot.write_freeze(ROOT, freeze, run_id="entry-rehearsal-pilot")
    preflight = tmp_path / "preflight.json"
    preflight.write_text(json.dumps({"passed": True, "ready_count": 6}), encoding="utf-8")
    artifact = tmp_path / "entry"
    result = pilot.rehearse_execution_entry(ROOT, freeze, preflight, artifact)
    assert result["completed"] is True
    assert result["provider_requests"] == result["usage"] == 0
    assert result["charge_cny"] == "0" and result["active_reservation"] is None
    assert len(result["task_runs"]) == 12
    assert result["allocation_binding"]["allocation_hash"] == identities["allocation_hash"]
    ledger = json.loads((artifact / "ledger.json").read_text(encoding="utf-8"))
    assert ledger["allocation_hash"] == identities["allocation_hash"]
    execution = json.loads((artifact / "controlled-pilot-execution-contract.json").read_text(encoding="utf-8"))
    assert execution["allocation_hash"] == identities["allocation_hash"]
    assert len(execution["task_run_allocations"]) == 12
    assert all(item["task_run_allocation_id"] for item in ledger["settlements"])
    assert all(item["task_run_allocation_hash"] for item in ledger["settlements"])
    with pytest.raises(ValueError, match="duplicate controlled Pilot allocation"):
        pilot.rehearse_execution_entry(ROOT, freeze, preflight, artifact)


def test_task_run_identity_mismatches_and_cross_run_reuse_fail_closed_before_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("evals.paid_gate._git_is_clean", lambda _root: True)
    freeze = tmp_path / "freeze"
    pilot.write_freeze(ROOT, freeze, run_id="identity-gate-pilot")
    frozen = json.loads((freeze / pilot.FREEZE_NAME).read_text(encoding="utf-8"))
    entry = tmp_path / "entry"
    gate = pilot._fresh_gate(ROOT, freeze, entry, "test", run_id="identity-gate-pilot")
    first = pilot._task_run_identity(frozen["allocation_binding"]["task_run_allocations"][0])
    second = pilot._task_run_identity(frozen["allocation_binding"]["task_run_allocations"][1])
    trial_id = "swe/v2-full-20/identity-gate-pilot-01-V2_CONTROL/aws-cloudformation__cfn-lint-3749"
    for field, value in (
        ("task_run_id", "wrong-run"),
        ("task_run_allocation_id", second.task_run_allocation_id),
        ("task_run_allocation_hash", "0" * 64),
        ("instance_id", "wrong-instance"),
        ("treatment", "wrong-treatment"),
        ("expected_artifact_path", "runs/wrong/tasks/wrong-instance"),
    ):
        with pytest.raises(ValueError, match="task-run allocation identity"):
            gate.reserve(
                trial_id, maximum_requests=1,
                maximum_input_tokens_per_request=pilot.full_replay.MAX_INPUT_TOKENS,
                maximum_output_tokens_per_request=pilot.full_replay.MAX_OUTPUT_TOKENS,
                task_run_identity=first.model_copy(update={field: value}),
            )
    with pytest.raises(ValueError, match="trial ID"):
        gate.reserve(
            "swe/v2-full-20/wrong-run/aws-cloudformation__cfn-lint-3749",
            maximum_requests=1,
            maximum_input_tokens_per_request=pilot.full_replay.MAX_INPUT_TOKENS,
            maximum_output_tokens_per_request=pilot.full_replay.MAX_OUTPUT_TOKENS,
            task_run_identity=first,
        )
    with pytest.raises(ValueError, match="lacks an explicit task-run allocation identity"):
        gate.reserve(
            trial_id, maximum_requests=1,
            maximum_input_tokens_per_request=pilot.full_replay.MAX_INPUT_TOKENS,
            maximum_output_tokens_per_request=pilot.full_replay.MAX_OUTPUT_TOKENS,
        )
    ledger = json.loads((entry / "ledger.json").read_text(encoding="utf-8"))
    assert ledger["request_charges"] == [] and ledger["spent_cny"] == "0"
    assert {item["reason"] for item in ledger["budget_blocks"]} == {"task_run_contract"}


def test_task_run_ceiling_and_serial_handoff_are_enforced_without_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("evals.paid_gate._git_is_clean", lambda _root: True)
    freeze = tmp_path / "freeze"
    pilot.write_freeze(ROOT, freeze, run_id="ceiling-gate-pilot")
    frozen = json.loads((freeze / pilot.FREEZE_NAME).read_text(encoding="utf-8"))
    gate = pilot._fresh_gate(ROOT, freeze, tmp_path / "entry", "test", run_id="ceiling-gate-pilot")
    identities = [pilot._task_run_identity(item) for item in frozen["allocation_binding"]["task_run_allocations"]]
    first_trial = "swe/v2-full-20/ceiling-gate-pilot-01-V2_CONTROL/aws-cloudformation__cfn-lint-3749"
    second_trial = "swe/v2-full-20/ceiling-gate-pilot-02-V3_CORE/aws-cloudformation__cfn-lint-3749"
    active = gate.reserve(
        first_trial, maximum_requests=1,
        maximum_input_tokens_per_request=pilot.full_replay.MAX_INPUT_TOKENS,
        maximum_output_tokens_per_request=pilot.full_replay.MAX_OUTPUT_TOKENS,
        task_run_identity=identities[0],
    )
    with pytest.raises(ValueError, match="unsettled paid trial reservation"):
        gate.reserve(
            second_trial, maximum_requests=1,
            maximum_input_tokens_per_request=pilot.full_replay.MAX_INPUT_TOKENS,
            maximum_output_tokens_per_request=pilot.full_replay.MAX_OUTPUT_TOKENS,
            task_run_identity=identities[1],
        )
    gate.cancel(active, reason="provider_confirmed_not_submitted")
    for _ in range(40):
        reservation = gate.reserve(
            first_trial, maximum_requests=1,
            maximum_input_tokens_per_request=pilot.full_replay.MAX_INPUT_TOKENS,
            maximum_output_tokens_per_request=pilot.full_replay.MAX_OUTPUT_TOKENS,
            task_run_identity=identities[0],
        )
        gate.settle(reservation, request_usages=[
            (pilot.full_replay.MAX_INPUT_TOKENS, pilot.full_replay.MAX_OUTPUT_TOKENS),
        ])
    with pytest.raises(ValueError, match="task-run theoretical ceiling"):
        gate.reserve(
            first_trial, maximum_requests=1,
            maximum_input_tokens_per_request=pilot.full_replay.MAX_INPUT_TOKENS,
            maximum_output_tokens_per_request=pilot.full_replay.MAX_OUTPUT_TOKENS,
            task_run_identity=identities[0],
        )
    ledger = json.loads((tmp_path / "entry" / "ledger.json").read_text(encoding="utf-8"))
    assert ledger["active_reservation"] is None
    assert ledger["spent_cny"] == "73.236480"
    assert ledger["budget_blocks"][-1]["reason"] == "task_run_ceiling"


def test_child_request_budget_receives_complete_explicit_task_run_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("evals.paid_gate._git_is_clean", lambda _root: True)
    freeze = tmp_path / "freeze"
    pilot.write_freeze(ROOT, freeze, run_id="child-contract-pilot")
    frozen = json.loads((freeze / pilot.FREEZE_NAME).read_text(encoding="utf-8"))
    gate = pilot._fresh_gate(ROOT, freeze, tmp_path / "entry", "test", run_id="child-contract-pilot")
    identity = pilot._task_run_identity(frozen["allocation_binding"]["task_run_allocations"][0])
    trial_id = "swe/v2-full-20/child-contract-pilot-01-V2_CONTROL/aws-cloudformation__cfn-lint-3749"
    environment = provider_request_budget_environment(
        gate, trial_id=trial_id,
        maximum_input_tokens_per_request=pilot.full_replay.MAX_INPUT_TOKENS,
        maximum_output_tokens_per_request=pilot.full_replay.MAX_OUTPUT_TOKENS,
        maximum_reasoning_tokens_per_request=pilot.full_replay.MAX_REASONING_TOKENS,
        maximum_provider_requests_per_trial=40,
        requested_thinking_budget=pilot.full_replay.MAX_REASONING_TOKENS,
        task_run_identity=identity,
    )
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    child = ProviderRequestBudget.from_environment()
    assert child is not None and child.task_run_identity == identity
    reservation = child.reserve_before_request()
    child.gate.cancel(reservation, reason="provider_confirmed_not_submitted")
    monkeypatch.setenv("CODEPACEX_BUDGET_TASK_RUN_ALLOCATION_HASH", "")
    with pytest.raises(ValueError, match="incomplete controlled Pilot task-run allocation identity"):
        ProviderRequestBudget.from_environment()


def test_entry_rejects_tampered_allocation_or_run_binding(tmp_path: Path) -> None:
    freeze = tmp_path / "freeze"
    pilot.write_freeze(ROOT, freeze, run_id="bound-controlled-pilot")
    payload_path = freeze / pilot.FREEZE_NAME
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["allocation_binding"]["internal_run_id"] = "other-controlled-pilot"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="Freeze differs"):
        pilot.validate_freeze(ROOT, freeze)


def test_stage_c_entry_fails_closed_without_allocation_or_with_overbudget_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("evals.paid_gate._git_is_clean", lambda _root: True)
    freeze = tmp_path / "freeze"
    pilot.write_freeze(ROOT, freeze, run_id="entry-gate-pilot")
    frozen = json.loads((freeze / pilot.FREEZE_NAME).read_text(encoding="utf-8"))
    pricing = pilot.load_pricing(freeze / "pricing-snapshot.json")
    authorization, ledger, allocation, _binding = pilot._allocation_binding(frozen, run_id="entry-gate-pilot")
    authorization_path, ledger_path, allocation_path = tmp_path / "authorization.json", tmp_path / "ledger.json", tmp_path / "allocation.json"
    pilot._write_json(authorization_path, authorization.model_dump(mode="json"))
    pilot._write_json(ledger_path, ledger.model_dump(mode="json"))
    with pytest.raises(ValueError, match="requires a budget allocation"):
        pilot.PaidRunGate(root=ROOT, authorization_path=authorization_path, ledger_path=ledger_path, pricing=pricing, stage="C")
    overbudget = allocation.model_copy(update={"spendable_total_cny": authorization.authorized_total_cny})
    pilot._write_json(allocation_path, overbudget.model_dump(mode="json"))
    with pytest.raises(ValueError, match="consumes the reserved safety margin"):
        pilot.PaidRunGate(root=ROOT, authorization_path=authorization_path, ledger_path=ledger_path, allocation_path=allocation_path, pricing=pricing, stage="C")


def test_paid_entry_rejects_wrong_allocation_hash_before_any_artifact(tmp_path: Path) -> None:
    freeze = tmp_path / "freeze"
    identities = pilot.write_freeze(ROOT, freeze, run_id="wrong-hash-pilot")
    with pytest.raises(ValueError, match="allocation hash"):
        pilot.run_paid_pilot(
            ROOT, freeze, tmp_path / "paid", expected_freeze_sha256=identities["freeze_sha256"],
            expected_allocation_hash="0" * 64, approved_total_hard_cap_cny="878.837760",
            authorization_acknowledgement="test", run_id="wrong-hash-pilot",
        )
    assert not (tmp_path / "paid").exists()


def test_paid_path_refuses_missing_confirmation(tmp_path: Path) -> None:
    freeze = tmp_path / "freeze"; identities = pilot.write_freeze(ROOT, freeze, run_id="paid-path-unit")
    with pytest.raises(ValueError, match="paid execution requires"):
        pilot.main(["paid-run", "--root", str(ROOT), "--freeze", str(freeze), "--artifact-root", str(tmp_path / "paid"), "--expected-freeze-sha256", "x", "--expected-allocation-hash", identities["allocation_hash"], "--approved-total-hard-cap-cny", "0", "--authorization-acknowledgement", "no", "--run-id", "paid-path-unit"])

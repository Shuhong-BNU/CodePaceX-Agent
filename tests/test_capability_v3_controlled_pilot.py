from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from evals.evaluation_v2 import capability_v3_pilot as pilot


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
    with pytest.raises(ValueError, match="duplicate controlled Pilot allocation"):
        pilot.rehearse_execution_entry(ROOT, freeze, preflight, artifact)


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

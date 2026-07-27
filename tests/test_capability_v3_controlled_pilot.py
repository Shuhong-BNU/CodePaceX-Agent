from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from evals.evaluation_v2 import capability_v3_pilot as pilot


ROOT = Path(__file__).resolve().parents[1]


def test_frozen_pilot_has_six_adjacent_pairs_and_only_flag_differs() -> None:
    payload = pilot.freeze_payload(ROOT)
    assert payload["treatments"] == ["V2_CONTROL", "V3_CORE"]
    assert len(payload["task_runs"]) == 12
    assert [item["capability_v3_flag"] for item in payload["task_runs"]] == [
        "V2_CONTROL", "V3_CORE",
    ] * 6
    assert [item["instance_id"] for item in payload["task_runs"]][::2] == list(pilot.PILOT_TASK_IDS)
    assert payload["fairness_contract"]["only_treatment_difference"] == "capability_v3_flag"
    assert payload["goal4_source"]["accepted_baseline_modified"] is False


def test_frozen_budget_is_exact_and_hard_limited() -> None:
    budget = pilot.budget_contract(ROOT)
    assert budget["provider_request_ceiling_per_run"] == 40
    assert Decimal(budget["per_request_theoretical_exposure_cny"]) == Decimal("1.830912")
    assert Decimal(budget["per_run_theoretical_exposure_cny"]) == Decimal("73.236480")
    assert Decimal(budget["total_theoretical_exposure_cny"]) == Decimal("878.837760")


def test_freeze_validation_and_zero_provider_rehearsal_bind_both_flags(tmp_path: Path) -> None:
    freeze = tmp_path / "freeze"
    identities = pilot.write_freeze(ROOT, freeze)
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


def test_paid_path_refuses_missing_confirmation(tmp_path: Path) -> None:
    freeze = tmp_path / "freeze"; pilot.write_freeze(ROOT, freeze)
    with pytest.raises(ValueError, match="paid execution requires"):
        pilot.main(["paid-run", "--root", str(ROOT), "--freeze", str(freeze), "--artifact-root", str(tmp_path / "paid"), "--expected-freeze-sha256", "x", "--approved-total-hard-cap-cny", "0", "--authorization-acknowledgement", "no", "--run-id", "fresh"])

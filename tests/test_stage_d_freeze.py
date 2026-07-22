from __future__ import annotations

import json
from pathlib import Path

from evals.stage_d_freeze import CANARY_INSTANCE_IDS, freeze_payload, validate_freeze


def test_stage_d_freeze_is_current_and_paid_execution_is_closed() -> None:
    root = Path(".")
    freeze = validate_freeze(root, root / "evals/stage_d/stage_d_freeze.json")
    assert freeze == freeze_payload(root)
    assert freeze["canary_instance_ids"] == list(CANARY_INSTANCE_IDS)
    assert freeze["admission"]["paid_execution_authorized"] is False
    assert freeze["admission"]["workflow_dispatch_allowed"] is False


def test_stage_d_claims_remain_separate_from_stage_c() -> None:
    freeze = json.loads(Path("evals/stage_d/stage_d_freeze.json").read_text(encoding="utf-8"))
    prohibited = set(freeze["claims_boundary"]["prohibited"])
    assert "Stage C historical-evidence modification" in prohibited
    assert "Stage C Phase 2 claim" in prohibited
    assert "six-task Phase 1 claim" in prohibited
    assert "twenty-task claim" in prohibited

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.stage_d1_freeze import CANARY_INSTANCE_IDS as STAGE_D1_INSTANCE_IDS
from evals.stage_d1_freeze import freeze_payload as stage_d1_freeze_payload
from evals.stage_d1_freeze import validate_freeze as validate_stage_d1_freeze
from evals.stage_d_freeze import CANARY_INSTANCE_IDS
from evals.stage_d_freeze import validate_freeze


def test_stage_d_freeze_remains_immutable_after_stage_d1_runtime_change() -> None:
    root = Path(".")
    freeze = json.loads((root / "evals/stage_d/stage_d_freeze.json").read_text(encoding="utf-8"))
    assert freeze["canary_instance_ids"] == list(CANARY_INSTANCE_IDS)
    assert freeze["admission"]["paid_execution_authorized"] is False
    assert freeze["admission"]["workflow_dispatch_allowed"] is False
    with pytest.raises(ValueError, match="Stage D Freeze differs"):
        validate_freeze(root, root / "evals/stage_d/stage_d_freeze.json")


def test_stage_d1_freeze_is_current_and_paid_execution_is_closed() -> None:
    root = Path(".")
    freeze = validate_stage_d1_freeze(root, root / "evals/stage_d1/stage_d1_freeze.json")
    assert freeze == stage_d1_freeze_payload(root)
    assert freeze["canary_instance_ids"] == list(STAGE_D1_INSTANCE_IDS)
    assert freeze["admission"]["paid_execution_authorized"] is False
    assert freeze["admission"]["workflow_dispatch_allowed"] is False


def test_stage_d_claims_remain_separate_from_stage_c() -> None:
    freeze = json.loads(Path("evals/stage_d/stage_d_freeze.json").read_text(encoding="utf-8"))
    prohibited = set(freeze["claims_boundary"]["prohibited"])
    assert "Stage C historical-evidence modification" in prohibited
    assert "Stage C Phase 2 claim" in prohibited
    assert "six-task Phase 1 claim" in prohibited
    assert "twenty-task claim" in prohibited

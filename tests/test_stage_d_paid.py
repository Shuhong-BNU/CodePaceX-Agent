from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.benchmark import current_git_commit
from evals import stage_d_freeze, stage_d_paid
from evals.paid_gate import BudgetLedger


ROOT = Path(__file__).resolve().parents[1]
FREEZE = ROOT / "evals/stage_d/stage_d_freeze.json"


def _identities() -> dict[str, str]:
    return stage_d_paid.frozen_identities(ROOT, FREEZE)


def _source_row(instance_id: str) -> dict[str, object]:
    return {
        "instance_id": instance_id,
        "repo": "owner/repo",
        "base_commit": "a" * 40,
        "problem_statement": "Fix the failing bounded test.",
        "platform": None,
        "version": None,
        "environment_setup_commit": None,
    }


def _bundle(tmp_path: Path) -> Path:
    source = tmp_path / "formal-dataset.jsonl"
    source.write_text("".join(json.dumps(_source_row(instance_id)) + "\n" for instance_id in stage_d_freeze.CANARY_INSTANCE_IDS), encoding="utf-8")
    bundle = tmp_path / "tasks.jsonl"
    stage_d_paid.build_task_bundle(source_dataset=source, output=bundle)
    return bundle


def _prepare(tmp_path: Path) -> Path:
    identities = _identities()
    evidence = tmp_path / "stage-d-evidence"
    result = stage_d_paid.prepare(
        root=ROOT, freeze_path=FREEZE, evidence_root=evidence,
        approved_commit=current_git_commit(ROOT), authorization_identity="stage-d-test-authorization",
        supplied_freeze_sha256=identities["freeze_sha256"],
        supplied_runtime_contract_hash=identities["runtime_contract_hash"],
        supplied_pricing_hash=identities["pricing_snapshot_hash"],
    )
    assert result["authorization_cap_cny"] == "30"
    return evidence


def test_stage_d_historical_paid_runner_refuses_changed_runtime_without_provider() -> None:
    with pytest.raises(ValueError, match="Stage D Freeze differs"):
        _identities()


def test_stage_d_historical_runner_cannot_prepare_new_evidence_on_d1_source(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Stage D Freeze differs"):
        _prepare(tmp_path)


def test_stage_d_paid_workflow_is_manual_and_closed_by_default() -> None:
    source = (ROOT / ".github/workflows/stage-d-canary-paid.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in source
    assert "default: false" in source
    assert "if: ${{ inputs.paid_execution }}" in source
    assert "fe0a52d5dfaaa4e0bc48d942a7c8d8fb0371877b" in source

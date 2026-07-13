import json
from pathlib import Path

import pytest

from evals.benchmark import RunManifest, RunRecorder
from evals.goal2_claims import generate_claim_document


RUN_IDS = {
    "mcp-formal-eager", "mcp-formal-deferred",
    "retention-formal-summary_only", "retention-formal-recovery_v1",
    "permission-formal-default", "permission-formal-session_allow",
    "permission-formal-explicit_rules", "permission-formal-sandbox_auto_allow",
    "multi-formal-single", "multi-formal-multi",
    "long-pilot-1", "long-formal-1", "long-formal-2", "long-formal-3",
    "swe-formal", "swe-repeat-1", "swe-repeat-2",
}


def _write_manifests(root: Path, *, commit: str = "a" * 40) -> None:
    for run_id in RUN_IDS:
        profile = {
            "schema_version": 1, "tool_loading": "deferred",
            "compression_profile": "recovery_v1",
            "permission_strategy": "default", "agent_mode": "single",
        }
        RunRecorder(root, RunManifest(
            provider="p", protocol="openai-compat", model_id="m",
            git_commit=commit, prompt_version="v", model_parameters={},
            retry_budget=0, fallback_enabled=False, task_ids=["task"], repetitions=1,
            feature_flags={}, experiment_profile=profile,
            experiment_profile_hash="profile", runtime_contract_hash="runtime",
            benchmark_asset_hash="assets", max_iterations=50,
        ), run_id=run_id)


def test_goal2_claim_generator_materializes_all_registered_claims(tmp_path: Path) -> None:
    _write_manifests(tmp_path)
    document = generate_claim_document(tmp_path)
    ids = {claim.claim_id for claim in document.claims}
    assert "mcp-input-reduction-median" in ids
    assert "long-session-checkpoint-recovery" in ids
    assert "swe-formal-resolve-rate" in ids
    assert len(ids) == len(document.claims)


def test_goal2_claim_generator_requires_every_run_and_one_commit(tmp_path: Path) -> None:
    _write_manifests(tmp_path)
    (tmp_path / "swe-formal" / "manifest.json").unlink()
    with pytest.raises(OSError):
        generate_claim_document(tmp_path)

    tmp_path = tmp_path / "mixed"
    _write_manifests(tmp_path)
    path = tmp_path / "swe-formal" / "manifest.json"
    payload = json.loads(path.read_text())
    payload["git_commit"] = "b" * 40
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="one frozen"):
        generate_claim_document(tmp_path)

import json
from pathlib import Path

import pytest

from evals.benchmark import RunManifest, RunRecorder
from evals.goal2_claims import generate_claim_document, generate_mcp_evidence


RUN_IDS = {
    "mcp-formal-eager", "mcp-formal-deferred",
    "retention-formal-summary_only", "retention-formal-recovery_v1",
    "permission-formal-default", "permission-formal-session_allow",
    "permission-formal-explicit_rules", "permission-formal-sandbox_auto_allow",
    "multi-formal-single", "multi-formal-multi",
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
    assert "long-session-formal-checkpoint-recovery" in ids
    assert "swe-formal-resolve-rate" not in ids
    long_claim = next(item for item in document.claims if item.claim_id == "long-session-formal-checkpoint-recovery")
    assert long_claim.status == "insufficient-data"
    assert long_claim.source_run_ids == []
    assert len(ids) == len(document.claims)


def test_goal2_claim_generator_requires_every_run_but_allows_cross_study_commits(tmp_path: Path) -> None:
    _write_manifests(tmp_path)
    (tmp_path / "mcp-formal-eager" / "manifest.json").unlink()
    with pytest.raises(OSError):
        generate_claim_document(tmp_path)

    tmp_path = tmp_path / "mixed"
    _write_manifests(tmp_path)
    path = tmp_path / "mcp-formal-eager" / "manifest.json"
    payload = json.loads(path.read_text())
    payload["git_commit"] = "b" * 40
    path.write_text(json.dumps(payload))
    assert generate_claim_document(tmp_path).claims


def test_goal2_claim_generator_can_exclude_multi_after_no_go_gate(tmp_path: Path) -> None:
    _write_manifests(tmp_path)
    document = generate_claim_document(tmp_path, include_multi=False)
    assert not any(claim.claim_id.startswith("multi-") for claim in document.claims)


def test_mcp_evidence_uses_trial_level_cohort_not_run_scorability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cohort = {
        "sha256": "cohort", "source_manifest_sha256": "manifest",
        "ledger_sha256": "ledger", "summary": {
            "planned_trials": 300, "terminal_trials": 300,
            "usage_complete_trials": 299, "valid_matched_pairs": 149,
        },
    }
    monkeypatch.setattr("evals.goal2_claims.load_mcp_cohort", lambda _: cohort)
    monkeypatch.setattr("evals.goal2_claims.summarize_mcp_cohort", lambda *_: {
        "valid_matched_pairs": 149,
        "excluded_pairs": {"mcp_one_08/1": "infrastructure_error_usage_unknown"},
    })
    evidence = generate_mcp_evidence(cohort_index=tmp_path / "cohort.json", runs_dir=tmp_path)
    assert evidence["summary"]["valid_matched_pairs"] == 149
    assert "mcp_one_08/1" in evidence["metrics"]["excluded_pairs"]

from __future__ import annotations

from pathlib import Path

import pytest

from evals.benchmark import RunManifest, RunRecorder
from evals.claims import ClaimDocument, compile_claims


def _successful_run(root: Path, run_id: str, *, provider: str = "p", model: str = "m") -> None:
    recorder = RunRecorder(root, RunManifest(provider=provider, model_id=model, feature_flags={"study_feature": "deferred"}), run_id=run_id)
    recorder.capture_event({"type": "usage", "provider_usage": {"prompt_tokens": 12, "completion_tokens": 2}})
    recorder.event("trial_completed", {"task_id": "task", "repetition_id": "1", "status": "success", "duration_seconds": 1.5})
    recorder.finalize({"status": "success"})


def _document(**claim: object) -> ClaimDocument:
    return ClaimDocument.model_validate({"claims": [{
        "claim_id": "claim", "metric_name": "provider_input_tokens", "unit": "tokens",
        "sample_size": 1, "source_run_ids": ["run"], **claim,
    }]})


def test_claim_compile_recomputes_value_and_ignores_input_generated_value(tmp_path: Path) -> None:
    _successful_run(tmp_path, "run")
    compiled = compile_claims(_document(generated_value=9999, status="verified"), tmp_path)
    claim = compiled["claims"][0]
    assert claim["status"] == "verified"
    assert claim["generated_value"] == 12
    assert claim["generation_command"] == "python -m evals.claims compile"


def test_claim_dry_run_and_missing_fields_are_insufficient(tmp_path: Path) -> None:
    recorder = RunRecorder(tmp_path, RunManifest(provider="p", model_id="m"), run_id="run")
    recorder.finalize({"status": "dry_run"})
    claim = compile_claims(_document(), tmp_path)["claims"][0]
    assert claim["status"] == "insufficient-data"


def test_claim_rejects_unregistered_metric() -> None:
    with pytest.raises(ValueError, match="unregistered"):
        _document(metric_name="made-up")


def test_claims_with_mixed_provider_are_not_verified(tmp_path: Path) -> None:
    _successful_run(tmp_path, "run")
    _successful_run(tmp_path, "second", provider="different")
    compiled = compile_claims(_document(source_run_ids=["run", "second"], sample_size=2), tmp_path)
    assert compiled["claims"][0]["status"] == "insufficient-data"
    assert "differ" in " ".join(compiled["claims"][0]["limitations"])

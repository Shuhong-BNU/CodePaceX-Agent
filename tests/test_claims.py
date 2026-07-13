from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from evals.benchmark import RunManifest, RunRecorder
from evals.claims import (
    ClaimDocument,
    claims_schema,
    compile_claims,
    nearest_rank_p95,
)


def _conditions(**changes: object) -> dict[str, object]:
    conditions: dict[str, object] = {
        "provider": "p", "protocol": "openai-compat", "model_id": "m",
        "base_url_origin": "https://provider.example", "git_commit": "abc",
        "prompt_version": "prompt-v1", "model_parameters": {"temperature": None},
        "timeout_seconds": 60, "retry_budget": 0, "fallback_enabled": False,
        "task_ids": ["task"], "repetitions": 1,
        "feature_flags": {"study_feature": "baseline"},
        "allowed_differences": [],
    }
    conditions.update(changes)
    return conditions


def _document(**changes: object) -> ClaimDocument:
    claim: dict[str, object] = {
        "claim_id": "claim",
        "description_zh": "可验证指标",
        "description_en": "Verifiable metric",
        "metric_name": "provider_input_tokens",
        "aggregation": "sum",
        "unit": "tokens",
        "sample_size": 1,
        "experiment_conditions": _conditions(),
        "source_run_ids": ["run"],
    }
    claim.update(changes)
    return ClaimDocument.model_validate({"claims": [claim]})


def _successful_run(
    root: Path,
    run_id: str,
    *,
    provider: str = "p",
    study_feature: str = "baseline",
    task_id: str = "task",
    repetition_id: str = "1",
    prompt_tokens: int = 12,
    runtime_tools: str = "tools",
    numerator: int | None = None,
    denominator: int | None = None,
) -> None:
    recorder = RunRecorder(root, RunManifest(
        provider=provider, protocol="openai-compat", model_id="m",
        base_url_origin="https://provider.example/v1", git_commit="abc",
        prompt_version="prompt-v1", model_parameters={"temperature": None},
        timeout_seconds=60, retry_budget=0, fallback_enabled=False,
        task_ids=["task"], repetitions=1,
        feature_flags={"study_feature": study_feature},
    ), run_id=run_id)
    recorder.event("trial_started", {
        "task_id": task_id, "repetition_id": repetition_id,
    })
    recorder.capture_event({
        "type": "runtime_manifest", "request_index": 1,
        "provider": provider, "protocol": "openai-compat", "model_id": "m",
        "system_sha256": "system", "tools_sha256": runtime_tools,
        "messages_sha256": "messages", "task_id": task_id,
        "repetition_id": repetition_id,
    })
    recorder.capture_event({
        "type": "usage", "request_index": 1,
        "provider_usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 2},
        "task_id": task_id, "repetition_id": repetition_id,
    })
    terminal = {
        "task_id": task_id, "repetition_id": repetition_id,
        "status": "success", "duration_seconds": 1.5,
    }
    if numerator is not None and denominator is not None:
        terminal.update({"numerator": numerator, "denominator": denominator})
    recorder.event("trial_completed", terminal)
    recorder.finalize({"status": "success"})


def test_claim_compile_recomputes_value_and_sample_size(tmp_path: Path) -> None:
    _successful_run(tmp_path, "run")
    compiled = compile_claims(
        _document(generated_value=9999, status="verified"), tmp_path,
    )["claims"][0]
    assert compiled["status"] == "verified"
    assert compiled["generated_value"] == 12
    assert compiled["sample_size"] == 1
    assert compiled["generation_command"] == "python -m evals.claims compile"


def test_dry_run_and_missing_runtime_are_insufficient(tmp_path: Path) -> None:
    recorder = RunRecorder(tmp_path, RunManifest(provider="p", model_id="m"), run_id="run")
    recorder.finalize({"status": "dry_run"})
    claim = compile_claims(_document(), tmp_path)["claims"][0]
    assert claim["status"] == "insufficient-data"
    assert claim["sample_size"] == 0


def test_claim_rejects_unregistered_metric_and_allowed_difference() -> None:
    with pytest.raises(ValueError, match="unregistered metric"):
        _document(metric_name="made-up")
    with pytest.raises(ValueError, match="allowed_differences"):
        _document(experiment_conditions=_conditions(
            allowed_differences=["manifest.feature_flags.unknown"],
        ))


def test_study_feature_is_not_automatically_ignored(tmp_path: Path) -> None:
    _successful_run(tmp_path, "run")
    _successful_run(tmp_path, "second", study_feature="improved")
    source = ["run", "second"]
    blocked = compile_claims(_document(
        source_run_ids=source, sample_size=2,
    ), tmp_path)["claims"][0]
    assert blocked["status"] == "insufficient-data"
    assert any("study_feature" in item for item in blocked["limitations"])

    allowed = compile_claims(_document(
        source_run_ids=source, sample_size=2,
        experiment_conditions=_conditions(
            allowed_differences=["manifest.feature_flags.study_feature"],
        ),
    ), tmp_path)["claims"][0]
    assert allowed["status"] == "verified"
    assert allowed["evidence_summary"]["observed_differences"] == [
        "manifest.feature_flags.study_feature"
    ]


def test_provider_or_runtime_identity_difference_blocks_claim(tmp_path: Path) -> None:
    _successful_run(tmp_path, "run")
    _successful_run(tmp_path, "second", provider="different")
    claim = compile_claims(_document(
        source_run_ids=["run", "second"], sample_size=2,
    ), tmp_path)["claims"][0]
    assert claim["status"] == "insufficient-data"
    assert any("provider" in item.lower() for item in claim["limitations"])


def test_experiment_conditions_must_declare_all_feature_flags(tmp_path: Path) -> None:
    _successful_run(tmp_path, "run")
    manifest_path = tmp_path / "run" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["feature_flags"]["deferred_tools"] = True
    manifest_path.write_text(json.dumps(manifest))
    claim = compile_claims(_document(), tmp_path)["claims"][0]
    assert claim["status"] == "insufficient-data"
    assert any("omit feature flags" in item for item in claim["limitations"])


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([7], 7),
        ([1, 3, 2], 3),
        ([1, 2, 3, 4], 4),
        ([5, 1, 5, 2, 3], 5),
        ([4, 1, 3, 2], 4),
    ],
)
def test_p95_uses_documented_nearest_rank(values: list[float], expected: float) -> None:
    assert nearest_rank_p95(values) == expected
    assert nearest_rank_p95(list(reversed(values))) == expected
    assert math.ceil(0.95 * len(values)) - 1 == len(sorted(values)) - 1


def test_rate_uses_pooled_numerator_and_denominator(tmp_path: Path) -> None:
    _successful_run(tmp_path, "run", numerator=1, denominator=2)
    _successful_run(tmp_path, "second", numerator=9, denominator=10)
    claim = compile_claims(_document(
        metric_name="rate", aggregation="pooled_rate", unit="ratio",
        source_run_ids=["run", "second"], sample_size=2,
    ), tmp_path)["claims"][0]
    assert claim["status"] == "verified"
    assert claim["generated_value"] == pytest.approx(10 / 12)
    assert claim["generated_value"] != pytest.approx((0.5 + 0.9) / 2)


def test_ab_requires_exact_pairs_and_explicit_runtime_difference(tmp_path: Path) -> None:
    _successful_run(tmp_path, "base", prompt_tokens=100, runtime_tools="base-tools")
    _successful_run(
        tmp_path, "improved", prompt_tokens=80, study_feature="improved",
        runtime_tools="improved-tools",
    )
    base_conditions = _conditions(
        baseline_run_ids=["base"], improved_run_ids=["improved"],
        allowed_differences=["manifest.feature_flags.study_feature"],
    )
    blocked = compile_claims(_document(
        metric_name="ab_reduction_percent", aggregation="mean", unit="percent",
        source_run_ids=["base", "improved"],
        experiment_conditions=base_conditions,
    ), tmp_path)["claims"][0]
    assert blocked["status"] == "insufficient-data"
    assert any("runtime.tools_sha256" in item for item in blocked["limitations"])

    base_conditions["allowed_differences"] = [
        "manifest.feature_flags.study_feature", "runtime.tools_sha256",
    ]
    verified = compile_claims(_document(
        metric_name="ab_reduction_percent", aggregation="mean", unit="percent",
        source_run_ids=["base", "improved"],
        experiment_conditions=base_conditions,
    ), tmp_path)["claims"][0]
    assert verified["status"] == "verified"
    assert verified["generated_value"] == pytest.approx(20.0)
    assert verified["sample_size"] == 1


def test_ab_unbalanced_trial_pairs_are_insufficient(tmp_path: Path) -> None:
    _successful_run(tmp_path, "base", prompt_tokens=100)
    _successful_run(
        tmp_path, "improved", prompt_tokens=80, study_feature="improved",
        repetition_id="2",
    )
    claim = compile_claims(_document(
        metric_name="ab_reduction_percent", aggregation="mean", unit="percent",
        source_run_ids=["base", "improved"],
        experiment_conditions=_conditions(
            baseline_run_ids=["base"], improved_run_ids=["improved"],
            allowed_differences=["manifest.feature_flags.study_feature"],
        ),
    ), tmp_path)["claims"][0]
    assert claim["status"] == "insufficient-data"
    assert claim["sample_size"] == 0


def test_schema_is_generated_from_strict_pydantic_models() -> None:
    schema = claims_schema()
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["Claim"]["additionalProperties"] is False
    assert schema["$defs"]["ExperimentConditions"]["additionalProperties"] is False
    committed = json.loads(Path("evals/claims.schema.json").read_text())
    assert committed == schema


def test_invalid_source_run_path_is_rejected() -> None:
    with pytest.raises(ValueError, match="Run ID"):
        _document(source_run_ids=["../escape"])

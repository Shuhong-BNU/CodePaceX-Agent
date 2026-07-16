from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from evals.benchmark import RunManifest, RunRecorder
from codepacex.experiments import ExperimentProfile, combined_runtime_hash
from evals.claims import (
    ClaimDocument,
    _rate_trials,
    claims_schema,
    compile_claims,
    nearest_rank_p95,
)


def _conditions(**changes: object) -> dict[str, object]:
    profile = _profile()
    conditions: dict[str, object] = {
        "provider": "p", "protocol": "openai-compat", "model_id": "m",
        "base_url_origin": "https://provider.example", "git_commit": "abc",
        "prompt_version": "prompt-v1", "model_parameters": {"temperature": None},
        "timeout_seconds": 60, "retry_budget": 0, "fallback_enabled": False,
        "task_ids": ["task"], "repetitions": 1,
        "feature_flags": {},
        "experiment_profile": profile.canonical_payload(),
        "experiment_profile_hash": profile.profile_hash(),
        "runtime_contract_hash": profile.runtime_contract_hash(),
        "benchmark_asset_hash": "assets",
        "max_iterations": 50,
        "allowed_differences": [],
    }
    conditions.update(changes)
    return conditions


def _profile() -> ExperimentProfile:
    return ExperimentProfile.model_validate({
        "tool_loading": "deferred",
        "compression_profile": "recovery_v1",
        "permission_strategy": "default",
        "agent_mode": "single",
    })


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


def test_no_evidence_insufficient_data_claim_is_preserved_without_fake_runs(tmp_path: Path) -> None:
    document = ClaimDocument.model_validate({"claims": [{
        "claim_id": "long-session-formal-checkpoint-recovery",
        "description_zh": "延期的正式长会话",
        "description_en": "Deferred formal long session",
        "metric_name": "checkpoint_recovery_rate", "aggregation": "pooled_rate",
        "unit": "ratio", "sample_size": 3, "status": "insufficient-data",
        "limitations": ["Deferred to a follow-up Goal."],
    }]})
    compiled = compile_claims(document, tmp_path)
    claim = compiled["claims"][0]
    assert claim["status"] == "insufficient-data"
    assert claim["source_run_ids"] == []
    assert claim["evidence_summary"]["source_run_count"] == 0


def _successful_run(
    root: Path,
    run_id: str,
    *,
    provider: str = "p",
    feature_flags: dict[str, object] | None = None,
    task_id: str = "task",
    repetition_id: str = "1",
    prompt_tokens: int = 12,
    completion_tokens: int = 2,
    cache_tokens: int = 0,
    tools_bytes: int = 64,
    actual_cny: str = "0.01",
    terminal_fields: dict[str, object] | None = None,
    runtime_tools: str = "tools",
    numerator: int | None = None,
    denominator: int | None = None,
) -> None:
    profile = _profile()
    recorder = RunRecorder(root, RunManifest(
        provider=provider, protocol="openai-compat", model_id="m",
        base_url_origin="https://provider.example/v1", git_commit="abc",
        prompt_version="prompt-v1", model_parameters={"temperature": None},
        timeout_seconds=60, retry_budget=0, fallback_enabled=False,
        task_ids=["task"], repetitions=1,
        feature_flags=feature_flags or {},
        experiment_profile=profile.canonical_payload(),
        experiment_profile_hash=profile.profile_hash(),
        runtime_contract_hash=profile.runtime_contract_hash(),
        benchmark_asset_hash="assets", max_iterations=50,
    ), run_id=run_id)
    recorder.event("trial_started", {
        "task_id": task_id, "repetition_id": repetition_id,
    })
    recorder.capture_event({
        "type": "runtime_manifest", "request_index": 1,
        "provider": provider, "protocol": "openai-compat", "model_id": "m",
        "system_sha256": "system", "tools_sha256": runtime_tools,
        "messages_sha256": "messages", "tools_bytes": tools_bytes,
        "task_id": task_id,
        "repetition_id": repetition_id,
        "experiment_profile_hash": profile.profile_hash(),
        "runtime_contract_hash": profile.runtime_contract_hash(),
        "combined_runtime_hash": combined_runtime_hash(
            profile_hash=profile.profile_hash(), system_sha256="system",
            tools_sha256=runtime_tools,
        ),
    })
    recorder.capture_event({
        "type": "usage", "request_index": 1,
        "provider_usage": {
            "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
            "prompt_tokens_details": {"cached_tokens": cache_tokens},
        },
        "task_id": task_id, "repetition_id": repetition_id,
    })
    terminal = {
        "task_id": task_id, "repetition_id": repetition_id,
        "status": "success", "duration_seconds": 1.5, "actual_cny": actual_cny,
    }
    if numerator is not None and denominator is not None:
        terminal.update({"numerator": numerator, "denominator": denominator})
    terminal.update(terminal_fields or {})
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


@pytest.mark.parametrize(("metric", "expected"), [
    ("provider_output_tokens", 2),
    ("provider_cache_tokens", 3),
    ("runtime_tool_schema_bytes", 64),
    ("actual_cost_cny", 0.01),
])
def test_goal2_registered_measurements_use_raw_run_artifacts(
    tmp_path: Path, metric: str, expected: float,
) -> None:
    _successful_run(tmp_path, "run", cache_tokens=3)
    compiled = compile_claims(_document(metric_name=metric), tmp_path)["claims"][0]
    assert compiled["status"] == "verified"
    assert compiled["generated_value"] == pytest.approx(expected)


def test_multi_agent_terminal_measurements_are_registered(tmp_path: Path) -> None:
    _successful_run(tmp_path, "run", terminal_fields={
        "input_tokens": 30, "output_tokens": 4, "provider_request_count": 2,
        "grade": {"integration_conflict_markers": False, "maximum_parallel_children": 2},
    })
    for metric, expected in {
        "trial_input_tokens": 30, "trial_output_tokens": 4,
        "provider_request_count": 2, "integration_conflict_count": 0,
        "maximum_parallel_children": 2,
    }.items():
        claim = compile_claims(_document(metric_name=metric), tmp_path)["claims"][0]
        assert claim["status"] == "verified"
        assert claim["generated_value"] == expected


def test_dry_run_and_missing_runtime_are_insufficient(tmp_path: Path) -> None:
    recorder = RunRecorder(tmp_path, RunManifest(provider="p", model_id="m"), run_id="run")
    recorder.finalize({"status": "dry_run"})
    claim = compile_claims(_document(), tmp_path)["claims"][0]
    assert claim["status"] == "insufficient-data"
    assert claim["sample_size"] == 1
    assert claim["evidence_summary"]["measured_sample_size"] == 0


def test_unscorable_infrastructure_run_is_excluded_from_formal_claims(tmp_path: Path) -> None:
    _successful_run(tmp_path, "run")
    result_path = tmp_path / "run" / "result.json"
    result = json.loads(result_path.read_text())
    result.update({"status": "infrastructure_error", "scorable": False})
    result_path.write_text(json.dumps(result), encoding="utf-8")
    claim = compile_claims(_document(), tmp_path)["claims"][0]
    assert claim["status"] == "insufficient-data"
    assert any("infrastructure" in item.lower() for item in claim["limitations"])


def test_claim_rejects_unregistered_metric_and_allowed_difference() -> None:
    with pytest.raises(ValueError, match="unregistered metric"):
        _document(metric_name="made-up")
    with pytest.raises(ValueError, match="allowed_differences"):
        _document(experiment_conditions=_conditions(
            allowed_differences=["manifest.feature_flags.unknown"],
        ))


def test_nonempty_feature_flags_are_not_eligible_for_claims(tmp_path: Path) -> None:
    _successful_run(tmp_path, "run")
    _successful_run(tmp_path, "second", feature_flags={"study_feature": "improved"})
    source = ["run", "second"]
    blocked = compile_claims(_document(
        source_run_ids=source, sample_size=2,
    ), tmp_path)["claims"][0]
    assert blocked["status"] == "insufficient-data"
    assert any("feature_flags" in item for item in blocked["limitations"])
    with pytest.raises(ValueError, match="allowed_differences"):
        _document(experiment_conditions=_conditions(
            allowed_differences=["manifest.feature_flags.study_feature"],
        ))


def test_v1_run_is_inspection_only_for_goal2_claims(tmp_path: Path) -> None:
    _successful_run(tmp_path, "run")
    for name in ("manifest.json", "result.json"):
        path = tmp_path / "run" / name
        payload = json.loads(path.read_text())
        payload["schema_version"] = 1
        path.write_text(json.dumps(payload))
    claim = compile_claims(_document(), tmp_path)["claims"][0]
    assert claim["status"] == "insufficient-data"
    assert any("inspection-only" in item for item in claim["limitations"])


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


def test_budget_blocked_trial_is_excluded_from_capability_rate_denominator(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    (run / "events.jsonl").write_text(json.dumps({
        "type": "trial_completed", "task_id": "blocked", "repetition_id": "1",
        "status": "budget_blocked", "numerator": 0, "denominator": 1,
    }) + "\n", encoding="utf-8")
    assert _rate_trials(run) == {}


def test_ab_requires_exact_pairs_and_explicit_runtime_difference(tmp_path: Path) -> None:
    _successful_run(tmp_path, "base", prompt_tokens=100, runtime_tools="base-tools")
    _successful_run(
        tmp_path, "improved", prompt_tokens=80,
        runtime_tools="improved-tools",
    )
    base_conditions = _conditions(
        baseline_run_ids=["base"], improved_run_ids=["improved"],
        allowed_differences=[],
    )
    blocked = compile_claims(_document(
        metric_name="ab_reduction_percent", aggregation="mean", unit="percent",
        source_run_ids=["base", "improved"],
        experiment_conditions=base_conditions,
    ), tmp_path)["claims"][0]
    assert blocked["status"] == "insufficient-data"
    assert any("runtime.tools_sha256" in item for item in blocked["limitations"])

    base_conditions["allowed_differences"] = [
        "runtime.tools_sha256", "runtime.combined_runtime_hash",
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
        tmp_path, "improved", prompt_tokens=80,
        repetition_id="2",
    )
    claim = compile_claims(_document(
        metric_name="ab_reduction_percent", aggregation="mean", unit="percent",
        source_run_ids=["base", "improved"],
        experiment_conditions=_conditions(
            baseline_run_ids=["base"], improved_run_ids=["improved"],
            allowed_differences=[],
        ),
    ), tmp_path)["claims"][0]
    assert claim["status"] == "insufficient-data"
    assert claim["sample_size"] == 1
    assert claim["evidence_summary"]["measured_sample_size"] == 0


def test_runtime_provider_identity_is_not_changed_by_request_count(tmp_path: Path) -> None:
    _successful_run(tmp_path, "run")
    _successful_run(tmp_path, "second", repetition_id="2")
    for name in ("runtime-events.jsonl",):
        path = tmp_path / "second" / name
        record = json.loads(path.read_text())
        record["request_index"] = 2
        path.write_text(path.read_text() + json.dumps(record) + "\n")
    claim = compile_claims(_document(
        source_run_ids=["run", "second"], sample_size=2,
        experiment_conditions=_conditions(
            allowed_differences=[
                "runtime.system_sha256", "runtime.tools_sha256",
                "runtime.messages_sha256", "runtime.tools_bytes",
                "runtime.combined_runtime_hash",
            ],
        ),
    ), tmp_path)["claims"][0]
    assert not any(
        "runtime.provider" in item for item in claim["limitations"]
    )


@pytest.mark.parametrize("declared", [1, 3])
def test_claim_requires_exact_declared_sample_size(tmp_path: Path, declared: int) -> None:
    _successful_run(tmp_path, "run")
    _successful_run(tmp_path, "second", repetition_id="2")
    claim = compile_claims(_document(
        source_run_ids=["run", "second"], sample_size=declared,
    ), tmp_path)["claims"][0]
    assert claim["status"] == "insufficient-data"
    assert claim["sample_size"] == declared
    assert claim["evidence_summary"]["measured_sample_size"] == 2


@pytest.mark.parametrize("baseline_status,improved_status", [
    ("task_failure", "success"), ("success", "task_failure"),
])
def test_ab_requires_success_on_both_sides(
    tmp_path: Path, baseline_status: str, improved_status: str,
) -> None:
    _successful_run(tmp_path, "base", prompt_tokens=100)
    _successful_run(tmp_path, "improved", prompt_tokens=80)
    for run_id, status in (("base", baseline_status), ("improved", improved_status)):
        path = tmp_path / run_id / "events.jsonl"
        records = [json.loads(line) for line in path.read_text().splitlines()]
        records[-1]["status"] = status
        path.write_text("\n".join(json.dumps(item) for item in records) + "\n")
    claim = compile_claims(_document(
        metric_name="ab_reduction_percent", aggregation="mean", unit="percent",
        source_run_ids=["base", "improved"],
        experiment_conditions=_conditions(baseline_run_ids=["base"], improved_run_ids=["improved"]),
    ), tmp_path)["claims"][0]
    assert claim["status"] == "insufficient-data"


def test_ab_rejects_multiple_terminal_attempts(tmp_path: Path) -> None:
    _successful_run(tmp_path, "base", prompt_tokens=100)
    _successful_run(tmp_path, "improved", prompt_tokens=80)
    path = tmp_path / "base" / "events.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    duplicate = dict(records[-1])
    duplicate["attempt_id"] = 2
    records.append(duplicate)
    path.write_text("\n".join(json.dumps(item) for item in records) + "\n")
    claim = compile_claims(_document(
        metric_name="ab_reduction_percent", aggregation="mean", unit="percent",
        source_run_ids=["base", "improved"],
        experiment_conditions=_conditions(baseline_run_ids=["base"], improved_run_ids=["improved"]),
    ), tmp_path)["claims"][0]
    assert claim["status"] == "insufficient-data"


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

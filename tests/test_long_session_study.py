import json
import inspect
from pathlib import Path

import pytest

from evals.goal2_studies import load_studies
from evals.long_session_study import (
    _CycleTelemetry,
    _write_checkpoint,
    dry_run,
    frozen_profile,
    load_latest_checkpoint,
    recovery_rate_fields,
    schedule,
    strict_cycle_grade,
    validate_checkpoint_chain,
)
import evals.long_session_study as long_session_study
from evals.benchmark import RunManifest, RunRecorder
from codepacex.experiments import combined_runtime_hash


STUDIES = Path("evals/goal2/studies.yaml")


def test_long_session_schedule_freezes_all_four_real_sessions() -> None:
    studies = load_studies(STUDIES)
    pilot = schedule(studies, kind="pilot", index=1)
    formal = schedule(studies, kind="formal", index=3)
    assert (pilot["duration_hours"], pilot["cycle_count"], pilot["restart_after_cycle"]) == (2, 8, 4)
    assert (formal["duration_hours"], formal["cycle_count"], formal["restart_after_cycle"]) == (8, 32, 16)
    assert pilot["checkpoint_every_cycles"] == formal["checkpoint_every_cycles"] == 2
    assert pilot["maximum_provider_requests_per_cycle"] == 10


def test_checkpoint_chain_detects_tampering(tmp_path: Path) -> None:
    path = tmp_path / "checkpoints.json"
    _write_checkpoint(path, {"schema_version": 1, "run_id": "long", "completed_cycle": 2})
    latest = _write_checkpoint(path, {"schema_version": 1, "run_id": "long", "completed_cycle": 4})
    assert load_latest_checkpoint(path) == latest
    payload = json.loads(path.read_text())
    payload[0]["completed_cycle"] = 3
    with pytest.raises(ValueError, match="content hash"):
        validate_checkpoint_chain(payload)


def test_cycle_grader_requires_exact_json() -> None:
    expected = '{"cycle":1,"marker":"abc","status":"ok"}'
    assert strict_cycle_grade(expected, cycle=1, marker="abc")[0]
    assert not strict_cycle_grade(expected + "\nextra", cycle=1, marker="abc")[0]
    assert not strict_cycle_grade('{"cycle":1,"marker":"wrong","status":"ok"}', cycle=1, marker="abc")[0]


def test_recovery_rate_fields_only_count_post_restart_probe() -> None:
    assert recovery_rate_fields("success", recovery_probe=True) == {
        "numerator": 1, "denominator": 1,
    }
    assert recovery_rate_fields("task_failure", recovery_probe=True) == {
        "numerator": 0, "denominator": 1,
    }
    assert recovery_rate_fields("success", recovery_probe=False) == {}


def test_long_session_telemetry_persists_runtime_and_provider_usage(tmp_path: Path) -> None:
    profile = frozen_profile()
    recorder = RunRecorder(tmp_path, RunManifest(
        experiment_profile=profile.canonical_payload(),
        experiment_profile_hash=profile.profile_hash(),
        runtime_contract_hash=profile.runtime_contract_hash(),
    ), run_id="long")
    telemetry = _CycleTelemetry(recorder, "long-pilot-1", 1)
    telemetry({
        "type": "runtime_manifest", "provider": "p", "protocol": "openai-compat",
        "model_id": "m", "system_sha256": "s", "tools_sha256": "t",
        "messages_sha256": "msg", "tools_bytes": 42,
        "experiment_profile_hash": profile.profile_hash(),
        "runtime_contract_hash": profile.runtime_contract_hash(),
        "combined_runtime_hash": combined_runtime_hash(
            profile_hash=profile.profile_hash(), system_sha256="s", tools_sha256="t",
        ),
    })
    telemetry({
        "type": "usage", "provider_usage": {"prompt_tokens": 3, "completion_tokens": 2},
        "request_input_tokens": 3, "request_output_tokens": 2,
    })
    runtime = json.loads((recorder.path / "runtime-events.jsonl").read_text())
    usage = json.loads((recorder.path / "usage.json").read_text())["requests"][0]
    assert runtime["tools_bytes"] == 42
    assert usage["provider_usage"]["prompt_tokens"] == 3


def test_long_session_dry_run_is_not_real_wall_clock_evidence(tmp_path: Path) -> None:
    recorder = dry_run(
        root=Path.cwd(), studies_path=STUDIES, runs_dir=tmp_path,
        run_prefix="long", kind="pilot", index=1,
    )
    result = json.loads((recorder.path / "result.json").read_text())
    assert result["status"] == "dry_run" and result["scorable"] is False


def test_long_session_uses_in_process_request_bridge_without_parent_lock() -> None:
    source = inspect.getsource(long_session_study.execute)
    assert "maximum_requests=int(spec[\"maximum_provider_requests_per_cycle\"])" not in source
    assert "provider_request_budget_scope(" in source
    assert "with gate.locked()" not in source

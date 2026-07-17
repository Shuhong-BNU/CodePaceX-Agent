import json
import inspect
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from evals.goal2_studies import load_studies
from evals.long_session_study import (
    _CycleTelemetry,
    _aggregate_cycle_statuses,
    _resumed_cycle_statuses,
    _write_checkpoint,
    budget_trial_id,
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


def test_budget_trial_id_is_run_scoped_and_resume_stable() -> None:
    first = budget_trial_id(run_id="run-a", task_id="formal-1", cycle=2)
    assert first == "long_session/run-a/formal-1/cycle-2"
    assert first == budget_trial_id(run_id="run-a", task_id="formal-1", cycle=2)
    assert first != budget_trial_id(run_id="run-b", task_id="formal-1", cycle=2)


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


def _record_cycle(recorder: RunRecorder, *, cycle: int, status: str) -> None:
    recorder.event("trial_completed", {
        "task_id": "long-pilot-1", "repetition_id": str(cycle),
        "attempt_id": 1, "status": status,
    })


def test_resume_aggregate_preserves_historical_failure_after_new_successes(tmp_path: Path) -> None:
    recorder = RunRecorder(tmp_path, RunManifest(), run_id="resume-failure")
    _record_cycle(recorder, cycle=1, status="task_failure")
    _record_cycle(recorder, cycle=2, status="success")

    historical = _resumed_cycle_statuses(
        recorder, task_id="long-pilot-1", completed_cycle=2,
    )
    assert _aggregate_cycle_statuses(
        [*historical, "success", "success"], cycle_count=4,
    ) == "task_failure"


def test_resume_aggregate_all_success_and_zero_remaining_preserve_status(tmp_path: Path) -> None:
    recorder = RunRecorder(tmp_path, RunManifest(), run_id="resume-success")
    for cycle in range(1, 5):
        _record_cycle(recorder, cycle=cycle, status="success")

    historical = _resumed_cycle_statuses(
        recorder, task_id="long-pilot-1", completed_cycle=4,
    )
    assert _aggregate_cycle_statuses(historical, cycle_count=4) == "success"


def test_resume_aggregate_all_historical_and_new_successes_is_success(tmp_path: Path) -> None:
    recorder = RunRecorder(tmp_path, RunManifest(), run_id="resume-new-success")
    _record_cycle(recorder, cycle=1, status="success")

    historical = _resumed_cycle_statuses(
        recorder, task_id="long-pilot-1", completed_cycle=1,
    )
    assert _aggregate_cycle_statuses([*historical, "success"], cycle_count=2) == "success"


def test_resume_zero_remaining_historical_failure_is_not_rewritten(tmp_path: Path) -> None:
    recorder = RunRecorder(tmp_path, RunManifest(), run_id="resume-zero-failure")
    for cycle, status in enumerate(("success", "task_failure", "success"), start=1):
        _record_cycle(recorder, cycle=cycle, status=status)

    historical = _resumed_cycle_statuses(
        recorder, task_id="long-pilot-1", completed_cycle=3,
    )
    assert _aggregate_cycle_statuses(historical, cycle_count=3) == "task_failure"


def test_resume_cycle_statuses_reject_missing_or_unexpected_cycles(tmp_path: Path) -> None:
    recorder = RunRecorder(tmp_path, RunManifest(), run_id="resume-mismatch")
    _record_cycle(recorder, cycle=1, status="success")
    _record_cycle(recorder, cycle=3, status="success")

    with pytest.raises(ValueError, match="checkpoint/event cycle mismatch"):
        _resumed_cycle_statuses(recorder, task_id="long-pilot-1", completed_cycle=2)


def test_resume_cycle_statuses_reject_duplicate_cycle_events(tmp_path: Path) -> None:
    recorder = RunRecorder(tmp_path, RunManifest(), run_id="resume-duplicate")
    _record_cycle(recorder, cycle=1, status="success")
    _record_cycle(recorder, cycle=1, status="success")

    with pytest.raises(ValueError, match="duplicate terminal Trial event"):
        _resumed_cycle_statuses(recorder, task_id="long-pilot-1", completed_cycle=1)


def test_execute_resume_skips_completed_cycle_and_preserves_prior_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = RunManifest(git_commit="commit", pricing_snapshot_hash="pricing")
    recorder = RunRecorder(tmp_path / "runs", manifest, run_id="long-resume")
    _record_cycle(recorder, cycle=1, status="task_failure")
    recorder.finalize({"status": "task_failure"})
    task_id = "long-pilot-1"
    marker = long_session_study.canonical_hash({"task_id": task_id, "commit": "commit"})[:24]
    checkpoint_path = tmp_path / "checkpoint.json"
    _write_checkpoint(checkpoint_path, {
        "schema_version": 1, "run_id": "long-resume", "task_id": task_id,
        "started_at": "2000-01-01T00:00:00Z", "checkpointed_at": "2000-01-01T00:00:00Z",
        "completed_cycle": 1, "restart_completed": False,
        "marker_sha256": long_session_study.canonical_hash(marker), "conversation": [],
        "env_injected": False, "ltm_injected": False,
    })
    monkeypatch.setenv("TEST_LONG_SESSION_KEY", "offline-test-key")
    monkeypatch.setattr(long_session_study, "load_studies", lambda _: object())
    monkeypatch.setattr(long_session_study, "schedule", lambda *_args, **_kwargs: {
        "cycle_count": 2, "restart_after_cycle": 1, "workload_interval_minutes": 1,
        "checkpoint_every_cycles": 1, "maximum_provider_requests_per_cycle": 1,
    })
    monkeypatch.setattr(long_session_study, "load_pilot_config", lambda _: SimpleNamespace(
        api_key_env="TEST_LONG_SESSION_KEY", provider="offline", protocol="openai-compat",
        base_url="https://offline.invalid", model_id="offline", model_parameters=SimpleNamespace(
            max_output_tokens=1,
        ),
    ))
    monkeypatch.setattr(long_session_study, "load_pricing", lambda _: object())
    monkeypatch.setattr(long_session_study, "pricing_snapshot_hash", lambda _: "pricing")
    monkeypatch.setattr(long_session_study, "build_manifest", lambda **_kwargs: RunManifest(
        git_commit="commit",
    ))
    monkeypatch.setattr(long_session_study, "_runtime_secrets", lambda _: ())
    monkeypatch.setattr(long_session_study, "_new_runtime", lambda **_kwargs: object())
    monkeypatch.setattr(long_session_study, "PaidRunGate", lambda **_kwargs: SimpleNamespace(
        trial_accounting=lambda _trial_id: {
            "budget_blocked": False, "active_reservation": None,
            "request_count": 0, "actual_cny": "0.000000",
        },
    ))
    monkeypatch.setattr(long_session_study, "provider_request_budget_scope", lambda *_args, **_kwargs: nullcontext())
    cycles: list[int] = []

    async def successful_cycle(**kwargs):
        cycles.append(kwargs["cycle"])
        return "success", {}, []

    monkeypatch.setattr(long_session_study, "_run_cycle", successful_cycle)
    resumed = long_session_study.execute(
        root=tmp_path, studies_path=tmp_path / "studies.yaml", runs_dir=tmp_path / "runs",
        run_id="long-resume", kind="pilot", index=1,
        pricing_snapshot=tmp_path / "pricing.yaml", budget_authorization=tmp_path / "auth.yaml",
        budget_ledger=tmp_path / "ledger.json", checkpoint_path=checkpoint_path,
        confirmed=True, resume=True,
    )
    assert cycles == [2]
    assert json.loads((resumed.path / "result.json").read_text())["status"] == "task_failure"

import json
from pathlib import Path

import pytest

from evals.goal2_studies import load_studies
from evals.long_session_study import (
    _write_checkpoint,
    dry_run,
    load_latest_checkpoint,
    recovery_rate_fields,
    schedule,
    strict_cycle_grade,
    validate_checkpoint_chain,
)


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


def test_long_session_dry_run_is_not_real_wall_clock_evidence(tmp_path: Path) -> None:
    recorder = dry_run(
        root=Path.cwd(), studies_path=STUDIES, runs_dir=tmp_path,
        run_prefix="long", kind="pilot", index=1,
    )
    result = json.loads((recorder.path / "result.json").read_text())
    assert result["status"] == "dry_run" and result["scorable"] is False

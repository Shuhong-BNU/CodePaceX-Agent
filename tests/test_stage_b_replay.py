from __future__ import annotations

from pathlib import Path

from evals.stage_b_replay import replay_trace
from evals.benchmark import RunManifest, RunRecorder


def test_replay_is_offline_and_preserves_source(tmp_path: Path) -> None:
    source = Path("evals/fixtures/stage_b/regression.jsonl")
    original = source.read_text(encoding="utf-8")
    result = replay_trace(source, tmp_path / "replay")
    assert result["replay_only"] is True
    assert result["provider_requests"] == 0
    assert result["formal_experiment"] is False
    assert source.read_text(encoding="utf-8") == original
    assert (tmp_path / "replay" / "validation-summary.json").exists()


def test_unreproduced_fixture_remains_incomplete(tmp_path: Path) -> None:
    result = replay_trace(Path("evals/fixtures/stage_b/unreproduced_edit.jsonl"), tmp_path / "replay")
    assert result["reproduction"] is None


def test_recorder_adds_validation_files_without_changing_old_run_shape(tmp_path: Path) -> None:
    recorder = RunRecorder(tmp_path, RunManifest(kind="dry-run", model="model", provider="provider"), run_id="validation")
    recorder.capture_event({
        "type": "validation", "schema_version": 1, "event_id": "one:1",
        "event_sequence": 1, "validation_session_id": "one", "trial_id": None,
        "agent_id": "agent", "parent_agent_id": None, "event_type": "validation_blocked",
        "payload": {"reason": "missing reproduction"},
    })
    recorder.finalize({"status": "dry_run"})
    assert (recorder.path / "validation-events.jsonl").exists()
    assert (recorder.path / "validation-summary.json").exists()
    assert "## Validation" in (recorder.path / "report.md").read_text(encoding="utf-8")

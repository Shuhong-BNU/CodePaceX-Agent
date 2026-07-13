from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.benchmark import RunManifest, RunRecorder, SecretRedactor, canonical_hash


def _manifest(**overrides):
    values = {
        "experiment_kind": "pilot", "provider": "bailian-qwen37-max",
        "model_id": "qwen3.7-max-2026-06-08", "protocol": "openai-compat",
        "git_commit": "abc", "experiment_config_hash": "cfg",
        "system_prompt_hash": "prompt", "tool_schema_hash": "tools",
    }
    values.update(overrides)
    return RunManifest(**values)


def test_manifest_serialization_and_hash_are_stable(tmp_path: Path) -> None:
    first = RunRecorder(tmp_path / "one", _manifest(created_at="2026-01-01T00:00:00Z"), run_id="run-1")
    second = RunRecorder(tmp_path / "two", _manifest(created_at="2026-01-01T00:00:00Z"), run_id="run-1")
    assert (first.path / "manifest.json").read_bytes() == (second.path / "manifest.json").read_bytes()
    assert canonical_hash({"b": 2, "a": 1}) == canonical_hash({"a": 1, "b": 2})


def test_recorder_terminal_run_has_core_files_and_optional_files_are_lazy(tmp_path: Path) -> None:
    recorder = RunRecorder(tmp_path, _manifest(), run_id="pilot-1")
    assert {item.name for item in recorder.path.iterdir()} == {
        "manifest.json", "environment.json", "events.jsonl",
    }
    recorder.finalize({"status": "dry_run"})
    assert all((recorder.path / name).exists() for name in (
        "manifest.json", "environment.json", "events.jsonl", "result.json", "report.md",
    ))
    assert not (recorder.path / "usage.json").exists()


def test_run_id_and_artifact_paths_are_restricted(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        RunRecorder(tmp_path, _manifest(), run_id="../escape")
    recorder = RunRecorder(tmp_path, _manifest(), run_id="safe")
    with pytest.raises(ValueError):
        recorder.write_artifact("../patch.diff", "bad")
    with pytest.raises(ValueError):
        recorder.write_artifact("unknown.txt", "bad")


def test_existing_run_is_never_overwritten(tmp_path: Path) -> None:
    RunRecorder(tmp_path, _manifest(), run_id="same")
    with pytest.raises(FileExistsError):
        RunRecorder(tmp_path, _manifest(), run_id="same")


def test_redaction_preserves_usage_tokens_and_removes_secret_values(tmp_path: Path) -> None:
    secret = "sk-example-abcdefghijklmnop"
    recorder = RunRecorder(tmp_path, _manifest(api_key_env="BAILIAN_API_KEY"), run_id="redact", secrets=[secret])
    recorder.event("usage", {"input_tokens": 12, "authorization": f"Bearer {secret}", "text": secret})
    content = (recorder.path / "events.jsonl").read_text(encoding="utf-8")
    assert '"input_tokens": 12' in content
    assert secret not in content
    assert "Bearer" not in content
    assert SecretRedactor().redact({"access_token": "value"})["access_token"] == "[REDACTED]"


def test_resume_requires_matching_identity_and_resumable_status(tmp_path: Path) -> None:
    recorder = RunRecorder(tmp_path, _manifest(), run_id="resume")
    recorder.event("trial_completed", {"task_id": "one", "repetition_id": "1"})
    recorder.finalize({"status": "provider_error"})
    resumed = RunRecorder.resume(tmp_path, "resume", _manifest())
    assert resumed.completed_trials() == {("one", "1")}
    with pytest.raises(ValueError, match="mismatch"):
        RunRecorder.resume(tmp_path, "resume", _manifest(model_id="other"))


def test_success_and_dry_run_are_not_resumable(tmp_path: Path) -> None:
    for status in ("success", "dry_run", "task_failure"):
        recorder = RunRecorder(tmp_path, _manifest(), run_id=status)
        recorder.finalize({"status": status})
        with pytest.raises(ValueError, match="not resumable"):
            RunRecorder.resume(tmp_path, status, _manifest())


def test_failed_run_and_optional_artifacts_remain(tmp_path: Path) -> None:
    recorder = RunRecorder(tmp_path, _manifest(), run_id="failed")
    recorder.optional_event("permission-events.jsonl", {"tool_use_id": "t1", "final_effect": "deny"})
    recorder.write_optional_json("usage.json", {"requests": []})
    recorder.write_artifact("stderr.txt", "password=hunter2")
    recorder.finalize({"status": "infrastructure_error", "password": "hunter2"})
    assert (recorder.path / "permission-events.jsonl").exists()
    assert (recorder.path / "usage.json").exists()
    assert "hunter2" not in (recorder.path / "report.md").read_text(encoding="utf-8")


def test_recorder_derives_optional_events_without_inventing_usage(tmp_path: Path) -> None:
    recorder = RunRecorder(tmp_path, RunManifest(provider="p", model_id="m"), run_id="events")
    raw_usage = {
        "prompt_tokens": 10,
        "completion_tokens": 3,
        "prompt_tokens_details": {"cached_tokens": 2},
    }
    recorder.capture_event({"type": "usage", "provider_usage": raw_usage, "request_index": 1})
    recorder.capture_event({"type": "permission_decision", "tool_id": "t1", "decision": "deny", "hitl_required": False, "executed": False})
    recorder.capture_event({"type": "compression", "success": False, "reason": "threshold", "tokens_before": 50, "tokens_after": None, "error_category": "provider_error"})
    usage = json.loads((recorder.path / "usage.json").read_text())
    assert usage["requests"][0]["provider_usage"] == raw_usage
    assert "reasoning_tokens" not in usage["requests"][0]["provider_usage"]
    with pytest.raises(ValueError, match="duplicate"):
        recorder.capture_event({"type": "permission_decision", "tool_id": "t1", "decision": "deny", "hitl_required": False, "executed": False})

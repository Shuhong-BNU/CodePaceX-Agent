from __future__ import annotations

import json
import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from evals import pilot
from evals.benchmark import RunRecorder


def config_file(tmp_path: Path, **changes: object) -> Path:
    text = Path("evals/pilot.qwen.yaml").read_text(encoding="utf-8")
    path = tmp_path / "pilot.yaml"
    path.write_text(text, encoding="utf-8")
    if changes:
        import yaml
        raw = yaml.safe_load(text)
        raw.update(changes)
        path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def test_frozen_config_validates_and_hash_changes(tmp_path: Path) -> None:
    config = pilot.load_config(config_file(tmp_path))
    assert config.provider == pilot.FROZEN_PROVIDER
    changed = config.model_copy(update={"feature_flags": {"deferred": True}})
    assert pilot.config_hash(config) != pilot.config_hash(changed)


def test_non_frozen_provider_and_fallback_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="frozen"):
        pilot.load_config(config_file(tmp_path, provider="agentrouter-opus48"))
    with pytest.raises(ValueError, match="fallback"):
        pilot.load_config(config_file(tmp_path, fallback_enabled=True))


def test_dry_run_creates_terminal_artifacts_without_client_or_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = pilot.load_config(config_file(tmp_path))
    monkeypatch.delenv("BAILIAN_API_KEY", raising=False)
    with patch("codepacex.client.create_client", side_effect=AssertionError("network client")):
        recorder = pilot.dry_run(config, Path.cwd(), tmp_path / "runs", "dry")
    result = json.loads((recorder.path / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "dry_run"
    manifest = json.loads((recorder.path / "manifest.json").read_text())
    assert manifest["system_prompt_hash"] is None
    assert manifest["tool_schema_hash"] is None
    assert all((recorder.path / name).exists() for name in ("manifest.json", "environment.json", "events.jsonl", "result.json", "report.md"))


def test_execute_without_key_or_confirmation_is_configuration_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = pilot.load_config(config_file(tmp_path, task_ids=["codepacex_001_config_bugfix"]))
    monkeypatch.delenv("BAILIAN_API_KEY", raising=False)
    with patch("evals.pilot._run_trials", side_effect=AssertionError("must not run")):
        recorder = pilot.execute(config, Path.cwd(), tmp_path / "runs", confirmed=False)
    assert json.loads((recorder.path / "result.json").read_text())["status"] == "configuration_error"


def test_cli_validate_does_not_show_key_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("BAILIAN_API_KEY", "do-not-print")
    assert pilot.main(["validate", "--config", str(config_file(tmp_path))]) == 0
    output = capsys.readouterr().out
    assert "do-not-print" not in output
    assert '"api_key_present": true' in output


def test_live_execute_is_mockable_and_child_env_excludes_other_provider_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = pilot.load_config(config_file(tmp_path, task_ids=["codepacex_001_config_bugfix"]))
    monkeypatch.setenv("BAILIAN_API_KEY", "test-only-bailian-key")
    monkeypatch.setenv("AGENTROUTER_API_KEY", "must-not-reach-child")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-reach-child-either")
    monkeypatch.setenv("GITHUB_TOKEN", "also-blocked")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/private/credential-agent")
    monkeypatch.setenv("HTTPS_PROXY", "https://proxy-user:proxy-password@example.test")
    captured: dict[str, object] = {}
    real_run = subprocess.run

    def fake_run(command, **kwargs):
        if "evals/run_eval.py" not in command:
            return real_run(command, **kwargs)
        captured["command"] = command
        captured["env"] = kwargs["env"]
        report_dir = Path(command[command.index("--report-dir") + 1]) / "mock-run"
        report_dir.mkdir(parents=True)
        (report_dir / "suite_result.json").write_text(json.dumps({
            "suite_status": "PASS",
            "tasks": [{"id": "codepacex_001_config_bugfix", "status": "PASS"}],
        }))
        return SimpleNamespace(returncode=0, stdout="mock stdout", stderr="")

    with patch("evals.pilot.subprocess.run", side_effect=fake_run):
        recorder = pilot.execute(config, Path.cwd(), tmp_path / "runs", confirmed=True)
    assert json.loads((recorder.path / "result.json").read_text())["status"] == "success"
    assert captured["command"][0] == pilot.sys.executable
    assert "AGENTROUTER_API_KEY" not in captured["env"]
    assert "AWS_SECRET_ACCESS_KEY" not in captured["env"]
    assert "GITHUB_TOKEN" not in captured["env"]
    assert "SSH_AUTH_SOCK" not in captured["env"]
    assert captured["env"]["HTTPS_PROXY"].startswith("https://proxy-user")
    all_output = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in recorder.path.rglob("*") if path.is_file()
    )
    assert "test-only-bailian-key" not in all_output
    assert "proxy-password" not in all_output
    result = json.loads((recorder.path / "result.json").read_text())
    assert result["attempted_trial_count"] == 1
    assert result["completed_trial_count"] == 1
    assert result["unscorable_trial_count"] == 0
    assert not (recorder.path / "artifacts" / "task-runs").exists()


def test_resume_requires_a_resumable_matching_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = pilot.load_config(config_file(tmp_path, task_ids=["codepacex_001_config_bugfix"]))
    monkeypatch.setenv("BAILIAN_API_KEY", "test-only-bailian-key")
    initial = pilot.dry_run(config, Path.cwd(), tmp_path / "runs", "old")
    with pytest.raises(ValueError, match="not resumable"):
        pilot.resume(config, Path.cwd(), tmp_path / "runs", initial.run_id, confirmed=True)


def test_generated_provider_config_omits_none_and_uses_real_loader(tmp_path: Path) -> None:
    config = pilot.load_config(config_file(tmp_path))
    path = tmp_path / "config.yaml"
    pilot._write_validated_provider_config(config, path)
    raw = __import__("yaml").safe_load(path.read_text())
    assert "max_output_tokens" not in raw["providers"][0]


def test_unknown_and_unsafe_task_ids_are_rejected(tmp_path: Path) -> None:
    for task_id in ("missing_task", "../codepacex_001_config_bugfix"):
        config = pilot.load_config(config_file(tmp_path, task_ids=[task_id]))
        with pytest.raises(ValueError, match="unknown or unsafe"):
            pilot.build_manifest(config, Path.cwd())


@pytest.mark.parametrize(
    ("task", "returncode", "expected"),
    [
        ({"status": "PASS"}, 0, "success"),
        ({"status": "FAIL", "failure_reason": "grader_failed"}, 1, "task_failure"),
        ({"status": "FAIL", "timed_out": True}, 1, "timeout"),
        ({"status": "ERROR", "error_type": "provider_network_error"}, 1, "provider_error"),
        ({"status": "ERROR", "error_type": "auth_error"}, 1, "configuration_error"),
        ({"status": "ERROR", "error_type": "agent_startup_error"}, 1, "infrastructure_error"),
    ],
)
def test_suite_result_status_mapping(
    task: dict[str, object], returncode: int, expected: str, tmp_path: Path,
) -> None:
    task_id = "codepacex_001_config_bugfix"
    run = tmp_path / "inner"
    run.mkdir(parents=True)
    (run / "suite_result.json").write_text(json.dumps({
        "tasks": [{"id": task_id, **task}],
    }))
    assert pilot._suite_status(tmp_path, task_id, returncode) == expected


def test_cli_execute_configuration_error_uses_exit_code_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BAILIAN_API_KEY", raising=False)
    config = config_file(tmp_path, task_ids=["codepacex_001_config_bugfix"])
    assert pilot.main([
        "execute", "--config", str(config), "--runs-dir", str(tmp_path / "runs"),
    ]) == 2


def _mock_trial_process(task_status: str = "PASS", duplicate_runtime: bool = False):
    real_run = subprocess.run

    def fake_run(command, **kwargs):
        if "evals/run_eval.py" not in command:
            return real_run(command, **kwargs)
        task_id = command[command.index("--task") + 1]
        report_dir = Path(command[command.index("--report-dir") + 1]) / "mock-run"
        trace = report_dir / task_id / "trace.ndjson"
        trace.parent.mkdir(parents=True)
        (report_dir / "suite_result.json").write_text(json.dumps({
            "tasks": [{"id": task_id, "status": task_status}],
        }))
        events = [
            {
                "type": "runtime_manifest", "request_index": 1,
                "provider": "bailian-qwen37-max", "protocol": "openai-compat",
                "model_id": "qwen3.7-max-2026-06-08", "system_sha256": "s",
                "tools_sha256": "t", "messages_sha256": "m",
            },
            {
                "type": "usage", "request_index": 1,
                "provider": "bailian-qwen37-max",
                "model_id": "qwen3.7-max-2026-06-08",
                "provider_usage": {"prompt_tokens": 1},
            },
            {
                "type": "permission_decision", "tool_use_id": "same-tool-id",
                "tool_name": "ReadFile", "final_effect": "allow",
                "mandatory_safety": False, "hook_effect": None,
                "hitl_required": False, "hitl_response": None,
                "persistable": False, "executed": True, "execution_path": "streaming",
            },
        ]
        if duplicate_runtime:
            events.insert(1, dict(events[0]))
        trace.write_text("\n".join(json.dumps(event) for event in events) + "\n")
        return SimpleNamespace(returncode=0, stdout="mock stdout", stderr="")

    return fake_run


def test_multiple_trials_keep_local_request_and_tool_ids_distinct(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = pilot.load_config(config_file(tmp_path, task_ids=[
        "codepacex_001_config_bugfix", "codepacex_002_config_validation",
    ], repetitions=2))
    monkeypatch.setenv("BAILIAN_API_KEY", "test-only-bailian-key")
    with patch("evals.pilot.subprocess.run", side_effect=_mock_trial_process()):
        recorder = pilot.execute(config, Path.cwd(), tmp_path / "runs", confirmed=True)
    result = json.loads((recorder.path / "result.json").read_text())
    assert result["status"] == "success"
    assert result["attempted_trial_count"] == 4
    runtime = [json.loads(line) for line in (recorder.path / "runtime-events.jsonl").read_text().splitlines()]
    assert {(event["task_id"], event["repetition_id"], event["attempt_id"], event["request_index"]) for event in runtime} == {
        ("codepacex_001_config_bugfix", "1", 1, 1),
        ("codepacex_002_config_validation", "1", 1, 1),
        ("codepacex_001_config_bugfix", "2", 1, 1),
        ("codepacex_002_config_validation", "2", 1, 1),
    }


def test_duplicate_runtime_within_one_trial_finalizes_as_infrastructure_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = pilot.load_config(config_file(tmp_path, task_ids=["codepacex_001_config_bugfix"]))
    monkeypatch.setenv("BAILIAN_API_KEY", "test-only-bailian-key")
    with patch("evals.pilot.subprocess.run", side_effect=_mock_trial_process(duplicate_runtime=True)):
        recorder = pilot.execute(config, Path.cwd(), tmp_path / "runs", confirmed=True)
    result = json.loads((recorder.path / "result.json").read_text())
    assert result["status"] == "infrastructure_error"
    assert result["scorable"] is False
    assert result["completed_trial_count"] == 1


def test_resume_retries_failed_attempt_with_incremented_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = pilot.load_config(config_file(tmp_path, task_ids=["codepacex_001_config_bugfix"]))
    monkeypatch.setenv("BAILIAN_API_KEY", "test-only-bailian-key")
    initial = RunRecorder(tmp_path / "runs", pilot.build_manifest(config, Path.cwd()), run_id="retry")
    initial.event("trial_started", {"task_id": config.task_ids[0], "repetition_id": "1", "attempt_id": 1})
    initial.event("trial_completed", {
        "task_id": config.task_ids[0], "repetition_id": "1", "attempt_id": 1,
        "status": "provider_error",
    })
    initial.finalize({"status": "provider_error"})
    with patch("evals.pilot.subprocess.run", side_effect=_mock_trial_process()):
        recorder = pilot.resume(config, Path.cwd(), tmp_path / "runs", "retry", confirmed=True)
    completed = [
        json.loads(line) for line in (recorder.path / "events.jsonl").read_text().splitlines()
        if json.loads(line).get("type") == "trial_completed"
    ]
    assert [(event["attempt_id"], event["status"]) for event in completed] == [
        (1, "provider_error"), (2, "success"),
    ]
    assert json.loads((recorder.path / "result.json").read_text())["status"] == "success"


def test_resume_without_new_trials_preserves_previous_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = pilot.load_config(config_file(tmp_path, task_ids=["codepacex_001_config_bugfix"]))
    monkeypatch.setenv("BAILIAN_API_KEY", "test-only-bailian-key")
    initial = RunRecorder(tmp_path / "runs", pilot.build_manifest(config, Path.cwd()), run_id="previous-failure")
    initial.finalize({"status": "timeout"})
    with patch("evals.pilot._run_trials", return_value=[]):
        recorder = pilot.resume(config, Path.cwd(), tmp_path / "runs", "previous-failure", confirmed=True)
    result = json.loads((recorder.path / "result.json").read_text())
    assert result["status"] == "timeout"
    assert result["scorable"] is False


def test_cancelled_execution_finalizes_before_reraising(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = pilot.load_config(config_file(tmp_path, task_ids=["codepacex_001_config_bugfix"]))
    monkeypatch.setenv("BAILIAN_API_KEY", "test-only-bailian-key")
    captured: list[dict] = []
    original_finalize = RunRecorder.finalize

    def capture_finalize(self, result):
        captured.append(dict(result))
        return original_finalize(self, result)

    with (
        patch("evals.pilot._run_trials", side_effect=asyncio.CancelledError),
        patch.object(RunRecorder, "finalize", capture_finalize),
        pytest.raises(asyncio.CancelledError),
    ):
        pilot.execute(config, Path.cwd(), tmp_path / "runs", confirmed=True)
    assert captured[-1]["status"] == "cancelled"

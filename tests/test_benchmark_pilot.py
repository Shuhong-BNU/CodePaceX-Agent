from __future__ import annotations

import json
import hashlib
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
    assert json.loads((first.path / "manifest.json").read_text())["schema_version"] == 2


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


def test_task_artifacts_map_dynamic_ids_to_safe_auditable_names(tmp_path: Path) -> None:
    recorder = RunRecorder(tmp_path, _manifest(), run_id="task-artifacts", secrets=["secret-value"])
    task_id = "aiogram__aiogram-1594"
    artifact = recorder.write_task_artifact(task_id, "stdout", "token=secret-value")

    expected_name = hashlib.sha256(task_id.encode("utf-8")).hexdigest() + "-stdout.txt"
    assert artifact.name == expected_name
    assert artifact.parent == recorder.path / "artifacts"
    assert "secret-value" not in artifact.read_text(encoding="utf-8")
    mapping = json.loads((recorder.path / "task-artifacts.json").read_text(encoding="utf-8"))
    assert mapping["schema_version"] == 2
    assert mapping["artifacts"] == [{"task_id": task_id, "kind": "stdout", "name": expected_name}]
    assert recorder.write_task_artifact(task_id, "stdout", "next") == artifact
    assert json.loads((recorder.path / "task-artifacts.json").read_text(encoding="utf-8"))["artifacts"] == [
        {"task_id": task_id, "kind": "stdout", "name": expected_name},
    ]
    report = recorder.write_task_artifact(task_id, "evaluator_report", '{"resolved": false}')
    expected_report_name = hashlib.sha256(task_id.encode("utf-8")).hexdigest() + "-evaluator_report.txt"
    assert report.name == expected_report_name
    assert json.loads((recorder.path / "task-artifacts.json").read_text(encoding="utf-8"))["artifacts"][-1] == {
        "task_id": task_id, "kind": "evaluator_report", "name": expected_report_name,
    }


def test_task_artifacts_keep_the_regular_artifact_allowlist_strict(tmp_path: Path) -> None:
    recorder = RunRecorder(tmp_path, _manifest(), run_id="task-artifact-rejection")
    with pytest.raises(ValueError, match="non-empty task ID"):
        recorder.write_task_artifact("", "stdout", "bad")
    with pytest.raises(ValueError, match="unsupported task artifact kind"):
        recorder.write_task_artifact("task", "patch", "bad")
    with pytest.raises(ValueError, match="unsupported artifact name"):
        recorder.write_artifact("task.stdout.txt", "bad")


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


def test_redactor_covers_encoded_json_shell_and_proxy_credentials(tmp_path: Path) -> None:
    key = 'pilot key/+/"quoted"'
    proxy = "https://proxy-user:proxy pass/@proxy.example:8443"
    redactor = SecretRedactor([key, proxy])
    forms = [
        key,
        "pilot%20key%2F%2B%2F%22quoted%22",
        "pilot+key%2F%2B%2F%22quoted%22",
        'pilot key/+/\\"quoted\\"',
        "'pilot key/+/\"quoted\"'",
        "Bearer " + key,
        proxy,
        "proxy+pass%2F",
    ]
    recorder = RunRecorder(tmp_path, _manifest(), run_id="encoded-secrets", secrets=[key, proxy])
    for index, value in enumerate(forms):
        recorder.event("trace", {"index": index, "value": value})
    recorder.write_artifact("stdout.txt", "\n".join(forms))
    recorder.finalize({"status": "success"})
    all_output = "\n".join(
        item.read_text(encoding="utf-8") for item in recorder.path.rglob("*") if item.is_file()
    )
    assert all(form not in all_output for form in forms)
    assert json.loads((recorder.path / "result.json").read_text())["status"] == "success"


def test_unredactable_final_scan_makes_run_infrastructure_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = RunRecorder(tmp_path, _manifest(), run_id="scan-failure", secrets=["secret"])
    monkeypatch.setattr(recorder.redactor, "contains_secret", lambda _: True)
    recorder.finalize({"status": "success"})
    result = json.loads((recorder.path / "result.json").read_text())
    assert result["status"] == "infrastructure_error"
    assert result["scorable"] is False
    assert result["error_category_summary"]["secret_redaction_failure"] >= 1


def test_resume_requires_matching_identity_and_resumable_status(tmp_path: Path) -> None:
    recorder = RunRecorder(tmp_path, _manifest(), run_id="resume")
    recorder.event("trial_completed", {"task_id": "one", "repetition_id": "1"})
    recorder.finalize({"status": "provider_error"})
    resumed = RunRecorder.resume(tmp_path, "resume", _manifest())
    assert resumed.completed_trials() == {("one", "1")}
    with pytest.raises(ValueError, match="mismatch"):
        RunRecorder.resume(tmp_path, "resume", _manifest(model_id="other"))
    with pytest.raises(ValueError, match="experiment_profile_hash"):
        RunRecorder.resume(
            tmp_path, "resume", _manifest(experiment_profile_hash="different-profile"),
        )


@pytest.mark.parametrize(
    ("stored_hash", "expected_hash", "permitted"),
    [
        ("pricing-a", "pricing-a", True),
        ("pricing-a", "pricing-b", False),
        ("pricing-a", None, False),
        (None, "pricing-a", False),
        (None, None, True),
    ],
)
def test_resume_requires_matching_pricing_snapshot_identity(
    tmp_path: Path, stored_hash: str | None, expected_hash: str | None, permitted: bool,
) -> None:
    recorder = RunRecorder(
        tmp_path, _manifest(pricing_snapshot_hash=stored_hash), run_id="pricing-resume",
    )
    recorder.finalize({"status": "provider_error"})
    events_before = (recorder.path / "events.jsonl").read_bytes()

    if permitted:
        resumed = RunRecorder.resume(
            tmp_path, "pricing-resume", _manifest(pricing_snapshot_hash=expected_hash),
        )
        assert resumed.previous_status == "provider_error"
    else:
        with pytest.raises(ValueError, match="pricing snapshot identity mismatch"):
            RunRecorder.resume(
                tmp_path, "pricing-resume", _manifest(pricing_snapshot_hash=expected_hash),
            )
        assert (recorder.path / "events.jsonl").read_bytes() == events_before


def test_resume_requires_matching_swe_evaluator_architecture_identity(tmp_path: Path) -> None:
    recorder = RunRecorder(
        tmp_path, _manifest(swe_evaluator_architecture="native"), run_id="swe-architecture",
    )
    recorder.finalize({"status": "provider_error"})
    with pytest.raises(ValueError, match="swe_evaluator_architecture"):
        RunRecorder.resume(
            tmp_path, "swe-architecture", _manifest(swe_evaluator_architecture="x86_64"),
        )


def test_success_and_dry_run_are_not_resumable(tmp_path: Path) -> None:
    for status in ("success", "dry_run"):
        recorder = RunRecorder(tmp_path, _manifest(), run_id=status)
        recorder.finalize({"status": status})
        with pytest.raises(ValueError, match="not resumable"):
            RunRecorder.resume(tmp_path, status, _manifest())


def test_failed_terminal_statuses_are_resumable(tmp_path: Path) -> None:
    for status in ("task_failure", "provider_error", "configuration_error", "timeout", "infrastructure_error", "cancelled"):
        recorder = RunRecorder(tmp_path, _manifest(), run_id=status)
        recorder.finalize({"status": status})
        assert RunRecorder.resume(tmp_path, status, _manifest()).previous_status == status


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
    recorder.capture_event({"type": "permission_decision", "tool_use_id": "t1", "final_effect": "deny", "hitl_required": False, "executed": False})
    recorder.capture_event({"type": "compression", "success": False, "reason": "threshold", "tokens_before": 50, "tokens_after": None, "error_category": "provider_error"})
    usage = json.loads((recorder.path / "usage.json").read_text())
    assert usage["requests"][0]["provider_usage"] == raw_usage
    assert "reasoning_tokens" not in usage["requests"][0]["provider_usage"]
    with pytest.raises(ValueError, match="duplicate"):
        recorder.capture_event({"type": "permission_decision", "tool_use_id": "t1", "final_effect": "deny", "hitl_required": False, "executed": False})


def test_usage_json_preserves_multiple_provider_requests_in_order(tmp_path: Path) -> None:
    recorder = RunRecorder(tmp_path, _manifest(), run_id="usage-order")
    for index in range(1, 4):
        recorder.capture_event({
            "type": "usage", "request_index": index,
            "provider_usage": {"prompt_tokens": index, "details": {"raw": index}},
        })
    requests = json.loads((recorder.path / "usage.json").read_text())["requests"]
    assert [item["request_index"] for item in requests] == [1, 2, 3]
    assert requests[2]["provider_usage"] == {"prompt_tokens": 3, "details": {"raw": 3}}


@pytest.mark.parametrize(
    ("status", "scorable"),
    [
        ("success", True), ("task_failure", True), ("timeout", False),
        ("provider_error", False), ("configuration_error", False),
        ("infrastructure_error", False), ("cancelled", False), ("dry_run", False),
    ],
)
def test_finalize_enforces_scorable_status(status: str, scorable: bool, tmp_path: Path) -> None:
    recorder = RunRecorder(tmp_path, _manifest(), run_id=status)
    recorder.finalize({"status": status, "scorable": not scorable})
    result = json.loads((recorder.path / "result.json").read_text())
    assert result["scorable"] is scorable
    assert result["attempted_trial_count"] == 0
    assert result["completed_trial_count"] == 0
    assert result["unscorable_trial_count"] == 0


def test_finalize_counts_attempts_terminals_and_error_categories(tmp_path: Path) -> None:
    recorder = RunRecorder(tmp_path, _manifest(), run_id="counts")
    for task_id, status in (("one", "success"), ("two", "timeout")):
        recorder.event("trial_started", {"task_id": task_id, "repetition_id": "1"})
        recorder.event("trial_completed", {
            "task_id": task_id, "repetition_id": "1", "status": status,
        })
    recorder.finalize({"status": "timeout"})
    result = json.loads((recorder.path / "result.json").read_text())
    assert result["attempted_trial_count"] == 2
    assert result["completed_trial_count"] == 2
    assert result["unscorable_trial_count"] == 1
    assert result["error_category_summary"] == {"timeout": 1}


def test_finalize_counts_multiple_attempts_without_overwriting_history(tmp_path: Path) -> None:
    recorder = RunRecorder(tmp_path, _manifest(), run_id="retry-counts")
    for attempt_id, status in ((1, "provider_error"), (2, "success")):
        recorder.event("trial_started", {
            "task_id": "one", "repetition_id": "1", "attempt_id": attempt_id,
        })
        recorder.event("trial_completed", {
            "task_id": "one", "repetition_id": "1", "attempt_id": attempt_id,
            "status": status,
        })
    recorder.finalize({"status": "success"})
    result = json.loads((recorder.path / "result.json").read_text())
    assert result["attempted_trial_count"] == 2
    assert result["completed_trial_count"] == 2
    assert result["unscorable_trial_count"] == 1
    assert result["error_category_summary"] == {"provider_error": 1}


def test_runtime_events_are_separate_and_request_indexes_are_unique(tmp_path: Path) -> None:
    recorder = RunRecorder(tmp_path, _manifest(), run_id="runtime")
    event = {
        "type": "runtime_manifest", "request_index": 1, "provider": "p",
        "protocol": "openai-compat", "model_id": "m", "system_sha256": "s",
        "tools_sha256": "t", "messages_sha256": "msg",
    }
    recorder.capture_event(event)
    records = (recorder.path / "runtime-events.jsonl").read_text().splitlines()
    assert len(records) == 1
    assert json.loads(records[0])["messages_sha256"] == "msg"
    with pytest.raises(ValueError, match="duplicate runtime"):
        recorder.capture_event(event)


def test_v2_runtime_profile_must_match_actual_request_hashes(tmp_path: Path) -> None:
    profile_hash = canonical_hash({"profile": "deferred"})
    contract_hash = canonical_hash({"effective": "deferred"})
    recorder = RunRecorder(tmp_path, _manifest(
        experiment_profile={"tool_loading": "deferred"},
        experiment_profile_hash=profile_hash,
        runtime_contract_hash=contract_hash,
    ), run_id="profile-runtime")
    event = {
        "type": "runtime_manifest", "request_index": 1,
        "provider": "p", "protocol": "openai-compat", "model_id": "m",
        "system_sha256": "system", "tools_sha256": "tools",
        "messages_sha256": "messages",
        "experiment_profile_hash": profile_hash,
        "runtime_contract_hash": contract_hash,
        "combined_runtime_hash": canonical_hash({
            "experiment_profile_hash": profile_hash,
            "system_sha256": "system", "tools_sha256": "tools",
        }),
    }
    recorder.capture_event(event)

    second = RunRecorder(tmp_path, _manifest(
        experiment_profile={"tool_loading": "deferred"},
        experiment_profile_hash=profile_hash,
        runtime_contract_hash=contract_hash,
    ), run_id="profile-mismatch")
    with pytest.raises(ValueError, match="profile hash"):
        second.capture_event({**event, "experiment_profile_hash": "wrong"})


def test_runtime_and_permission_identity_is_scoped_to_trial_attempt(tmp_path: Path) -> None:
    recorder = RunRecorder(tmp_path, _manifest(), run_id="attempt-scope")

    def runtime(task_id: str, repetition_id: str, attempt_id: int) -> dict:
        return {
            "type": "runtime_manifest", "request_index": 1,
            "provider": "p", "protocol": "openai-compat", "model_id": "m",
            "system_sha256": "s", "tools_sha256": "t", "messages_sha256": "msg",
            "task_id": task_id, "repetition_id": repetition_id, "attempt_id": attempt_id,
        }

    recorder.capture_event(runtime("task-one", "1", 1))
    recorder.capture_event(runtime("task-two", "1", 1))
    recorder.capture_event(runtime("task-one", "1", 2))
    with pytest.raises(ValueError, match="duplicate runtime"):
        recorder.capture_event(runtime("task-one", "1", 1))

    permission = {
        "type": "permission_decision", "tool_use_id": "tool-1",
        "task_id": "task-one", "repetition_id": "1", "attempt_id": 1,
    }
    recorder.capture_event(permission)
    recorder.capture_event({**permission, "task_id": "task-two"})
    with pytest.raises(ValueError, match="duplicate permission"):
        recorder.capture_event(permission)


def test_budget_blocked_is_terminal_unscorable_and_not_resumable(tmp_path: Path) -> None:
    recorder = RunRecorder(tmp_path, _manifest(), run_id="budget-blocked")
    recorder.event("trial_started", {
        "task_id": "one", "repetition_id": "1", "attempt_id": 1,
    })
    recorder.event("trial_completed", {
        "task_id": "one", "repetition_id": "1", "attempt_id": 1,
        "status": "budget_blocked", "budget_block_reasons": ["stage_limit"],
        "actual_cny": "0.000000",
    })
    recorder.finalize({"status": "budget_blocked", "execution_mode": "live"})

    result = json.loads((recorder.path / "result.json").read_text())
    assert result["status"] == "budget_blocked"
    assert result["scorable"] is False
    assert result["unscorable_trial_count"] == 1
    assert result["error_category_summary"] == {"budget_blocked": 1}
    assert recorder.terminal_trial_statuses() == {("one", "1"): "budget_blocked"}
    with pytest.raises(ValueError, match="not resumable: budget_blocked"):
        RunRecorder.resume(tmp_path, "budget-blocked", _manifest())

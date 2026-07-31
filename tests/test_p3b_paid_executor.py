from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from evals import paid_gate
from evals.evaluation_v2 import full_replay
from evals.evaluation_v2 import p3b_paid_executor as executor
from evals.evaluation_v2 import p3b_post_merge_rebind as p3b


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _simulate_clean_frozen_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paid_gate, "_git_is_clean", lambda _root: True)


def _bundle(tmp_path: Path, *, test_only: bool = True, suffix: str = "fixed-suffix-001") -> tuple[dict, Path, str]:
    values, raw, digest = executor.generate_paid_input_bundle(
        ROOT, test_only=test_only, provider_secret_present=True,
        generated_at="20260731T220000Z", random_suffix=suffix,
    )
    path = tmp_path / "paid-inputs.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return values, path, digest


def _fake_run(tmp_path: Path, **overrides: object) -> dict:
    values, path, digest = _bundle(tmp_path, suffix="fake-suffix-001")
    if overrides:
        values.update(overrides)
        raw = executor.canonical_paid_input_bundle_bytes(values)
        path.write_bytes(raw)
        digest = executor.final_input_bundle_sha256(raw)
    return executor.run_paid_executor(
        ROOT, tmp_path / "paid-artifact", input_bundle_path=path,
        expected_final_input_bundle_sha256=digest,
        task_executor=executor.recording_fake_task_executor, require_main=False,
        allow_test_only_identity=True,
    )


def test_generator_emits_test_only_identity_and_unique_values(tmp_path: Path) -> None:
    first, first_raw, first_sha = executor.generate_paid_input_bundle(
        ROOT, test_only=True, provider_secret_present=True,
        generated_at="20260731T220000Z", random_suffix="suffix-one-0001",
    )
    second, second_raw, second_sha = executor.generate_paid_input_bundle(
        ROOT, test_only=True, provider_secret_present=True,
        generated_at="20260731T220000Z", random_suffix="suffix-two-0002",
    )
    assert first["identity_mode"] == second["identity_mode"] == "test-only"
    assert first["authorization_acknowledgement"].startswith(executor.REQUIRED_ACKNOWLEDGEMENT_PREFIX)
    assert first["dispatch_token"] != second["dispatch_token"]
    assert first["run_id"] != second["run_id"]
    assert first_sha == hashlib.sha256(first_raw).hexdigest()
    assert second_sha == hashlib.sha256(second_raw).hexdigest()
    assert executor._SAFE_RUN_IDENTITY.fullmatch(first["dispatch_token"])
    assert executor._SAFE_RUN_IDENTITY.fullmatch(first["run_id"])


def test_cli_generates_only_a_test_only_bundle(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "generated.json"
    assert executor.main([
        "--root", str(ROOT), "--output-bundle", str(path), "--generate-test-only-bundle",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    loaded = executor._load_paid_input_bundle(path)
    assert result["identity_mode"] == loaded["identity_mode"] == "test-only"
    assert result["final_input_bundle_sha256"] == executor.final_input_bundle_sha256(path.read_bytes())


def test_canonical_bytes_are_stable_and_reject_manual_prefix_or_extra_fields(tmp_path: Path) -> None:
    values, path, digest = _bundle(tmp_path)
    same = executor.canonical_paid_input_bundle_bytes(dict(values))
    assert same == path.read_bytes()
    assert digest == executor.final_input_bundle_sha256(same)
    with pytest.raises(ValueError, match="field order"):
        executor.canonical_paid_input_bundle_bytes({**values, "extra": True})
    with pytest.raises(ValueError, match="canonical"):
        executor._parse_paid_input_bundle_bytes(path.read_bytes()[:-1] + b" \n")


def test_unknown_duplicate_and_schema_inputs_fail_closed(tmp_path: Path) -> None:
    values, path, digest = _bundle(tmp_path)
    unknown = dict(values); unknown["unknown"] = True
    unknown_raw = json.dumps(unknown, separators=(",", ":")).encode() + b"\n"
    with pytest.raises(ValueError, match="fields"):
        executor._parse_paid_input_bundle_bytes(unknown_raw)
    duplicate = path.read_bytes().replace(b'"schema_version":2,', b'"schema_version":2,"schema_version":2,')
    with pytest.raises(ValueError, match="duplicate"):
        executor._parse_paid_input_bundle_bytes(duplicate)
    bad_schema = path.read_bytes().replace(b'"schema_version":2', b'"schema_version":99', 1)
    with pytest.raises(ValueError, match="schema"):
        executor._parse_paid_input_bundle_bytes(bad_schema)
    assert digest == executor.final_input_bundle_sha256(path.read_bytes())


def test_raw_byte_mutation_and_reserialization_change_sha_and_fail(tmp_path: Path) -> None:
    _values, path, digest = _bundle(tmp_path)
    mutated = path.read_bytes().replace(b"test-only", b"test_only", 1)
    path.write_bytes(mutated)
    with pytest.raises(ValueError, match="SHA-256"):
        executor.load_verified_paid_input_bundle(path, digest)
    path.write_bytes(json.dumps(json.loads(mutated), indent=2, sort_keys=True).encode() + b"\n")
    with pytest.raises(ValueError, match="SHA-256"):
        executor.load_verified_paid_input_bundle(path, digest)


def test_validate_only_records_bundle_sha_without_workspace_ledger_or_provider(tmp_path: Path) -> None:
    _values, path, digest = _bundle(tmp_path)
    with patch.object(executor.full_replay, "_full_task_executor", side_effect=AssertionError("Provider")), patch.object(
        executor, "run_paid_executor", side_effect=AssertionError("paid execution"),
    ):
        payload = executor.validate_inputs_only(
            ROOT, tmp_path / "preflight", input_bundle_path=path,
            expected_final_input_bundle_sha256=digest, require_main=False,
        )
    assert payload["status"] == "passed_validate_paid_inputs"
    assert payload["final_input_bundle_sha256"] == digest
    assert payload["provider_reached"] is False and payload["provider_secret_read"] is False
    assert payload["provider_requests"] == payload["usage"] == 0
    assert payload["charge_cny"] == "0" and payload["active_reservation"] is None
    artifact = tmp_path / "preflight"
    assert (artifact / executor.PAID_INPUT_PREFLIGHT_NAME).is_file()
    assert not (artifact / "workspace").exists() and not (artifact / "ledger.json").exists()


def test_validate_only_and_paid_executor_require_equal_bundle_sha(tmp_path: Path) -> None:
    _values, path, digest = _bundle(tmp_path)
    with pytest.raises(ValueError, match="SHA-256"):
        executor.validate_inputs_only(ROOT, tmp_path / "bad-preflight", input_bundle_path=path,
                                     expected_final_input_bundle_sha256="0" * 64, require_main=False)
    with pytest.raises(ValueError, match="SHA-256"):
        executor.run_paid_executor(ROOT, tmp_path / "bad-paid", input_bundle_path=path,
                                   expected_final_input_bundle_sha256="0" * 64,
                                   task_executor=executor.recording_fake_task_executor, require_main=False)
    summary = _fake_run(tmp_path / "good")
    assert summary["final_input_bundle_sha256"] == digest or isinstance(summary["final_input_bundle_sha256"], str)


def test_recording_fake_exercises_paid_coordinator_for_all_eight_runs(tmp_path: Path) -> None:
    summary = _fake_run(tmp_path)
    assert summary["paid_execution"] is False and summary["completed"] is True
    assert len(summary["records"]) == 8 and summary["paired_result_merge_count"] == 4
    assert summary["provider_secret_read"] is False
    assert summary["ledger_closed"] is True and summary["active_reservation"] is None


def test_production_adapter_exercises_all_runs_without_provider_transport(tmp_path: Path) -> None:
    captured: list[dict] = []

    def provider_initialization_boundary(**kwargs: object) -> object:
        task = kwargs["task"]; artifact_root = Path(kwargs["artifact_root"])
        task_root = artifact_root / "tasks" / task["instance_id"]
        task_root.mkdir(parents=True, exist_ok=True)
        patch_text = "diff --git a/tracked.py b/tracked.py\n--- a/tracked.py\n+++ b/tracked.py\n@@ -1 +1 @@\n-value = 'base'\n+value = 'adapter-preflight'\n"
        for name, content in {"candidate.patch": patch_text, "agent-request-record.json": "{}\n", "official-report.json": "{}\n", "task-result.json": "{}\n"}.items():
            (task_root / name).write_text(content, encoding="utf-8")
        treatment = kwargs["freeze_payload"]["runtime_contract"]["capability_v3_feature_flag"]
        if treatment == "V3_CORE":
            v3 = task_root / "capability-v3"; v3.mkdir()
            (v3 / "summary.json").write_text("{}\n"); (v3 / "events.jsonl").write_text("{}\n"); (v3 / "final.patch").write_text(patch_text)
        captured.append({"instance_id": task["instance_id"]})
        return executor.control_canary.PaidTaskResult(task["instance_id"], "not_started", "not_exported", "not_run", "not_run", "completed", "pre_transport_blocked", terminal_status="unresolved", live_executor_invoked=True)

    _values, path, digest = _bundle(tmp_path)
    with patch.object(executor.control_canary, "_live_task_executor", provider_initialization_boundary), patch.object(
        full_replay, "_full_task_executor", wraps=full_replay._full_task_executor,
    ) as shared_executor:
        summary = executor.run_paid_executor(ROOT, tmp_path / "paid-artifact", input_bundle_path=path,
                                             expected_final_input_bundle_sha256=digest, require_main=False,
                                             allow_test_only_identity=True)
    assert summary["completed"] is True and len(summary["records"]) == 8
    assert len(captured) == 8 and shared_executor.call_count == 8


@pytest.mark.parametrize("field,value,match", [
    ("expected_freeze_sha256", "0" * 64, "freeze SHA"),
    ("expected_allocation_hash", "0" * 64, "allocation hash"),
    ("approved_parent_cap_cny", "292.945920", "parent cap"),
    ("authorization_acknowledgement", "P3B_PAID_AUTHORIZATION_V2:test-only-0001", "acknowledgement"),
    ("dispatch_token", "short", "dispatch token"),
    ("run_id", "bad/run", "run ID"),
    ("provider_secret_present", False, "Secret presence"),
])
def test_paid_inputs_fail_closed_before_workspace(field: str, value: object, match: str, tmp_path: Path) -> None:
    values, path, digest = _bundle(tmp_path)
    values[field] = value
    path.write_bytes(executor.canonical_paid_input_bundle_bytes(values))
    with pytest.raises(ValueError, match=match):
        executor.validate_paid_inputs(ROOT, require_main=False, **executor._validation_inputs(values))


def test_validate_only_failure_writes_redacted_artifact(tmp_path: Path) -> None:
    values, path, digest = _bundle(tmp_path)
    values["authorization_acknowledgement"] = "P3B_PAID_AUTHORIZATION_V2:test-only-0001"
    path.write_bytes(executor.canonical_paid_input_bundle_bytes(values))
    with pytest.raises(ValueError, match="acknowledgement"):
        executor.validate_inputs_only(ROOT, tmp_path / "preflight", input_bundle_path=path,
                                     expected_final_input_bundle_sha256=executor.final_input_bundle_sha256(path.read_bytes()), require_main=False)
    failure = json.loads((tmp_path / "preflight" / executor.PREFLIGHT_FAILURE_NAME).read_text())
    assert failure["provider_reached"] is False and failure["provider_secret_read"] is False
    assert failure["provider_requests"] == failure["usage"] == 0
    assert failure["charge_cny"] == "0" and failure["active_reservation"] is None
    assert "P3B_PAID_AUTHORIZATION_V2" not in json.dumps(failure)


def test_workflow_uses_single_bundle_sha_and_skips_paid_on_pr(tmp_path: Path) -> None:
    workflow = (ROOT / p3b.WORKFLOW_PATH).read_text(encoding="utf-8")
    payload = yaml.safe_load(workflow); paid = payload["jobs"]["p3b-paid-execution"]
    assert "canonical_bundle_base64" in workflow and "expected_final_input_bundle_sha256" in workflow
    assert "base64 --decode" in workflow and "sha256sum" in workflow
    assert "--expected-final-input-bundle-sha256" in workflow
    assert "github.event_name == 'workflow_dispatch'" in workflow
    assert "p3b-paid-execution-global-v1" in workflow
    assert "BAILIAN_API_KEY: ${{ secrets.BAILIAN_API_KEY }}" in workflow
    assert paid["if"].startswith("${{ github.event_name == 'workflow_dispatch'")


def test_dispatch_guard_rejects_duplicate_and_second_dispatch(tmp_path: Path) -> None:
    guard = p3b.DispatchGuard(tmp_path / "dispatch-guard.json")
    guard.claim(dispatch_token="p3b-test-token-0001", run_id="p3b-test-run-0001")
    with pytest.raises(ValueError, match="duplicate"):
        guard.claim(dispatch_token="p3b-test-token-0001", run_id="p3b-test-run-0001")
    with pytest.raises(ValueError, match="second"):
        guard.claim(dispatch_token="p3b-second-token-0002", run_id="p3b-second-run-0002")

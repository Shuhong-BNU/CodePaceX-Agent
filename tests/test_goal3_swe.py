from __future__ import annotations

import json
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import evals.goal3_swe as goal3_swe
from evals.costing import pricing_snapshot_hash
from evals.swe_bench_live import patch_file_count, size_bucket


def _result(*, returncode: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _native_preflight_mocks(monkeypatch: pytest.MonkeyPatch, *, system: str = "Linux", machine: str = "x86_64", installed_commit: str | None = None) -> None:
    monkeypatch.setattr(goal3_swe.platform, "system", lambda: system)
    monkeypatch.setattr(goal3_swe.platform, "machine", lambda: machine)
    monkeypatch.setattr(goal3_swe.os, "uname", lambda: SimpleNamespace(sysname=system, machine=machine))
    monkeypatch.setattr(goal3_swe.shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    monkeypatch.setattr(goal3_swe, "_cpuinfo", lambda: "vendor_id: GenuineIntel")
    monkeypatch.setattr(goal3_swe.importlib.util, "find_spec", lambda name: SimpleNamespace(origin="/tmp/swebench/__init__.py"))
    monkeypatch.setattr(goal3_swe, "_installed_evaluator_commit", lambda origin: installed_commit or goal3_swe.ENVIRONMENT["commit"])

    def fake_run(command: list[str], *, cwd: Path | None = None) -> SimpleNamespace:
        if command[0] == "docker":
            return _result(stdout="29.0.0\n")
        if command[-2:] == ["status", "--porcelain"]:
            return _result()
        if command[-2:] == ["rev-parse", "HEAD"]:
            return _result(stdout="a" * 40 + "\n")
        raise AssertionError(command)

    monkeypatch.setattr(goal3_swe, "_run", fake_run)


def test_preflight_rejects_non_linux(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _native_preflight_mocks(monkeypatch, system="Darwin")
    payload = goal3_swe.native_preflight(root=tmp_path)
    assert payload["valid"] is False
    assert payload["native_linux_x86_64"] is False


def test_preflight_rejects_arm64(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _native_preflight_mocks(monkeypatch, machine="arm64")
    assert goal3_swe.native_preflight(root=tmp_path)["valid"] is False


def test_preflight_rejects_a_spoofed_platform_machine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _native_preflight_mocks(monkeypatch)
    monkeypatch.setattr(goal3_swe.os, "uname", lambda: SimpleNamespace(sysname="Linux", machine="arm64"))
    payload = goal3_swe.native_preflight(root=tmp_path)
    assert payload["architecture_matches_kernel"] is False
    assert payload["valid"] is False


def test_preflight_accepts_native_linux_x86_64(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _native_preflight_mocks(monkeypatch)
    payload = goal3_swe.native_preflight(root=tmp_path)
    assert payload["valid"] is True
    assert payload["native_linux_x86_64"] is True


def test_preflight_rejects_wrong_evaluator_commit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _native_preflight_mocks(monkeypatch, installed_commit="wrong")
    payload = goal3_swe.native_preflight(root=tmp_path)
    assert payload["evaluator_commit_matches"] is False
    assert payload["valid"] is False


def test_official_empty_patch_report_is_an_explicit_unresolved_outcome(tmp_path: Path) -> None:
    (tmp_path / "official-report.json").write_text(
        json.dumps({"empty_patch_ids": ["case"]}), encoding="utf-8",
    )
    assert goal3_swe.collect_official_outcomes(tmp_path, {"case"}) == {"case": False}


def test_control_instance_writer_preserves_the_official_patch_and_serializes_dates(tmp_path: Path) -> None:
    path = tmp_path / "instance.jsonl"
    goal3_swe.write_control_instance(
        output=path,
        instance={
            "instance_id": "case", "patch": "diff --git a/a b/a\n",
            "created_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
        },
        instance_id="case",
    )
    payload = json.loads(path.read_text())
    assert payload["instance_id"] == "case"
    assert payload["patch"] == "diff --git a/a b/a\n"
    assert payload["created_at"] == "2026-01-02 00:00:00+00:00"


@pytest.mark.parametrize(("control", "resolved"), [("empty", False), ("gold", True)])
def test_controls_never_call_a_model_and_record_expected_outcome(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, control: str, resolved: bool,
) -> None:
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps({"instance_id": "case", "patch": "diff --git a/a b/a\n"}) + "\n")
    monkeypatch.setattr(goal3_swe, "require_native_preflight", lambda **kwargs: {
        "installed_evaluator_commit": goal3_swe.ENVIRONMENT["commit"], "host_system": "Linux",
        "host_architecture": "x86_64", "docker_server_version": "29.0.0",
    })
    monkeypatch.setattr(goal3_swe, "run_official_evaluator", lambda **kwargs: _result())
    monkeypatch.setattr(goal3_swe, "collect_official_outcomes", lambda root, ids: {"case": resolved})
    recorder = goal3_swe.run_control(
        root=tmp_path, dataset_jsonl=dataset, instance_id="case", control=control,
        runs_dir=tmp_path / "goal3-control", run_id=f"control-{control}",
    )
    events = [json.loads(line) for line in (recorder.path / "events.jsonl").read_text().splitlines()]
    started = next(event for event in events if event["type"] == "control_started")
    completed = next(event for event in events if event["type"] == "control_completed")
    prediction = json.loads((recorder.path / "predictions.json").read_text())[0]
    assert started["model_called"] is False and started["network_called"] is False
    assert completed["resolved"] is resolved
    assert prediction["model_patch"] == ("" if control == "empty" else "diff --git a/a b/a\n")


def test_goal3_manifest_and_default_paths_are_isolated(tmp_path: Path) -> None:
    manifest = goal3_swe.build_pilot_manifest(root=tmp_path)
    assert manifest.experiment_kind.startswith("goal3-swe-")
    assert "goal2" not in str(goal3_swe.DEFAULT_RUNS_DIR)
    with pytest.raises(ValueError, match="Goal 2"):
        goal3_swe.dry_run(root=tmp_path, runs_dir=tmp_path / "goal2-swe", run_id="bad")


def _patch(file_count: int) -> str:
    return "\n".join(
        f"--- a/f{index}.py\n+++ b/f{index}.py\n@@ -1 +1 @@\n-a\n+b"
        for index in range(file_count)
    )


def test_pilot_freeze_binds_three_fixed_official_instances_and_price_hash(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    rows = [
        {"instance_id": "one", "repo": "org/one", "platform": "linux", "patch": _patch(1)},
        {"instance_id": "medium", "repo": "org/medium", "platform": "linux", "patch": _patch(3)},
        {"instance_id": "large", "repo": "org/large", "platform": "linux", "patch": _patch(5)},
    ]
    dataset.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    output = tmp_path / "goal3" / "pilot.frozen.json"
    payload = goal3_swe.freeze_pilot_config(
        dataset_jsonl=dataset, output=output, dataset_revision="official-revision",
        provider="verified-provider", model_id="verified-model",
        model_parameters={"temperature": None, "top_p": None, "max_output_tokens": 8192},
        pricing_snapshot_hash="a" * 64,
    )
    assert payload["status"] == "frozen"
    assert payload["experiment_kind"] == "goal3-swe-bench-live-pilot"
    assert payload["instance_ids"] == ["large", "medium", "one"]
    assert set(payload["instance_payload_hashes"]) == set(payload["instance_ids"])
    assert json.loads(output.read_text()) == payload


def test_goal3_budget_paths_are_named_but_not_created() -> None:
    paths = goal3_swe.goal3_budget_paths()
    assert paths == {
        "authorization": Path("evals/.runs/goal3-swe/budget-authorization.json"),
        "ledger": Path("evals/.runs/goal3-swe/budget-ledger.json"),
        "allocation": Path("evals/.runs/goal3-swe/budget-allocation.json"),
    }
    assert not any(path.exists() for path in paths.values())


def test_paid_workflow_uses_one_goal3_only_artifact_root() -> None:
    workflow = Path(".github/workflows/goal3-swe-paid-pilot.yml").read_text(encoding="utf-8")
    root = "${{ runner.temp }}/goal3-swe-paid/${{ github.run_id }}"
    assert 'FREEZE_ROOT="$RUNNER_TEMP/goal3-swe-paid/$GITHUB_RUN_ID"' in workflow
    assert workflow.count(f"path: {root}") == 2
    assert "${{ github.workspace }}/.runs/goal3-swe" not in workflow
    assert 'test -z "$(git status --porcelain)"' in workflow
    assert "if: ${{ inputs.execute_paid }}" in workflow


def test_existing_goal3_run_is_not_overwritten(tmp_path: Path) -> None:
    runs_dir = tmp_path / "goal3-swe"
    goal3_swe.dry_run(root=tmp_path, runs_dir=runs_dir, run_id="existing")
    with pytest.raises(ValueError, match="terminal Goal 3 Run"):
        goal3_swe.ensure_new_paid_pilot_run(
            runs_dir=runs_dir, run_id="existing", ledger_path=tmp_path / "ledger.json",
        )


def test_active_goal3_ledger_blocks_future_paid_pilot(tmp_path: Path) -> None:
    ledger = tmp_path / "goal3-ledger.json"
    ledger.write_text(json.dumps({
        "schema_version": 2, "currency": "CNY", "authorization_hash": "a" * 64, "spent_cny": "0.000000",
        "active_reservation": {
            "reservation_id": "r", "trial_id": "goal3/test", "stage": "C",
            "maximum_requests": 1, "maximum_input_tokens_per_request": 1,
            "maximum_output_tokens_per_request": 1, "reserved_cny": "1.000000", "created_at": "2026-01-01T00:00:00Z",
        }, "request_charges": [], "settlements": [], "budget_blocks": [],
        "authorization_rebinds": [], "updated_at": "2026-01-01T00:00:00Z",
    }))
    with pytest.raises(ValueError, match="active reservation"):
        goal3_swe.ensure_new_paid_pilot_run(
            runs_dir=tmp_path / "goal3-swe", run_id="new", ledger_path=ledger,
        )


def _frozen_pilot(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    input_root = tmp_path / "goal3-inputs"
    input_root.mkdir()
    pricing_path = input_root / "pricing.json"
    pricing_path.write_text(json.dumps({
        "schema_version": 1, "retrieved_at": "2026-07-19T00:00:00Z",
        "source_url": "https://pricing.example", "rate_limit_source_url": "https://limits.example",
        "provider": "bailian-qwen37-max", "model_id": "qwen3.7-max-2026-06-08",
        "deployment_scope": "Chinese mainland", "region": "China (Beijing)", "currency": "CNY",
        "token_range": "0<Token<=1M", "unit_tokens": 1000000, "input_price": 12.0,
        "output_price": 36.0, "requests_per_minute": 600, "tokens_per_minute": 1000000,
        "assumptions": ["standard list price", "no discounts", "Usage is authoritative"],
    }))
    pricing = goal3_swe.load_pricing(pricing_path)
    rows = [
        {"instance_id": "one", "repo": "org/one", "platform": "linux", "base_commit": "a" * 40,
         "patch": _patch(1), "problem_statement": "one"},
        {"instance_id": "medium", "repo": "org/medium", "platform": "linux", "base_commit": "b" * 40,
         "patch": _patch(3), "problem_statement": "medium"},
        {"instance_id": "large", "repo": "org/large", "platform": "linux", "base_commit": "c" * 40,
         "patch": _patch(5), "problem_statement": "large"},
    ]
    dataset = input_root / "pilot-dataset.jsonl"
    dataset.write_text(
        "".join(json.dumps(goal3_swe.execution_instance_payload(row)) + "\n" for row in rows),
        encoding="utf-8",
    )
    selected = goal3_swe.select_pilot_instances(rows)
    pilots = [{
        "instance_id": item["instance_id"], "repo": item["repo"],
        "size_bucket": size_bucket(item),
        "gold_file_count": patch_file_count(item["patch"]),
        "payload_sha256": goal3_swe.instance_payload_hash(item),
        "execution_payload_sha256": goal3_swe.execution_instance_payload_hash(item),
    } for item in selected]
    matrix = {
        "dataset_revision": "a" * 40, "dataset_source_sha256": "b" * 64,
        "selection_algorithm": "python-lite-size-stratified-v1", "pilots": pilots,
    }
    frozen = {
        "schema_version": 1, "status": "frozen_pending_authorization",
        "experiment_kind": "goal3-swe-bench-live-pilot", "codepacex_commit": "c" * 40,
        "official_evaluator_commit": goal3_swe.ENVIRONMENT["commit"],
        "dataset": goal3_swe.ENVIRONMENT["dataset"], "dataset_split": goal3_swe.ENVIRONMENT["split"],
        "dataset_revision": matrix["dataset_revision"], "dataset_source_sha256": matrix["dataset_source_sha256"],
        "selection_algorithm": matrix["selection_algorithm"],
        "matrix_sha256": goal3_swe.hashlib.sha256(json.dumps(matrix, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "provider": "bailian-qwen37-max", "protocol": "openai-compat",
        "base_url": "https://llm-ipge9fy38w648m28.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "BAILIAN_API_KEY", "model_id": "qwen3.7-max-2026-06-08",
        "pricing_snapshot_hash": pricing_snapshot_hash(pricing),
        "experiment_profile": {"schema_version": 1, "tool_loading": "deferred", "compression_profile": "recovery_v1", "permission_strategy": "session_allow", "agent_mode": "single"},
        "fallback_enabled": False, "retry_budget": 0, "serial": True,
        "max_provider_requests_per_instance": 50, "maximum_input_tokens_per_request": 128000,
        "maximum_output_tokens_per_request": 8192,
        "model_parameters": {"temperature": None, "top_p": None, "max_output_tokens": 8192},
        "pilots": pilots,
    }
    freeze_path = tmp_path / "pilot-freeze.json"
    freeze_path.write_text(json.dumps(frozen), encoding="utf-8")
    return freeze_path, dataset, frozen


def test_frozen_pilot_rejects_mismatched_current_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    freeze_path, _dataset, _frozen = _frozen_pilot(tmp_path)
    monkeypatch.setattr(goal3_swe, "current_git_commit", lambda root: "d" * 40)
    with pytest.raises(ValueError, match="current commit"):
        goal3_swe.load_frozen_pilot(freeze_path, root=tmp_path)


def test_paid_bundle_redacts_gold_data_and_binds_full_and_execution_payloads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    source = tmp_path / "goal3-inputs" / "official.jsonl"
    source.parent.mkdir()
    rows = [
        {"instance_id": "one", "repo": "org/one", "platform": "linux", "base_commit": "a" * 40,
         "patch": _patch(1), "test_patch": "secret-test", "problem_statement": "one"},
        {"instance_id": "medium", "repo": "org/medium", "platform": "linux", "base_commit": "b" * 40,
         "patch": _patch(3), "test_patch": "secret-test", "problem_statement": "medium"},
        {"instance_id": "large", "repo": "org/large", "platform": "linux", "base_commit": "c" * 40,
         "patch": _patch(5), "test_patch": "secret-test", "problem_statement": "large"},
    ]
    source.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    pricing = source.parent / "pricing.json"
    pricing.write_text(json.dumps({
        "schema_version": 1, "retrieved_at": "2026-07-19T00:00:00Z",
        "source_url": "https://pricing.example", "rate_limit_source_url": "https://limits.example",
        "provider": "bailian-qwen37-max", "model_id": "qwen3.7-max-2026-06-08",
        "deployment_scope": "Chinese mainland", "region": "China (Beijing)", "currency": "CNY",
        "token_range": "0<Token<=1M", "unit_tokens": 1000000, "input_price": 12.0,
        "output_price": 36.0, "requests_per_minute": 600, "tokens_per_minute": 1000000,
        "assumptions": ["standard list price", "no discounts", "Usage is authoritative"],
    }), encoding="utf-8")
    monkeypatch.setattr(goal3_swe, "current_git_commit", lambda root: "c" * 40)
    output = tmp_path / "goal3-freeze"
    frozen = goal3_swe.freeze_paid_pilot_bundle(
        root=tmp_path, dataset_jsonl=source, pricing_snapshot=pricing,
        output_dir=output, dataset_revision="d" * 40,
    )
    dataset_text = (output / "pilot-dataset.jsonl").read_text(encoding="utf-8")
    assert "patch" not in dataset_text and "secret-test" not in dataset_text
    assert frozen["codepacex_commit"] == "c" * 40
    assert all("execution_payload_sha256" in item for item in frozen["pilots"])
    assert goal3_swe.load_frozen_instances(pilot_freeze=frozen, dataset_jsonl=output / "pilot-dataset.jsonl")
    with pytest.raises(ValueError, match="already exists"):
        goal3_swe.freeze_paid_pilot_bundle(
            root=tmp_path, dataset_jsonl=source, pricing_snapshot=pricing,
            output_dir=output, dataset_revision="d" * 40,
        )


def test_create_paid_artifacts_is_goal3_only_and_refuses_rewrite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    run_root = tmp_path / "goal3-swe"
    run_root.mkdir()
    freeze_path, _dataset, _frozen = _frozen_pilot(tmp_path)
    pricing_path = tmp_path / "goal3-inputs" / "pricing.json"
    copied_freeze = run_root / "pilot-freeze.json"
    copied_pricing = run_root / "pricing-snapshot.json"
    copied_freeze.write_bytes(freeze_path.read_bytes())
    copied_pricing.write_bytes(pricing_path.read_bytes())
    monkeypatch.setattr(goal3_swe, "current_git_commit", lambda root: "c" * 40)
    paths = {name: run_root / f"budget-{name}.json" for name in ("authorization", "ledger", "allocation")}
    payload = goal3_swe.create_paid_artifacts(
        root=tmp_path, pilot_freeze_path=copied_freeze, pricing_snapshot=copied_pricing,
        budget_authorization=paths["authorization"], budget_ledger=paths["ledger"],
        budget_allocation=paths["allocation"], authorized_total_cny=Decimal("315.832320"),
    )
    assert payload["execution_ceiling_cny"] == "274.636800"
    assert all(path.exists() for path in paths.values())
    with pytest.raises(ValueError, match="already exists"):
        goal3_swe.create_paid_artifacts(
            root=tmp_path, pilot_freeze_path=copied_freeze, pricing_snapshot=copied_pricing,
            budget_authorization=paths["authorization"], budget_ledger=paths["ledger"],
            budget_allocation=paths["allocation"], authorized_total_cny=Decimal("315.832320"),
        )


def test_paid_preflight_does_not_need_or_call_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    run_root = tmp_path / "goal3-swe"
    run_root.mkdir()
    freeze_path, dataset, _frozen = _frozen_pilot(tmp_path)
    pricing_path = tmp_path / "goal3-inputs" / "pricing.json"
    copied_freeze = run_root / "pilot-freeze.json"
    copied_pricing = run_root / "pricing-snapshot.json"
    copied_freeze.write_bytes(freeze_path.read_bytes())
    copied_pricing.write_bytes(pricing_path.read_bytes())
    monkeypatch.setattr(goal3_swe, "current_git_commit", lambda root: "c" * 40)
    result = goal3_swe.paid_preflight(
        root=tmp_path, dataset_jsonl=dataset, pilot_freeze_path=copied_freeze,
        pricing_snapshot=copied_pricing, budget_authorization=run_root / "budget-authorization.json",
        budget_ledger=run_root / "budget-ledger.json", budget_allocation=run_root / "budget-allocation.json",
    )
    assert result["valid"] is True
    assert result["paid_execution_enabled"] is False
    assert result["authorization_exists"] is False


def test_execute_requires_confirmation_before_any_preflight_or_provider_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    called = []
    monkeypatch.setattr(goal3_swe, "require_native_preflight", lambda **kwargs: called.append(kwargs))
    with pytest.raises(ValueError, match="confirm-paid-run"):
        goal3_swe.execute_pilot(
            root=tmp_path, dataset_jsonl=tmp_path / "goal3-inputs" / "pilot-dataset.jsonl",
            runs_dir=tmp_path / "goal3-swe", run_id="new",
            pilot_freeze_path=tmp_path / "goal3-swe" / "pilot-freeze.json",
            pricing_snapshot=tmp_path / "goal3-swe" / "pricing-snapshot.json",
            budget_authorization=tmp_path / "goal3-swe" / "budget-authorization.json",
            budget_ledger=tmp_path / "goal3-swe" / "budget-ledger.json",
            budget_allocation=tmp_path / "goal3-swe" / "budget-allocation.json",
            confirmed=False,
        )
    assert called == []


def test_execute_pilot_preserves_task_logs_and_reaches_official_evaluator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Regression for dynamic instance IDs rejected by the generic Artifact whitelist."""
    runs_dir = tmp_path / "goal3-swe"
    runs_dir.mkdir()
    freeze_path, dataset, frozen = _frozen_pilot(tmp_path)
    pricing_path = tmp_path / "goal3-inputs" / "pricing.json"
    for source, name in ((freeze_path, "pilot-freeze.json"), (pricing_path, "pricing-snapshot.json")):
        (runs_dir / name).write_bytes(source.read_bytes())
    rows = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines()]
    target = frozen["pilots"][0]
    row = next(item for item in rows if item["instance_id"] == target["instance_id"])
    row["instance_id"] = "aiogram__aiogram-1594"
    target["instance_id"] = row["instance_id"]
    target["execution_payload_sha256"] = goal3_swe.execution_instance_payload_hash(row)
    target["payload_sha256"] = "a" * 64
    (runs_dir / "pilot-freeze.json").write_text(json.dumps(frozen), encoding="utf-8")
    dataset.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    monkeypatch.setattr(goal3_swe, "current_git_commit", lambda _root: "c" * 40)
    monkeypatch.setenv("BAILIAN_API_KEY", "offline-test-key")
    monkeypatch.setattr(goal3_swe, "require_native_preflight", lambda **_kwargs: {
        "native_linux_x86_64": True, "installed_evaluator_commit": goal3_swe.ENVIRONMENT["commit"],
    })
    monkeypatch.setattr(goal3_swe, "ensure_new_paid_pilot_run", lambda **_kwargs: None)
    monkeypatch.setattr(goal3_swe, "_goal3_materialize_instance", lambda *_args: None)
    monkeypatch.setattr(goal3_swe, "_goal3_extract_patch", lambda _workspace: "diff --git a/a b/a\n")
    monkeypatch.setattr(goal3_swe, "_child_environment", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(goal3_swe, "_runtime_secrets", lambda _pilot: [])
    monkeypatch.setattr(goal3_swe, "provider_request_budget_environment", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(goal3_swe, "trace_usage", lambda _trace: (1, 0, 0))
    monkeypatch.setattr(goal3_swe, "_ingest_trace", lambda *_args: (1, 0, 0))
    monkeypatch.setattr(goal3_swe.subprocess, "run", lambda *_args, **_kwargs: _result(stdout="offline trace"))
    evaluator_calls: list[dict[str, object]] = []
    monkeypatch.setattr(goal3_swe, "run_official_evaluator", lambda **kwargs: evaluator_calls.append(kwargs) or _result(stdout="official"))
    monkeypatch.setattr(goal3_swe, "collect_official_outcomes", lambda _root, ids: {item: True for item in ids})

    class FakeGate:
        def trial_accounting(self, trial_id: str) -> dict[str, object]:
            return {
                "trial_id": trial_id, "budget_blocked": False, "budget_block_reasons": [],
                "active_reservation": None, "request_count": 1, "actual_cny": "0.000000",
            }

    monkeypatch.setattr(goal3_swe, "PaidRunGate", lambda **_kwargs: FakeGate())
    paths = {name: runs_dir / f"budget-{name}.json" for name in ("authorization", "ledger", "allocation")}
    recorder = goal3_swe.execute_pilot(
        root=tmp_path, dataset_jsonl=dataset, runs_dir=runs_dir, run_id="offline-artifact-chain",
        pilot_freeze_path=runs_dir / "pilot-freeze.json", pricing_snapshot=runs_dir / "pricing-snapshot.json",
        budget_authorization=paths["authorization"], budget_ledger=paths["ledger"],
        budget_allocation=paths["allocation"], confirmed=True,
    )

    task_artifacts = json.loads((recorder.path / "task-artifacts.json").read_text(encoding="utf-8"))["artifacts"]
    assert len(evaluator_calls) == 3
    assert json.loads((recorder.path / "result.json").read_text(encoding="utf-8"))["official_evaluator_completed"] is True
    assert {entry["kind"] for entry in task_artifacts} == {"stdout", "stderr", "evaluator"}
    assert {entry["task_id"] for entry in task_artifacts} == {row["instance_id"] for row in rows}
    assert all((recorder.path / "artifacts" / entry["name"]).is_file() for entry in task_artifacts)


def test_paid_contract_rejects_goal2_dataset_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    run_root = tmp_path / "goal3-swe"
    run_root.mkdir()
    freeze_path, dataset, _frozen = _frozen_pilot(tmp_path)
    pricing_path = tmp_path / "goal3-inputs" / "pricing.json"
    copied_freeze = run_root / "pilot-freeze.json"
    copied_pricing = run_root / "pricing-snapshot.json"
    copied_freeze.write_bytes(freeze_path.read_bytes())
    copied_pricing.write_bytes(pricing_path.read_bytes())
    monkeypatch.setattr(goal3_swe, "current_git_commit", lambda root: "c" * 40)
    goal2_dataset = tmp_path / "goal2-swe" / dataset.name
    goal2_dataset.parent.mkdir()
    goal2_dataset.write_bytes(dataset.read_bytes())
    with pytest.raises(ValueError, match="Goal 2"):
        goal3_swe.paid_preflight(
            root=tmp_path, dataset_jsonl=goal2_dataset, pilot_freeze_path=copied_freeze,
            pricing_snapshot=copied_pricing, budget_authorization=run_root / "budget-authorization.json",
            budget_ledger=run_root / "budget-ledger.json", budget_allocation=run_root / "budget-allocation.json",
        )

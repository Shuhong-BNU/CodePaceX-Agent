from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import evals.goal3_swe as goal3_swe


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
        "authorization": Path("evals/.runs/goal3-control/budget-authorization.json"),
        "ledger": Path("evals/.runs/goal3-control/budget-ledger.json"),
        "allocation": Path("evals/.runs/goal3-control/budget-allocation.json"),
    }
    assert not any(path.exists() for path in paths.values())


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

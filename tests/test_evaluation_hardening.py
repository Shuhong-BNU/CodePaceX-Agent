from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from evals.benchmark import RunManifest, RunRecorder
from evals.swe_bench_live import (
    build_evaluator_command,
    main,
    run_official_evaluator,
    select_instances,
    write_frozen_manifest,
)


def _command(tmp_path: Path, ids: list[str]) -> list[str]:
    return build_evaluator_command(
        dataset_name="org/live", split="test", predictions_path=tmp_path / "predictions.json",
        instance_ids=ids, max_workers=2, run_id="run-1", namespace="codepacex",
        python_executable="python3",
    )


def test_official_command_uses_current_module_and_arguments(tmp_path: Path) -> None:
    command = _command(tmp_path, ["one", "two"])
    assert command[:3] == ["python3", "-m", "swebench.harness.run_evaluation"]
    assert command[command.index("--dataset_name") + 1] == "org/live"
    assert command[command.index("--instance_ids") + 1:] == ["one", "two"]
    assert "--predictions_path" in command
    assert "--max_workers" in command
    assert "--namespace" in command


def test_empty_instance_ids_omit_argument(tmp_path: Path) -> None:
    assert "--instance_ids" not in _command(tmp_path, [])


def test_arm64_local_build_namespace_and_report_dir_are_supported(tmp_path: Path) -> None:
    command = build_evaluator_command(
        dataset_name="org/live", split="lite",
        predictions_path=tmp_path / "predictions.json", instance_ids=["one"],
        max_workers=1, run_id="arm", namespace="", report_dir=tmp_path / "reports",
    )
    assert command[command.index("--namespace") + 1] == ""
    assert command[command.index("--report_dir") + 1] == str(tmp_path / "reports")


def test_selection_is_stable_and_optionally_filters_language() -> None:
    items = [
        {"instance_id": "b", "repo": "repo", "platform": "linux", "language": "python"},
        {"instance_id": "a", "repo": "repo", "platform": "linux", "language": "python"},
        {"instance_id": "c", "repo": "repo", "platform": "linux", "language": "python"},
        {"instance_id": "d", "repo": "other", "platform": "windows", "language": "python"},
        {"instance_id": "e", "repo": "other", "platform": "linux", "language": "rust"},
    ]
    assert [item["instance_id"] for item in select_instances(items)] == ["a", "b", "e"]
    assert [item["instance_id"] for item in select_instances(items, language_field="language")] == ["a", "b"]


def test_frozen_manifest_is_stable_and_reproducible(tmp_path: Path) -> None:
    kwargs = dict(dataset_name="org/live", split="test", revision="abc", source="official", codepacex_commit="def", model="model", provider="provider")
    instances = [{"instance_id": "one", "repo": "repo"}]
    first, second = tmp_path / "one.json", tmp_path / "two.json"
    write_frozen_manifest(instances, first, **kwargs)
    write_frozen_manifest(instances, second, **kwargs)
    assert first.read_bytes() == second.read_bytes()
    payload = json.loads(first.read_text(encoding="utf-8"))
    assert payload["revision"] == "abc"
    assert payload["selection_algorithm"]
    assert payload["instances"] == instances


def test_missing_evaluator_is_clear_error(tmp_path: Path) -> None:
    predictions = tmp_path / "predictions.json"
    predictions.write_text("[]", encoding="utf-8")
    with patch("evals.swe_bench_live.importlib.util.find_spec", return_value=None):
        with pytest.raises(RuntimeError, match="not installed"):
            run_official_evaluator(
                dataset_name="org/live", split="test", predictions_path=predictions,
                instance_ids=[], max_workers=1, run_id="run", namespace="codepacex",
            )


def test_evaluator_return_code_is_propagated(tmp_path: Path) -> None:
    predictions = tmp_path / "predictions.json"
    predictions.write_text("[]", encoding="utf-8")
    completed = subprocess.CompletedProcess(["swebench"], 7, "out", "err")
    with patch("evals.swe_bench_live.importlib.util.find_spec", return_value=object()), patch(
        "evals.swe_bench_live.subprocess.run", return_value=completed
    ):
        result = run_official_evaluator(
            dataset_name="org/live", split="test", predictions_path=predictions,
            instance_ids=["one"], max_workers=1, run_id="run", namespace="codepacex",
        )
    assert result.returncode == 7


def test_cli_dry_run_needs_no_evaluator(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    result = main([
        "--dataset-name", "org/live", "--predictions-path", str(tmp_path / "missing.json"),
        "--run-id", "run", "--namespace", "codepacex", "--dry-run",
    ])
    assert result == 0
    assert "swebench.harness.run_evaluation" in capsys.readouterr().out


def test_recorder_core_files_and_optional_artifacts(tmp_path: Path) -> None:
    recorder = RunRecorder(tmp_path, RunManifest(kind="dry-run", model="model", provider="provider"))
    assert (recorder.path / "events.jsonl").exists()
    recorder.write_artifact("patch.diff", "diff")
    recorder.finalize({"success": True, "api_key": "secret"})
    for name in ("manifest.json", "environment.json", "events.jsonl", "result.json", "report.md"):
        assert (recorder.path / name).exists()
    assert (recorder.path / "artifacts/patch.diff").read_text(encoding="utf-8") == "diff"
    assert "secret" not in (recorder.path / "report.md").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        recorder.write_artifact("../escape", "bad")

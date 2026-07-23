from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from evals.evaluation_v2 import golden_path
from evals.swe_bench_live import official_evaluator_report_path


def _git(workspace: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=workspace, check=True, text=True, capture_output=True)


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "beets").mkdir(parents=True)
    (workspace / "beets" / "importer.py").write_text("from __future__ import annotations\n\ndef import_item():\n    return None\n", encoding="utf-8")
    (workspace / "test").mkdir()
    (workspace / "test" / "test_importer.py").write_text("class ImportTest:\n    def test_set_fields(self):\n        assert True\n", encoding="utf-8")
    _git(workspace, "init", "-q")
    _git(workspace, "add", ".")
    _git(workspace, "-c", "user.email=evaluation-v2@example.invalid", "-c", "user.name=Evaluation V2", "commit", "-qm", "base")
    return workspace


def _fake_evaluator(tmp_path: Path) -> Path:
    package = tmp_path / "fake" / "swebench" / "harness"
    package.mkdir(parents=True)
    for directory in (package.parent, package):
        (directory / "__init__.py").write_text("", encoding="utf-8")
    (package / "run_evaluation.py").write_text(
        "import argparse, json\n"
        "from pathlib import Path\n"
        "p=argparse.ArgumentParser(); p.add_argument('--predictions_path'); p.add_argument('--run_id'); p.add_argument('--instance_ids', nargs='+'); p.add_argument('--namespace'); p.add_argument('--dataset_name'); p.add_argument('--split'); p.add_argument('--max_workers'); p.add_argument('--report_dir'); a=p.parse_args()\n"
        "prediction=json.loads(Path(a.predictions_path).read_text())[0]; instance=a.instance_ids[0]; model=prediction['model_name_or_path']\n"
        "report=Path('logs/run_evaluation')/a.run_id/model/instance/'report.json'; report.parent.mkdir(parents=True, exist_ok=True)\n"
        "report.write_text(json.dumps({instance:{'patch_is_None':False,'patch_exists':True,'patch_successfully_applied':True,'resolved':False,'tests_status':{}}}))\n",
        encoding="utf-8",
    )
    return tmp_path / "fake"


def test_native_replay_runs_agent_tools_exports_bound_candidate_and_executes_evaluator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _workspace(tmp_path)
    fake = _fake_evaluator(tmp_path)
    monkeypatch.syspath_prepend(str(fake))
    monkeypatch.setenv("PYTHONPATH", str(fake) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    result = golden_path.run_golden_path(workspace=workspace, artifact_root=tmp_path / "artifact", run_id="v2-test")
    summary = result.summary
    assert summary["provider_requests"] == summary["usage"] == summary["settlement_added"] == 0
    assert summary["active_reservation"] is None
    assert summary["real_agent_loop"] and summary["real_edit_or_write"] == 1
    assert summary["pre_edit_test_executed"] and summary["post_edit_test_executed"]
    assert summary["candidate_matches_workspace_diff"]
    assert summary["official_evaluator_executed"] and summary["official_outcome"] == "unresolved"
    trace = json.loads((result.artifact_root / "agent-tool-trace.json").read_text(encoding="utf-8"))
    assert [item["toolName"] for item in trace] == ["ReadFile", "Grep", "RunTest", "EditFile", "RunTest"]
    ledger = json.loads((result.artifact_root / "zero-provider-ledger.json").read_text(encoding="utf-8"))
    assert ledger["reservations"][0]["reserved_cny"] == "0"
    assert "model_patch" in (result.artifact_root / "candidate.json").read_text(encoding="utf-8")


def test_report_collector_prefers_detailed_falls_back_to_summary_and_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "reports"
    detailed = root / "logs" / "run_evaluation" / "run" / "model" / "case" / "report.json"
    detailed.parent.mkdir(parents=True)
    detailed.write_text("{}", encoding="utf-8")
    summary = root / "model.run.json"
    summary.write_text("{}", encoding="utf-8")
    assert official_evaluator_report_path(cwd=root, run_id="run", model_id="model", instance_id="case") == detailed
    detailed.unlink()
    assert official_evaluator_report_path(cwd=root, run_id="run", model_id="model", instance_id="case") == summary
    detailed.write_text("{}", encoding="utf-8")
    detailed.with_name("report-extra.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="multiple report candidates"):
        official_evaluator_report_path(cwd=root, run_id="run", model_id="model", instance_id="case")

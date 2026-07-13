from pathlib import Path
import sys

import pytest

from codepacex.config import MCPServerConfig
from codepacex.mcp.client import MCPClient

from evals.mcp_study import (
    dry_run,
    execute,
    grade_trace,
    load_study,
    profiles,
    scoped_tasks,
    study_asset_hash,
    top_level_trial_count,
)


STUDY = Path("evals/goal2/mcp_study.yaml")


def test_frozen_mcp_matrix_has_30_independent_tasks_and_300_trials() -> None:
    study, tasks = load_study(STUDY)
    assert len({task.id for task in tasks.tasks}) == 30
    assert top_level_trial_count(study, tasks) == 300
    assert len(study_asset_hash(STUDY, study)) == 64
    assert study_asset_hash(STUDY, study) == study_asset_hash(STUDY.resolve(), study)


def test_mcp_pilot_scope_is_paired_one_task_per_category() -> None:
    study, tasks = load_study(STUDY)
    selected, repetitions = scoped_tasks(study, tasks, scope="pilot")
    assert repetitions == 1
    assert [task.category for task in selected] == ["no_mcp", "one_mcp", "multi_mcp"]
    assert len({task.id for task in selected}) == 3


def test_mcp_arms_change_effective_tool_loading_not_labels_only() -> None:
    study, _ = load_study(STUDY)
    eager, deferred = profiles(study)
    assert eager.effective_runtime()["defer_mcp_tools"] is False
    assert deferred.effective_runtime()["defer_mcp_tools"] is True
    assert eager.runtime_contract_hash() != deferred.runtime_contract_hash()


def test_controlled_tasks_reference_only_fixture_tools() -> None:
    _, manifest = load_study(STUDY)
    assert all(
        name.startswith("mcp_fixture_tool_")
        for task in manifest.tasks for name in task.expected_tools
    )


@pytest.mark.asyncio
async def test_fixture_server_exposes_exactly_50_tools_over_real_stdio_protocol() -> None:
    client = MCPClient(MCPServerConfig(
        name="fixture", command=sys.executable,
        args=[str(Path("evals/fixtures/mcp_protocol_server.py").resolve())],
    ))
    try:
        await client.connect()
        tools = await client.list_tools()
    finally:
        await client.close()
    assert [tool.name for tool in tools] == [f"tool_{index:02d}" for index in range(1, 51)]


def test_mcp_dry_run_creates_two_unscorable_v2_arm_manifests(tmp_path: Path) -> None:
    recorders = dry_run(
        root=Path.cwd(), study_path=STUDY,
        runs_dir=tmp_path, run_prefix="matrix",
    )
    assert [recorder.run_id for recorder in recorders] == [
        "matrix-eager", "matrix-deferred",
    ]
    for recorder in recorders:
        import json

        manifest = json.loads((recorder.path / "manifest.json").read_text())
        result = json.loads((recorder.path / "result.json").read_text())
        assert manifest["schema_version"] == 2
        assert manifest["benchmark_asset_hash"]
        assert result["status"] == "dry_run" and result["scorable"] is False


def test_trace_grader_requires_exact_mcp_tool_set_and_answer() -> None:
    _, manifest = load_study(STUDY)
    task = next(task for task in manifest.tasks if task.id == "mcp_one_01")
    valid = "\n".join([
        '{"type":"tool_use","tool_name":"ToolSearch"}',
        '{"type":"tool_use","tool_name":"mcp_fixture_tool_01"}',
        '{"type":"result","result":"tool_01:one-01"}',
    ])
    passed, grade = grade_trace(task, valid)
    assert passed and grade["answer_match"] is True

    unexpected = valid.replace(
        '{"type":"result"',
        '{"type":"tool_use","tool_name":"mcp_fixture_tool_02"}\n{"type":"result"',
    )
    assert grade_trace(task, unexpected)[0] is False


def test_paid_execute_is_blocked_without_confirmation_or_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BAILIAN_API_KEY", raising=False)
    pricing = tmp_path / "pricing.json"
    pricing.write_text("{}")
    with pytest.raises(ValueError, match="confirm-paid-run"):
        execute(
            root=Path.cwd(), study_path=STUDY, runs_dir=tmp_path / "runs",
            run_prefix="blocked", pricing_snapshot=pricing, confirmed=False,
        )

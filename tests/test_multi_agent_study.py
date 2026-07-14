import json
import os
import subprocess
import sys
from pathlib import Path

from codepacex.experiments import AgentMode
from evals.goal2_studies import load_studies
from evals.multi_agent_study import (
    _prepare_workspace,
    agent_summary,
    dry_run,
    grader_preflight,
    grade_trial,
    profiles,
    scoped_tasks,
    success_rate_fields,
)


STUDIES = Path("evals/goal2/studies.yaml")


def test_multi_agent_profiles_change_real_tool_availability_contract() -> None:
    single, multi = profiles(load_studies(STUDIES))
    assert single.effective_runtime()["multi_agent_tools_enabled"] is False
    assert multi.effective_runtime()["multi_agent_tools_enabled"] is True
    assert single.permission_strategy.value == multi.permission_strategy.value == "session_allow"


def test_multi_agent_pilot_scope_pairs_both_modes_on_one_task() -> None:
    tasks, repetitions = scoped_tasks(load_studies(STUDIES), scope="pilot")
    assert repetitions == 1
    assert [task.id for task in tasks] == ["multi_parser_commands"]


def test_frozen_fixture_starts_with_each_task_failing(tmp_path: Path) -> None:
    _prepare_workspace(tmp_path)
    for task in load_studies(STUDIES).multi_agent.tasks:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", task.test_file],
            cwd=tmp_path, text=True, capture_output=True, check=False,
            env={**os.environ, "PYTHONPATH": str(tmp_path)},
        )
        assert result.returncode == 1, (task.id, result.stdout, result.stderr)


def test_multi_agent_grader_requires_delegation_and_exact_change_scope(tmp_path: Path) -> None:
    _prepare_workspace(tmp_path)
    task = load_studies(STUDIES).multi_agent.tasks[0]
    for path in task.expected_files:
        target = tmp_path / path
        target.write_text(target.read_text() + "\n# changed\n", encoding="utf-8")
    trace = json.dumps({
        "type": "experiment_agent_summary", "agent_mode": "multi",
        "child_count": 1, "completed_child_count": 1, "failed_child_count": 0,
        "child_input_tokens": 10, "child_output_tokens": 5,
        "child_request_count": 1, "maximum_parallel_children": 1,
    })
    assert agent_summary(trace) is not None
    passed, grade = grade_trial(
        task=task, mode=AgentMode.MULTI, trace_text=trace,
        workspace=tmp_path, test_returncode=0,
    )
    assert passed and grade["delegation_ok"] is True
    no_child = trace.replace('"child_count": 1', '"child_count": 0')
    assert not grade_trial(
        task=task, mode=AgentMode.MULTI, trace_text=no_child,
        workspace=tmp_path, test_returncode=0,
    )[0]


def test_multi_agent_dry_run_creates_two_unscorable_arms(tmp_path: Path) -> None:
    recorders = dry_run(
        root=Path.cwd(), studies_path=STUDIES,
        runs_dir=tmp_path, run_prefix="agents",
    )
    assert [item.run_id for item in recorders] == ["agents-single", "agents-multi"]
    assert all(
        json.loads((item.path / "result.json").read_text())["status"] == "dry_run"
        for item in recorders
    )


def test_multi_agent_grader_preflight_is_zero_model_and_blocks_runtime_noise() -> None:
    result = grader_preflight(studies_path=STUDIES)
    assert result["model_called"] is False
    assert result["network_called"] is False
    assert result["test_returncode"] == 0
    assert result["status"] == "NO-GO"
    assert result["grade"]["exact_change_scope"] is False


def test_multi_agent_success_rate_fields_count_every_formal_trial() -> None:
    assert success_rate_fields("success") == {"numerator": 1, "denominator": 1}
    assert success_rate_fields("task_failure") == {"numerator": 0, "denominator": 1}

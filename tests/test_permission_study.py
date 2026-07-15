import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from codepacex.experiments import PermissionStrategy
from evals.benchmark import RunRecorder
from evals.goal2_studies import load_studies
from evals.permission_study import (
    _run_profile, build_manifest, dangerous_interception_fields, dry_run, grade_trace,
    profiles, scoped_tasks, selected_profiles, trace_usage, validate_batch_limit,
)


STUDIES = Path("evals/goal2/studies.yaml")


def test_permission_matrix_freezes_concrete_tool_arguments() -> None:
    studies = load_studies(STUDIES)
    assert len(studies.permission.tasks) == 10
    assert len(profiles(studies)) == 4
    assert all(task.prompt and task.arguments for task in studies.permission.tasks)
    dangerous = [task for task in studies.permission.tasks if task.dangerous]
    assert dangerous and all(task.explicit_rule_effect == "deny" for task in dangerous)


def test_permission_pilot_scope_pairs_one_safe_and_one_dangerous_task() -> None:
    tasks, repetitions = scoped_tasks(load_studies(STUDIES), scope="pilot")
    assert repetitions == 1
    assert [task.dangerous for task in tasks] == [False, True]


def test_permission_batch_selection_preserves_the_frozen_strategy_coordinate() -> None:
    studies = load_studies(STUDIES)
    assert [item.permission_strategy.value for item in selected_profiles(
        studies, strategy="explicit_rules",
    )] == ["explicit_rules"]
    assert len(selected_profiles(studies, strategy=None)) == 4
    with pytest.raises(ValueError, match="unknown permission strategy"):
        selected_profiles(studies, strategy="not-a-strategy")


def test_permission_batch_limit_is_small_positive_or_unbounded() -> None:
    assert validate_batch_limit(None) is None
    assert validate_batch_limit(1) == 1
    assert validate_batch_limit(5) == 5
    for value in (0, 6):
        with pytest.raises(ValueError, match="between 1 and 5"):
            validate_batch_limit(value)


def test_permission_batch_limit_stops_after_one_terminal_trial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    studies = load_studies(STUDIES)
    profile = profiles(studies)[0]
    recorder = RunRecorder(
        tmp_path, build_manifest(
            root=Path.cwd(), studies_path=STUDIES, studies=studies,
            profile=profile, scope="formal",
        ), run_id="permission-batch",
    )

    class FakeGate:
        def trial_accounting(self, _trial_id: str) -> dict[str, object]:
            return {
                "budget_blocked": False, "budget_block_reasons": [],
                "active_reservation": None, "request_count": 1,
                "actual_cny": "0.001000",
            }

    first_task = studies.permission.tasks[0]
    trace = "\n".join([
        json.dumps({"type": "tool_use", "tool_name": first_task.tool, "tool_use_id": "tool-1"}),
        json.dumps({
            "type": "permission_decision", "tool_name": first_task.tool,
            "tool_use_id": "tool-1", "final_effect": "deny", "executed": False,
            "hitl_required": False,
        }),
        json.dumps({"type": "usage", "request_input_tokens": 10, "request_output_tokens": 1}),
    ])
    monkeypatch.setattr("evals.permission_study._child_environment", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("evals.permission_study.provider_request_budget_environment", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        "evals.permission_study.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=trace, returncode=0),
    )
    assert _run_profile(
        root=Path.cwd(), studies=studies, profile=profile, recorder=recorder,
        gate=FakeGate(), scope="formal", max_new_trials=1,
    ) == "cancelled"
    assert recorder.terminal_trial_statuses() == {(first_task.id, "1"): "success"}
    events = [json.loads(line) for line in (recorder.path / "events.jsonl").read_text().splitlines()]
    assert events[-1] == {
        "type": "batch_limit_reached", "max_new_trials": 1,
        "completed_new_trials": 1, "scope": "formal", "schema_version": 2,
        "timestamp": events[-1]["timestamp"],
    }


def test_permission_grader_rejects_dangerous_execution() -> None:
    task = next(
        task for task in load_studies(STUDIES).permission.tasks
        if task.id == "perm_delete_command"
    )
    safe_trace = "\n".join([
        '{"type":"tool_use","tool_name":"Bash"}',
        '{"type":"permission_decision","tool_name":"Bash","final_effect":"deny","executed":false,"hitl_required":false}',
    ])
    assert grade_trace(task, safe_trace, strategy=PermissionStrategy.EXPLICIT_RULES)[0]
    assert not grade_trace(
        task, safe_trace.replace('"executed":false', '"executed":true'),
        strategy=PermissionStrategy.EXPLICIT_RULES,
    )[0]


def test_permission_trace_usage_counts_actual_provider_requests() -> None:
    trace = "\n".join([
        '{"type":"usage","request_input_tokens":100,"request_output_tokens":20}',
        '{"type":"usage","request_input_tokens":50,"request_output_tokens":10}',
    ])
    assert trace_usage(trace) == (2, 150, 30)


def test_permission_trace_usage_rejects_cumulative_only_usage() -> None:
    with pytest.raises(ValueError, match="per-request"):
        trace_usage('{"type":"usage","input_tokens":50,"output_tokens":10}')


def test_dangerous_interception_fields_exclude_safe_tasks() -> None:
    studies = load_studies(STUDIES)
    dangerous = next(task for task in studies.permission.tasks if task.dangerous)
    safe = next(task for task in studies.permission.tasks if not task.dangerous)
    assert dangerous_interception_fields(
        dangerous, "success", {"unsafe_execution": False},
    ) == {"numerator": 1, "denominator": 1}
    assert dangerous_interception_fields(
        dangerous, "task_failure", {"unsafe_execution": True},
    ) == {"numerator": 0, "denominator": 1}
    assert dangerous_interception_fields(safe, "success", {}) == {}


def test_permission_dry_run_creates_four_unscorable_arms(tmp_path: Path) -> None:
    recorders = dry_run(
        root=Path.cwd(), studies_path=STUDIES,
        runs_dir=tmp_path, run_prefix="permission",
    )
    assert [item.run_id for item in recorders] == [
        "permission-default", "permission-session_allow",
        "permission-explicit_rules", "permission-sandbox_auto_allow",
    ]
    for recorder in recorders:
        result = (recorder.path / "result.json").read_text(encoding="utf-8")
        assert '"status": "dry_run"' in result

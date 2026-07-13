from pathlib import Path

from codepacex.experiments import PermissionStrategy
from evals.goal2_studies import load_studies
from evals.permission_study import dry_run, grade_trace, profiles, trace_usage


STUDIES = Path("evals/goal2/studies.yaml")


def test_permission_matrix_freezes_concrete_tool_arguments() -> None:
    studies = load_studies(STUDIES)
    assert len(studies.permission.tasks) == 10
    assert len(profiles(studies)) == 4
    assert all(task.prompt and task.arguments for task in studies.permission.tasks)
    dangerous = [task for task in studies.permission.tasks if task.dangerous]
    assert dangerous and all(task.explicit_rule_effect == "deny" for task in dangerous)


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
        '{"type":"usage","input_tokens":50,"output_tokens":10}',
    ])
    assert trace_usage(trace) == (2, 150, 30)


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

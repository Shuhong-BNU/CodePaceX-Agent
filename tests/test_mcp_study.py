import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from codepacex.config import MCPServerConfig
from codepacex.mcp.client import MCPClient
from codepacex.permissions import (
    DangerousCommandDetector,
    PathSandbox,
    PermissionChecker,
    PermissionMode,
    RuleEngine,
)

from evals.mcp_study import (
    MCPTask,
    dry_run,
    execute,
    fixture_permission_rules,
    grade_trace,
    load_study,
    _write_fixture_permissions,
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
    assert eager.permission_strategy == deferred.permission_strategy
    assert eager.runtime_contract_hash() != deferred.runtime_contract_hash()


def test_controlled_tasks_reference_only_fixture_tools() -> None:
    _, manifest = load_study(STUDY)
    assert all(
        name.startswith("mcp_fixture_tool_")
        for task in manifest.tasks for name in task.expected_tools
    )


def test_fixture_permission_rules_allow_only_manifest_fixture_tools() -> None:
    _, manifest = load_study(STUDY)
    rules = fixture_permission_rules(manifest)
    expected = {
        name for task in manifest.tasks for name in task.expected_tools
    }
    assert {rule["rule"][:-3] for rule in rules} == expected
    assert {rule["effect"] for rule in rules} == {"allow"}
    assert all("mcp_fixture_tool_*" not in rule["rule"] for rule in rules)


def test_fixture_permissions_are_narrow_and_explicit_deny_still_wins(
    tmp_path: Path,
) -> None:
    _, manifest = load_study(STUDY)
    _write_fixture_permissions(workspace=tmp_path, tasks=manifest)
    fixture = SimpleNamespace(
        name="mcp_fixture_tool_01", category="command", path_accesses=(),
        requires_explicit_authorization=False,
    )
    outside = SimpleNamespace(
        name="mcp_fixture_tool_50", category="command", path_accesses=(),
        requires_explicit_authorization=False,
    )
    user_rules = tmp_path / "home-permissions.yaml"
    checker = PermissionChecker(
        detector=DangerousCommandDetector(), sandbox=PathSandbox(tmp_path),
        rule_engine=RuleEngine(
            user_rules_path=user_rules,
            project_rules_path=tmp_path / ".codepacex" / "permissions.yaml",
        ),
        mode=PermissionMode.DEFAULT,
    )
    assert checker.check(fixture, {}).effect == "allow"
    assert checker.check(outside, {}).effect == "ask"

    user_rules.write_text(
        "- rule: mcp_fixture_tool_01(*)\n  effect: deny\n", encoding="utf-8",
    )
    assert checker.check(fixture, {}).effect == "deny"


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


def _trace(
    tools: list[str], answer: str, *, include_results: bool = True,
    result_error: bool = False,
) -> str:
    events = [
        {"type": "tool_use", "tool_name": tool, "tool_id": f"call-{index}"}
        for index, tool in enumerate(tools)
    ]
    if include_results:
        events.extend(
            {
                "type": "tool_result", "tool_name": tool,
                "tool_id": f"call-{index}", "is_error": result_error,
            }
            for index, tool in enumerate(tools)
            if tool.startswith("mcp_fixture_tool_")
        )
    events.append({"type": "result", "result": answer})
    return "\n".join(json.dumps(event) for event in events)


def test_trace_grader_requires_exact_mcp_tool_multiset_and_answer() -> None:
    _, manifest = load_study(STUDY)
    task = next(task for task in manifest.tasks if task.id == "mcp_one_01")
    valid = _trace(["ToolSearch", "mcp_fixture_tool_01"], "tool_01:one-01")
    passed, grade = grade_trace(task, valid)
    assert passed and grade["tools_match"] is True and grade["execution_match"] is True and grade["answer_match"] is True

    unexpected = _trace(
        ["mcp_fixture_tool_01", "mcp_fixture_tool_01"], "tool_01:one-01",
    )
    passed, grade = grade_trace(task, unexpected)
    assert not passed and grade["tools_match"] is False and grade["answer_match"] is True


def test_trace_grader_accepts_reordered_calls_and_repeated_expected_tools() -> None:
    task = MCPTask(
        id="multiplicity", category="multi_mcp", prompt="fixture",
        expected_tools=["mcp_fixture_tool_01", "mcp_fixture_tool_01"],
        expected_answer="tool_01:one-01|tool_01:one-01",
    )
    passed, grade = grade_trace(
        task,
        _trace(["mcp_fixture_tool_01", "mcp_fixture_tool_01"], "tool_01:one-01"),
    )
    assert passed and grade["observed_mcp_tools"] == task.expected_tools

    reordered = task.model_copy(update={
        "expected_tools": ["mcp_fixture_tool_01", "mcp_fixture_tool_02"],
        "expected_answer": "tool_01:one-01|tool_02:one-02",
    })
    assert grade_trace(
        reordered,
        _trace(["mcp_fixture_tool_02", "mcp_fixture_tool_01"], "tool_01:one-01 tool_02:one-02"),
    )[0]


@pytest.mark.parametrize("tools", [
    [],
    ["mcp_fixture_tool_02"],
    ["mcp_fixture_tool_01", "mcp_fixture_tool_02"],
])
def test_trace_grader_rejects_missing_or_different_mcp_calls(tools: list[str]) -> None:
    _, manifest = load_study(STUDY)
    task = next(task for task in manifest.tasks if task.id == "mcp_one_01")
    passed, grade = grade_trace(task, _trace(tools, "tool_01:one-01"))
    assert not passed and grade["tools_match"] is False and grade["answer_match"] is True


def test_trace_grader_keeps_no_mcp_answer_matching_strict() -> None:
    task = MCPTask(
        id="no-tools", category="no_mcp", prompt="fixture",
        expected_tools=[], expected_answer="exact",
    )
    assert grade_trace(task, _trace([], "exact"))[0]
    assert not grade_trace(task, _trace([], "exact plus"))[0]


@pytest.mark.parametrize("trace", [
    _trace(["mcp_fixture_tool_01"], "tool_01:one-01", include_results=False),
    _trace(["mcp_fixture_tool_01"], "tool_01:one-01", result_error=True),
    "\n".join([
        json.dumps({"type": "tool_use", "tool_name": "mcp_fixture_tool_01", "tool_id": "call-1"}),
        json.dumps({"type": "permission_decision", "tool_name": "mcp_fixture_tool_01", "tool_use_id": "call-1", "final_effect": "allow", "executed": True}),
        json.dumps({"type": "result", "result": "tool_01:one-01"}),
    ]),
    "\n".join([
        json.dumps({"type": "tool_use", "tool_name": "mcp_fixture_tool_01", "tool_id": "call-1"}),
        json.dumps({"type": "tool_result", "tool_name": "mcp_fixture_tool_01", "tool_id": "other", "is_error": False}),
        json.dumps({"type": "result", "result": "tool_01:one-01"}),
    ]),
])
def test_trace_grader_rejects_attempt_only_error_and_malformed_execution(
    trace: str,
) -> None:
    _, manifest = load_study(STUDY)
    task = next(task for task in manifest.tasks if task.id == "mcp_one_01")
    passed, grade = grade_trace(task, trace)
    assert not passed
    assert grade["tools_match"] is True
    assert grade["execution_match"] is False


def test_trace_grader_rejects_duplicate_or_extra_execution_evidence() -> None:
    _, manifest = load_study(STUDY)
    task = next(task for task in manifest.tasks if task.id == "mcp_one_01")
    duplicate = _trace(["mcp_fixture_tool_01"], "tool_01:one-01").replace(
        '{"type": "result", "result": "tool_01:one-01"}',
        '{"type": "tool_result", "tool_name": "mcp_fixture_tool_01", "tool_id": "call-0", "is_error": false}\n'
        '{"type": "result", "result": "tool_01:one-01"}',
    )
    passed, grade = grade_trace(task, duplicate)
    assert not passed and grade["execution_match"] is False


def test_trace_grader_rejects_answer_without_successful_execution() -> None:
    _, manifest = load_study(STUDY)
    task = next(task for task in manifest.tasks if task.id == "mcp_one_01")
    passed, grade = grade_trace(
        task, _trace(["mcp_fixture_tool_01"], "tool_01:one-01", include_results=False),
    )
    assert not passed
    assert grade["answer_match"] is True
    assert grade["execution_match"] is False


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

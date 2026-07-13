"""Deterministic 100-case Agent Hook consistency study (zero model calls)."""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from pydantic import BaseModel

from codepacex.agent import Agent, HookEvent, PermissionDecisionEvent
from codepacex.context import CompactEvent
from codepacex.conversation import ConversationManager
from codepacex.hooks import Action, Hook, HookEngine
from codepacex.hooks.conditions import parse_condition
from codepacex.permissions import (
    DangerousCommandDetector,
    PathSandbox,
    PermissionChecker,
    PermissionMode,
    RuleEngine,
)
from codepacex.tools import ToolRegistry
from codepacex.tools.base import StreamEnd, TextDelta, Tool, ToolCallComplete, ToolResult


PathName = Literal["sequential", "parallel", "streaming", "no_checker"]
PATHS: tuple[PathName, ...] = ("sequential", "parallel", "streaming", "no_checker")
CASES_PER_PATH = 25


class EmptyParams(BaseModel):
    pass


class CountingTool(Tool):
    description = "Goal 2 deterministic hook target"
    params_model = EmptyParams

    def __init__(self, name: str, *, concurrent: bool, category: str = "read") -> None:
        self.name = name
        self.is_concurrency_safe = concurrent
        self.category = category
        self.count = 0

    async def execute(self, params: EmptyParams) -> ToolResult:
        self.count += 1
        return ToolResult("ok")


class ScriptedClient:
    def __init__(self, calls: list[ToolCallComplete]) -> None:
        self.calls = calls
        self.turn = 0

    async def stream(
        self, conversation: ConversationManager, system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[Any]:
        self.turn += 1
        if self.turn == 1:
            for call in self.calls:
                yield call
            yield StreamEnd("tool_use")
        else:
            yield TextDelta("done")
            yield StreamEnd("end_turn")

    def set_max_output_tokens(self, tokens: int) -> None:
        pass


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    path: PathName
    expected_effect: str
    observed_effects: list[str]
    observed_paths: list[str]
    hook_success_count: int
    target_execution_count: int
    passed: bool


def _checker(work_dir: str) -> PermissionChecker:
    return PermissionChecker(
        DangerousCommandDetector(), PathSandbox(work_dir), RuleEngine(),
        PermissionMode.DEFAULT,
    )


async def run_case(path: PathName, index: int) -> CaseResult:
    reject = index % 2 == 0
    event = "pre_tool_use" if reject else "post_tool_use"
    hook = Hook(
        id=f"hook-{path}-{index:02d}", event=event,
        action=Action(type="command", command="printf hook-$TOOL_NAME"),
        condition=parse_condition('tool == "Target"'), reject=reject,
    )
    engine = HookEngine([hook])
    registry = ToolRegistry()
    calls: list[ToolCallComplete]
    target: CountingTool
    if path == "parallel":
        gate = CountingTool("Gate", concurrent=False)
        target = CountingTool("Target", concurrent=True)
        registry.register(gate)
        registry.register(target)
        calls = [
            ToolCallComplete("gate", "Gate", {}),
            ToolCallComplete("target-a", "Target", {}),
            ToolCallComplete("target-b", "Target", {}),
        ]
        expected_target_decisions = 2
        expected_path = "parallel"
    elif path == "streaming":
        target = CountingTool("Target", concurrent=True)
        registry.register(target)
        calls = [ToolCallComplete("target", "Target", {})]
        expected_target_decisions = 1
        expected_path = "streaming"
    else:
        target = CountingTool(
            "Target", concurrent=False,
            category="write" if path == "no_checker" else "read",
        )
        registry.register(target)
        calls = [ToolCallComplete("target", "Target", {})]
        expected_target_decisions = 1
        expected_path = "sequential"

    with tempfile.TemporaryDirectory(prefix="codepacex-hook-study-") as work_dir:
        checker = None if path == "no_checker" else _checker(work_dir)
        agent = Agent(
            ScriptedClient(calls), registry, "anthropic", work_dir=work_dir,
            permission_checker=checker, hook_engine=engine,
        )
        conversation = ConversationManager()
        conversation.add_user_message("run deterministic hook case")
        events = [item async for item in agent.run(conversation)]

    decisions = [
        item for item in events
        if isinstance(item, PermissionDecisionEvent) and item.tool_name == "Target"
    ]
    hook_events = [
        item for item in events
        if isinstance(item, HookEvent) and item.hook_id == hook.id and item.success
    ]
    expected_effect = "deny" if reject else "allow"
    expected_executions = 0 if reject else expected_target_decisions
    passed = (
        len(decisions) == expected_target_decisions
        and all(item.final_effect == expected_effect for item in decisions)
        and all(item.execution_path == expected_path for item in decisions)
        and len(hook_events) == expected_target_decisions
        and target.count == expected_executions
    )
    return CaseResult(
        case_id=f"{path}-{index:02d}", path=path,
        expected_effect=expected_effect,
        observed_effects=[item.final_effect for item in decisions],
        observed_paths=[item.execution_path for item in decisions],
        hook_success_count=len(hook_events),
        target_execution_count=target.count,
        passed=passed,
    )


async def run_study() -> dict[str, Any]:
    cases = [
        await run_case(path, index)
        for path in PATHS for index in range(1, CASES_PER_PATH + 1)
    ]
    passed = sum(case.passed for case in cases)
    return {
        "schema_version": 2,
        "study_id": "goal2-hook-consistency",
        "model_called": False,
        "network_called": False,
        "side_effect_policy": "command hooks use printf only; tools are in-memory",
        "case_count": len(cases),
        "passed_case_count": passed,
        "numerator": passed,
        "denominator": len(cases),
        "rate": passed / len(cases),
        "cases": [asdict(case) for case in cases],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic Hook study")
    parser.add_argument("command", choices=["validate", "run"])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.command == "validate":
        print(json.dumps({
            "valid": True, "paths": list(PATHS),
            "cases_per_path": CASES_PER_PATH,
            "case_count": len(PATHS) * CASES_PER_PATH,
            "model_called": False, "network_called": False,
        }, sort_keys=True))
        return 0
    result = asyncio.run(run_study())
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0 if result["passed_case_count"] == result["case_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from codepacex.agent import (
    Agent,
    CompressionEvent,
    PermissionDecisionEvent,
    PermissionRequest,
    PermissionResponse,
)
from codepacex.context import CompactEvent
from codepacex.conversation import ConversationManager
from codepacex.hooks import Action, Hook, HookEngine
from codepacex.permissions import (
    DangerousCommandDetector,
    PathSandbox,
    PermissionChecker,
    PermissionMode,
    RuleEngine,
)
from codepacex.tools import create_default_registry
from codepacex.tools.base import StreamEnd, TextDelta, Tool, ToolCallComplete, ToolResult


class EmptyParams(BaseModel):
    pass


class CommandParams(BaseModel):
    command: str


class CountingTool(Tool):
    description = "telemetry test tool"
    params_model = EmptyParams

    def __init__(self, name: str, *, category: str = "read", concurrent: bool = True) -> None:
        self.name = name
        self.category = category
        self.is_concurrency_safe = concurrent
        self.count = 0

    async def execute(self, params: EmptyParams) -> ToolResult:
        self.count += 1
        return ToolResult("ok")


class ScriptedClient:
    def __init__(self, calls: list[list[Any]]) -> None:
        self.calls = list(calls)

    async def stream(self, conversation, system="", tools=None):
        for event in self.calls.pop(0):
            yield event

    def set_max_output_tokens(self, tokens: int) -> None:
        pass


class CountingCommand(Tool):
    name = "Bash"
    description = "command sentinel"
    params_model = CommandParams
    category = "command"
    is_concurrency_safe = False

    def __init__(self) -> None:
        self.count = 0

    async def execute(self, params: CommandParams) -> ToolResult:
        self.count += 1
        return ToolResult("simulated")


def _checker(tmp_path, mode: PermissionMode = PermissionMode.DEFAULT) -> PermissionChecker:
    return PermissionChecker(
        DangerousCommandDetector(), PathSandbox(str(tmp_path)), RuleEngine(), mode,
    )


async def _run(agent: Agent) -> list[Any]:
    conversation = ConversationManager()
    conversation.add_user_message("run")
    events = []
    async for event in agent.run(conversation):
        if isinstance(event, PermissionRequest):
            event.future.set_result(PermissionResponse.ALLOW)
        events.append(event)
    return events


def _two_turn_client(*tool_calls: ToolCallComplete) -> ScriptedClient:
    return ScriptedClient([
        [*tool_calls, StreamEnd("tool_use", input_tokens=1, output_tokens=1)],
        [TextDelta("done"), StreamEnd("end_turn", input_tokens=1, output_tokens=1)],
    ])


@pytest.mark.asyncio
async def test_streaming_path_emits_one_final_permission_event(tmp_path) -> None:
    tool = CountingTool("StreamRead")
    registry = create_default_registry()
    registry.register(tool)
    agent = Agent(
        _two_turn_client(ToolCallComplete("s1", tool.name, {})),
        registry, "anthropic", work_dir=str(tmp_path),
    )
    events = await _run(agent)
    decisions = [event for event in events if isinstance(event, PermissionDecisionEvent)]
    assert [(event.tool_use_id, event.execution_path) for event in decisions] == [
        ("s1", "streaming")
    ]
    assert decisions[0].executed is True
    assert tool.count == 1


@pytest.mark.asyncio
async def test_parallel_and_sequential_paths_share_final_exit(tmp_path) -> None:
    serial = CountingTool("SerialRead", concurrent=False)
    parallel_one = CountingTool("ParallelOne")
    parallel_two = CountingTool("ParallelTwo")
    registry = create_default_registry()
    for tool in (serial, parallel_one, parallel_two):
        registry.register(tool)
    client = _two_turn_client(
        ToolCallComplete("q0", serial.name, {}),
        ToolCallComplete("q1", parallel_one.name, {}),
        ToolCallComplete("q2", parallel_two.name, {}),
    )
    events = await _run(Agent(client, registry, "anthropic", work_dir=str(tmp_path)))
    decisions = [event for event in events if isinstance(event, PermissionDecisionEvent)]
    assert [(event.tool_use_id, event.execution_path) for event in decisions] == [
        ("q0", "sequential"), ("q1", "parallel"), ("q2", "parallel"),
    ]
    assert all(event.executed for event in decisions)


@pytest.mark.asyncio
async def test_hook_deny_emits_once_after_final_effect_without_execution(tmp_path) -> None:
    tool = CountingTool("HookRead")
    registry = create_default_registry()
    registry.register(tool)
    hook = Hook(
        id="deny", event="pre_tool_use",
        action=Action(type="command", command="printf blocked"), reject=True,
    )
    agent = Agent(
        _two_turn_client(ToolCallComplete("h1", tool.name, {})),
        registry, "anthropic", work_dir=str(tmp_path),
        hook_engine=HookEngine([hook]),
    )
    decisions = [
        event for event in await _run(agent)
        if isinstance(event, PermissionDecisionEvent)
    ]
    assert len(decisions) == 1
    assert decisions[0].final_effect == "deny"
    assert decisions[0].hook_effect == "deny"
    assert decisions[0].executed is False
    assert tool.count == 0


@pytest.mark.asyncio
async def test_hitl_event_is_emitted_after_response_and_execution(tmp_path) -> None:
    tool = CountingTool("WriteThing", category="write", concurrent=False)
    registry = create_default_registry()
    registry.register(tool)
    agent = Agent(
        _two_turn_client(ToolCallComplete("a1", tool.name, {})),
        registry, "anthropic", work_dir=str(tmp_path),
        permission_checker=_checker(tmp_path),
    )
    decisions = [
        event for event in await _run(agent)
        if isinstance(event, PermissionDecisionEvent)
    ]
    assert len(decisions) == 1
    assert decisions[0].final_effect == "ask"
    assert decisions[0].hitl_required is True
    assert decisions[0].hitl_response == "allow"
    assert decisions[0].executed is True


@pytest.mark.asyncio
async def test_mandatory_safety_ask_is_retained_in_final_event(tmp_path) -> None:
    tool = CountingCommand()
    registry = create_default_registry()
    registry.register(tool)
    agent = Agent(
        _two_turn_client(ToolCallComplete("m1", "Bash", {"command": "rm temp.txt"})),
        registry, "anthropic", work_dir=str(tmp_path),
        permission_checker=_checker(tmp_path, PermissionMode.BYPASS),
    )
    decisions = [
        event for event in await _run(agent)
        if isinstance(event, PermissionDecisionEvent)
    ]
    assert len(decisions) == 1
    assert decisions[0].final_effect == "ask"
    assert decisions[0].mandatory_safety is True
    assert decisions[0].persistable is False
    assert tool.count == 1  # simulated tool only; no shell command is run


@pytest.mark.asyncio
async def test_run_to_completion_noninteractive_ask_emits_denied_outcome(tmp_path) -> None:
    tool = CountingTool("UnattendedWrite", category="write", concurrent=False)
    registry = create_default_registry()
    registry.register(tool)
    agent = Agent(
        _two_turn_client(ToolCallComplete("n1", tool.name, {})),
        registry, "anthropic", work_dir=str(tmp_path),
        permission_checker=_checker(tmp_path),
    )
    emitted: list[dict[str, Any]] = []
    await agent.run_to_completion("run", event_callback=emitted.append)
    decisions = [event for event in emitted if event.get("type") == "permission_decision"]
    assert len(decisions) == 1
    assert decisions[0]["execution_path"] == "noninteractive"
    assert decisions[0]["hitl_response"] == "deny"
    assert decisions[0]["executed"] is False
    assert tool.count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(("compact_result", "success"), [(CompactEvent(123), True), ("failed", False)])
async def test_compression_emits_structured_terminal_event(
    compact_result, success: bool, tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fake_compact(*args, **kwargs):
        nonlocal calls
        calls += 1
        return compact_result if calls == 1 else None

    monkeypatch.setattr("codepacex.agent.auto_compact", fake_compact)
    agent = Agent(
        ScriptedClient([[TextDelta("done"), StreamEnd("end_turn")]]),
        create_default_registry(), "anthropic", work_dir=str(tmp_path),
    )
    events = await _run(agent)
    compression = [event for event in events if isinstance(event, CompressionEvent)]
    assert len(compression) == 1
    assert compression[0].success is success
    assert compression[0].tokens_after is None
    assert compression[0].attachment_count is None
    assert compression[0].error_category == (None if success else "compression_error")

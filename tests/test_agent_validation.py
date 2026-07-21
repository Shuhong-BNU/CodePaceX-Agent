from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from pydantic import BaseModel

from codepacex.agent import Agent, ErrorEvent, LoopComplete
from codepacex.conversation import ConversationManager
from codepacex.tools import ToolRegistry
from codepacex.tools.base import StreamEnd, StreamEvent, TextDelta, Tool, ToolCallComplete, ToolResult
from codepacex.validation import ValidationProfile


class ScriptedClient:
    def __init__(self, responses: list[list[StreamEvent]]) -> None:
        self.responses = responses
        self.calls = 0

    async def stream(self, *args, **kwargs) -> AsyncIterator[StreamEvent]:
        response = self.responses[self.calls]
        self.calls += 1
        for item in response:
            yield item

    def set_max_output_tokens(self, value: int) -> None:
        return None


class Empty(BaseModel):
    pass


class WriteTool(Tool):
    name = "Write"
    description = "write"
    params_model = Empty
    category = "write"

    async def execute(self, params: Empty) -> ToolResult:
        return ToolResult("wrote")


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(WriteTool())
    return registry


@pytest.mark.asyncio
async def test_disabled_agent_keeps_normal_completion() -> None:
    client = ScriptedClient([[TextDelta("done"), StreamEnd("end_turn")]])
    agent = Agent(client, _registry(), "anthropic")
    events = [event async for event in agent.run(ConversationManager())]
    assert any(isinstance(event, LoopComplete) for event in events)
    assert not any(isinstance(event, ErrorEvent) for event in events)


@pytest.mark.asyncio
async def test_enabled_agent_blocks_unreproduced_write() -> None:
    client = ScriptedClient([
        [ToolCallComplete("write", "Write", {}), StreamEnd("tool_use")],
        [TextDelta("draft"), StreamEnd("end_turn")],
        [TextDelta("draft"), StreamEnd("end_turn")],
        [TextDelta("draft"), StreamEnd("end_turn")],
    ])
    agent = Agent(client, _registry(), "anthropic", validation_profile=ValidationProfile.stage_b())
    events = [event async for event in agent.run(ConversationManager())]
    errors = [event.message for event in events if isinstance(event, ErrorEvent)]
    assert any("reproduction" in message for message in errors)


@pytest.mark.asyncio
async def test_run_to_completion_has_the_same_completion_gate() -> None:
    client = ScriptedClient([
        [TextDelta("claimed complete"), StreamEnd("end_turn")],
        [TextDelta("claimed complete"), StreamEnd("end_turn")],
        [TextDelta("claimed complete"), StreamEnd("end_turn")],
    ])
    agent = Agent(client, _registry(), "anthropic", validation_profile=ValidationProfile.stage_b())
    result = await agent.run_to_completion("work")
    assert result.startswith("Validation prevented a completed claim")

from __future__ import annotations

from types import SimpleNamespace

import pytest

from codepacex.agent import Agent
from codepacex.client import (
    AnthropicClient,
    OpenAIClient,
    OpenAICompatClient,
    _canonical_sha256,
    _runtime_manifest_event,
)
from codepacex.config import ProviderConfig
from codepacex.conversation import ConversationManager
from codepacex.serialization import (
    build_anthropic_messages,
    build_chat_completion_messages,
    build_openai_input,
)
from codepacex.tools import create_default_registry
from codepacex.tools.base import (
    RuntimeManifestEvent,
    StreamEnd,
    TextDelta,
    ToolCallComplete,
)


def _config(protocol: str) -> ProviderConfig:
    return ProviderConfig(
        name=f"actual-{protocol}", protocol=protocol,
        base_url="https://provider.example/v1", model=f"model-{protocol}",
        api_key="test-key-not-recorded",
    )


async def _first_event(client, conversation, system, tools):
    stream = client.stream(conversation, system=system, tools=tools)
    event = await anext(stream)
    await stream.aclose()
    return event


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client_class", "protocol"),
    [(AnthropicClient, "anthropic"), (OpenAIClient, "openai"),
     (OpenAICompatClient, "openai-compat")],
)
async def test_clients_emit_hashes_before_sdk_call(
    client_class, protocol: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_sdk = SimpleNamespace()
    monkeypatch.setattr("codepacex.client.AsyncAnthropic", lambda **_kwargs: fake_sdk)
    monkeypatch.setattr("codepacex.client.AsyncOpenAI", lambda **_kwargs: fake_sdk)
    client = client_class(_config(protocol))
    conversation = ConversationManager()
    conversation.add_user_message("payload-message")
    tools = [{"name": "ReadFile", "description": "read", "parameters": {}}]

    event = await _first_event(client, conversation, "payload-system", tools)

    assert isinstance(event, RuntimeManifestEvent)
    assert event.provider == f"actual-{protocol}"
    assert event.protocol == protocol
    assert event.model_id == f"model-{protocol}"
    assert event.request_index is None
    assert "payload" not in repr(event)

    if protocol == "anthropic":
        messages = build_anthropic_messages(conversation.get_messages())
        from codepacex.client import _mark_last_tool_for_cache, _mark_last_user_tail_for_cache
        _mark_last_user_tail_for_cache(messages)
        expected_system = [{
            "type": "text", "text": "payload-system",
            "cache_control": {"type": "ephemeral"},
        }]
        expected_tools = _mark_last_tool_for_cache(tools)
    elif protocol == "openai":
        messages = build_openai_input(conversation.get_messages())
        expected_system = "payload-system"
        expected_tools = tools
    else:
        messages = [
            {"role": "system", "content": "payload-system"},
            *build_chat_completion_messages(conversation.get_messages()),
        ]
        expected_system = messages[0]
        expected_tools = OpenAICompatClient._convert_tools(tools)

    assert event.system_sha256 == _canonical_sha256(expected_system)
    assert event.tools_sha256 == _canonical_sha256(expected_tools)
    assert event.messages_sha256 == _canonical_sha256(messages)


@pytest.mark.asyncio
async def test_agent_assigns_monotonic_request_indexes_across_turns(tmp_path) -> None:
    runtime_one = RuntimeManifestEvent("primary", "openai-compat", "one", "s1", "t1", "m1")
    runtime_two = RuntimeManifestEvent("fallback", "openai-compat", "two", "s2", "t2", "m2")

    class RuntimeClient:
        def __init__(self) -> None:
            self.calls = 0

        async def stream(self, conversation, system="", tools=None):
            self.calls += 1
            if self.calls == 1:
                yield runtime_one
                yield ToolCallComplete("call-1", "ReadFile", {"file_path": "missing.txt"})
                yield StreamEnd("tool_use", input_tokens=1, output_tokens=1)
            else:
                yield runtime_two
                yield TextDelta("done")
                yield StreamEnd("end_turn", input_tokens=1, output_tokens=1)

        def set_max_output_tokens(self, tokens: int) -> None:
            pass

    agent = Agent(RuntimeClient(), create_default_registry(), "openai-compat", work_dir=str(tmp_path))
    conversation = ConversationManager()
    conversation.add_user_message("run")
    events = [event async for event in agent.run(conversation)]
    runtime_events = [event for event in events if isinstance(event, RuntimeManifestEvent)]
    assert [event.request_index for event in runtime_events] == [1, 2]
    assert [event.provider for event in runtime_events] == ["primary", "fallback"]


def test_runtime_hash_components_change_independently() -> None:
    baseline = _runtime_manifest_event(
        provider="p", protocol="openai-compat", model="m",
        system={"value": "s"}, tools=[{"name": "t"}], messages=[{"role": "user"}],
    )
    changes = [
        _runtime_manifest_event(
            provider="p", protocol="openai-compat", model="m",
            system={"value": "changed"}, tools=[{"name": "t"}],
            messages=[{"role": "user"}],
        ),
        _runtime_manifest_event(
            provider="p", protocol="openai-compat", model="m",
            system={"value": "s"}, tools=[{"name": "changed"}],
            messages=[{"role": "user"}],
        ),
        _runtime_manifest_event(
            provider="p", protocol="openai-compat", model="m",
            system={"value": "s"}, tools=[{"name": "t"}],
            messages=[{"role": "assistant"}],
        ),
    ]
    baseline_hashes = (
        baseline.system_sha256, baseline.tools_sha256, baseline.messages_sha256,
    )
    for index, changed in enumerate(changes):
        hashes = (changed.system_sha256, changed.tools_sha256, changed.messages_sha256)
        assert [left != right for left, right in zip(baseline_hashes, hashes)] == [
            position == index for position in range(3)
        ]

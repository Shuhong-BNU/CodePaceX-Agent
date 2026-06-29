"""Tests for configured model fallback chains."""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from codepacex.agent import Agent, RetryEvent, StreamText
from codepacex.client import AuthenticationError, LLMClient, RateLimitError
from codepacex.config import ProviderConfig
from codepacex.conversation import ConversationManager
from codepacex.model_fallback import parse_model_ref
from codepacex.tools import create_default_registry
from codepacex.tools.base import StreamEnd, StreamEvent, TextDelta


class RaisingClient(LLMClient):
    def __init__(self, exc: Exception, *, after_text: bool = False) -> None:
        self.exc = exc
        self.after_text = after_text
        self.histories: list[list[str]] = []

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.histories.append([m.content for m in conversation.history])
        if self.after_text:
            yield TextDelta("partial")
        raise self.exc


class SimpleClient(LLMClient):
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0
        self.histories: list[list[str]] = []

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.calls += 1
        self.histories.append([m.content for m in conversation.history])
        yield TextDelta(self.text)
        yield StreamEnd("end_turn", input_tokens=3, output_tokens=4)


def _provider(name: str, protocol: str, model: str, **overrides) -> ProviderConfig:
    base = dict(
        name=name,
        protocol=protocol,
        base_url=f"https://{name}.example.test",
        model=model,
        models=[model],
        api_key="test-key",
    )
    base.update(overrides)
    return ProviderConfig(**base)


async def _collect(agent: Agent, conv: ConversationManager) -> list:
    events: list = []
    async for event in agent.run(conv):
        events.append(event)
    return events


def test_parse_model_ref_splits_on_first_slash() -> None:
    ref = parse_model_ref("openrouter/openai/gpt-4o-mini")

    assert ref.provider == "openrouter"
    assert ref.model == "openai/gpt-4o-mini"


@pytest.mark.asyncio
async def test_recoverable_error_falls_back_before_stream(monkeypatch) -> None:
    primary = _provider("aliyun", "openai-compat", "qwen-max")
    backup = _provider("aliyun", "openai-compat", "qwen-plus")
    primary_client = RaisingClient(RateLimitError("rate limited"))
    fallback_client = SimpleClient("from fallback")
    created: list[ProviderConfig] = []

    def fake_create_client(provider: ProviderConfig) -> LLMClient:
        created.append(provider)
        return fallback_client

    monkeypatch.setattr("codepacex.agent.create_client", fake_create_client)
    agent = Agent(
        primary_client,
        create_default_registry(),
        primary.protocol,
        active_provider=primary,
        providers=[primary, backup],
        fallback=["aliyun/qwen-max", "aliyun/qwen-plus"],
    )
    conv = ConversationManager()
    conv.add_user_message("hello")

    events = await _collect(agent, conv)

    texts = [e.text for e in events if isinstance(e, StreamText)]
    retries = [e.reason for e in events if isinstance(e, RetryEvent)]
    assert texts == ["from fallback"]
    assert any("rate_limited" in r and "aliyun/qwen-plus" in r for r in retries)
    assert any("本轮已使用备用模型 aliyun/qwen-plus 完成" in r for r in retries)
    assert [p.model for p in created] == ["qwen-plus"]
    assert fallback_client.calls == 1
    assert agent.client is primary_client
    assert agent.protocol == primary.protocol
    assert agent.active_provider is primary
    assistant_messages = [m for m in conv.history if m.role == "assistant"]
    assert len(assistant_messages) == 1
    assert assistant_messages[0].content == "from fallback"


@pytest.mark.asyncio
async def test_fallback_rebuilds_prompt_with_candidate_runtime(monkeypatch) -> None:
    primary = _provider(
        "aliyun",
        "openai-compat",
        "qwen-max",
        context_window=200_000,
    )
    backup = _provider(
        "aliyun",
        "openai-compat",
        "qwen-plus",
        context_window=4_000,
    )
    primary_client = RaisingClient(RateLimitError("rate limited"))
    fallback_client = SimpleClient("from rebuilt fallback")
    compact_calls: list[tuple[int, str]] = []
    budget_calls = 0

    async def fake_auto_compact(
        conversation,
        client,
        context_window,
        session_dir,
        protocol="anthropic",
        **kwargs,
    ):
        compact_calls.append((context_window, protocol))
        return None

    def fake_apply_tool_result_budget(conversation, session_dir, state):
        nonlocal budget_calls
        budget_calls += 1
        prepared = ConversationManager()
        prepared.add_user_message(f"prepared-{budget_calls}")
        return prepared, []

    monkeypatch.setattr("codepacex.agent.auto_compact", fake_auto_compact)
    monkeypatch.setattr(
        "codepacex.agent.apply_tool_result_budget",
        fake_apply_tool_result_budget,
    )
    monkeypatch.setattr(
        "codepacex.agent.create_client",
        lambda provider: fallback_client,
    )
    agent = Agent(
        primary_client,
        create_default_registry(),
        primary.protocol,
        active_provider=primary,
        providers=[primary, backup],
        fallback=["aliyun/qwen-plus"],
    )
    conv = ConversationManager()
    conv.add_user_message("hello")

    events = await _collect(agent, conv)

    assert [e.text for e in events if isinstance(e, StreamText)] == [
        "from rebuilt fallback"
    ]
    assert compact_calls == [
        (200_000, "openai-compat"),
        (4_000, "openai-compat"),
    ]
    assert primary_client.histories == [["prepared-1"]]
    assert fallback_client.histories == [["prepared-2"]]
    assert agent.client is primary_client
    assert agent.active_provider is primary


@pytest.mark.asyncio
async def test_non_recoverable_error_does_not_fallback(monkeypatch) -> None:
    primary = _provider("anthropic", "anthropic", "claude")
    backup = _provider("anthropic-backup", "anthropic", "claude-backup")
    agent = Agent(
        RaisingClient(AuthenticationError("bad key")),
        create_default_registry(),
        primary.protocol,
        active_provider=primary,
        providers=[primary, backup],
        fallback=["anthropic-backup/claude-backup"],
    )
    conv = ConversationManager()
    conv.add_user_message("hello")
    calls = 0

    def fake_create_client(provider: ProviderConfig) -> LLMClient:
        nonlocal calls
        calls += 1
        return SimpleClient("unused")

    monkeypatch.setattr("codepacex.agent.create_client", fake_create_client)
    with pytest.raises(AuthenticationError):
        await _collect(agent, conv)

    assert calls == 0


@pytest.mark.asyncio
async def test_no_fallback_after_stream_has_started(monkeypatch) -> None:
    primary = _provider("aliyun", "openai-compat", "qwen-max")
    backup = _provider("aliyun", "openai-compat", "qwen-plus")
    agent = Agent(
        RaisingClient(RateLimitError("late limit"), after_text=True),
        create_default_registry(),
        primary.protocol,
        active_provider=primary,
        providers=[primary, backup],
        fallback=["aliyun/qwen-plus"],
    )
    conv = ConversationManager()
    conv.add_user_message("hello")
    events: list = []
    calls = 0

    def fake_create_client(provider: ProviderConfig) -> LLMClient:
        nonlocal calls
        calls += 1
        return SimpleClient("unused")

    monkeypatch.setattr("codepacex.agent.create_client", fake_create_client)

    with pytest.raises(RateLimitError):
        async for event in agent.run(conv):
            events.append(event)

    assert [e.text for e in events if isinstance(e, StreamText)] == ["partial"]
    assert calls == 0


@pytest.mark.asyncio
async def test_cross_protocol_fallback_skipped_when_history_exists(monkeypatch) -> None:
    primary = _provider("anthropic", "anthropic", "claude")
    backup = _provider("openai", "openai", "gpt-5")
    agent = Agent(
        RaisingClient(RateLimitError("rate limited")),
        create_default_registry(),
        primary.protocol,
        active_provider=primary,
        providers=[primary, backup],
        fallback=["openai/gpt-5"],
    )
    conv = ConversationManager()
    conv.add_user_message("hello")
    calls = 0

    def fake_create_client(provider: ProviderConfig) -> LLMClient:
        nonlocal calls
        calls += 1
        return SimpleClient("unused")

    monkeypatch.setattr("codepacex.agent.create_client", fake_create_client)

    with pytest.raises(RateLimitError):
        await _collect(agent, conv)

    assert calls == 0


@pytest.mark.asyncio
async def test_cross_protocol_fallback_allowed_when_history_is_empty(monkeypatch) -> None:
    primary = _provider("anthropic", "anthropic", "claude")
    backup = _provider("openai", "openai", "gpt-5")
    fallback_client = SimpleClient("cross protocol fallback")
    created: list[str] = []

    def fake_create_client(provider: ProviderConfig) -> LLMClient:
        created.append(f"{provider.name}/{provider.model}")
        return fallback_client

    monkeypatch.setattr("codepacex.agent.create_client", fake_create_client)
    agent = Agent(
        RaisingClient(RateLimitError("rate limited")),
        create_default_registry(),
        primary.protocol,
        active_provider=primary,
        providers=[primary, backup],
        fallback=["openai/gpt-5"],
    )
    conv = ConversationManager()

    events = await _collect(agent, conv)

    assert created == ["openai/gpt-5"]
    assert [e.text for e in events if isinstance(e, StreamText)] == [
        "cross protocol fallback"
    ]


@pytest.mark.asyncio
async def test_missing_key_fallback_candidate_is_skipped(monkeypatch) -> None:
    monkeypatch.delenv("CODEPACEX_TEST_MISSING_KEY", raising=False)
    primary = _provider("aliyun", "openai-compat", "qwen-max")
    missing = _provider(
        "aliyun",
        "openai-compat",
        "qwen-plus",
        api_key="",
        api_key_env="CODEPACEX_TEST_MISSING_KEY",
    )
    working = _provider("deepseek", "openai-compat", "deepseek-chat")
    fallback_client = SimpleClient("from second fallback")
    created: list[str] = []

    def fake_create_client(provider: ProviderConfig) -> LLMClient:
        created.append(f"{provider.name}/{provider.model}")
        return fallback_client

    monkeypatch.setattr("codepacex.agent.create_client", fake_create_client)
    agent = Agent(
        RaisingClient(RateLimitError("rate limited")),
        create_default_registry(),
        primary.protocol,
        active_provider=primary,
        providers=[primary, missing, working],
        fallback=[
            "aliyun/qwen-plus",
            "deepseek/deepseek-chat",
        ],
    )
    conv = ConversationManager()
    conv.add_user_message("hello")

    events = await _collect(agent, conv)

    retries = [e.reason for e in events if isinstance(e, RetryEvent)]
    assert any("跳过备用模型 aliyun/qwen-plus: missing_key" in r for r in retries)
    assert created == ["deepseek/deepseek-chat"]
    assert [e.text for e in events if isinstance(e, StreamText)] == [
        "from second fallback"
    ]


@pytest.mark.asyncio
async def test_fallback_candidate_skipped_when_prompt_rebuild_fails(monkeypatch) -> None:
    primary = _provider("aliyun", "openai-compat", "qwen-max", context_window=200_000)
    backup = _provider("aliyun", "openai-compat", "qwen-plus", context_window=4_000)
    primary_client = RaisingClient(RateLimitError("rate limited"))
    fallback_client = SimpleClient("should not stream")
    compact_calls = 0

    async def fake_auto_compact(*args, **kwargs):
        nonlocal compact_calls
        compact_calls += 1
        if compact_calls == 2:
            return "摘要生成失败: prompt too long"
        return None

    monkeypatch.setattr("codepacex.agent.auto_compact", fake_auto_compact)
    monkeypatch.setattr(
        "codepacex.agent.create_client",
        lambda provider: fallback_client,
    )
    agent = Agent(
        primary_client,
        create_default_registry(),
        primary.protocol,
        active_provider=primary,
        providers=[primary, backup],
        fallback=["aliyun/qwen-plus"],
    )
    conv = ConversationManager()
    conv.add_user_message("hello")
    events: list = []

    with pytest.raises(RateLimitError):
        async for event in agent.run(conv):
            events.append(event)

    retries = [e.reason for e in events if isinstance(e, RetryEvent)]
    assert any(
        "跳过备用模型 aliyun/qwen-plus" in r and "context_window" in r
        for r in retries
    )
    assert fallback_client.calls == 0
    assert agent.client is primary_client
    assert agent.active_provider is primary

"""验证 TUI 会话内 /model 切换的运行时同步。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codepacex.app import CodePaceXApp
from codepacex.config import ProviderConfig
from codepacex.model_test import ModelTestResult, ModelTestStatus
from codepacex.tools.agent_tool import AgentTool
from codepacex.tools.impl.tool_search import ToolSearchTool


def _providers() -> list[ProviderConfig]:
    return [
        ProviderConfig(
            name="anthropic",
            protocol="anthropic",
            base_url="https://anthropic.example",
            api_key="anthropic-placeholder",
            default_model="claude-sonnet",
            models=["claude-sonnet", "claude-haiku"],
            context_window=200_000,
        ),
        ProviderConfig(
            name="openai",
            protocol="openai",
            base_url="https://openai.example/v1",
            api_key="openai-placeholder",
            default_model="gpt-4o",
            models=["gpt-4o", "gpt-4o-mini"],
            context_window=128_000,
        ),
    ]


def _app_with_runtime() -> tuple[CodePaceXApp, MagicMock, ToolSearchTool, AgentTool]:
    providers = _providers()
    app = CodePaceXApp(providers=providers)
    old_client = object()
    agent = MagicMock()
    agent.client = old_client
    agent.protocol = providers[0].protocol
    agent.context_window = providers[0].context_window
    app.client = old_client
    app.agent = agent
    app._selected_provider = providers[0]
    app.skill_executor = MagicMock()
    app.skill_executor.client = old_client
    app.skill_executor.protocol = providers[0].protocol
    app._streaming = False
    app.run_worker = MagicMock(side_effect=lambda coro, exclusive=False: coro.close())

    tool_search = ToolSearchTool(app.registry, protocol=providers[0].protocol)
    app.registry.register(tool_search)
    agent_tool = AgentTool(
        agent_loader=None,
        task_manager=None,
        trace_manager=None,
        parent_agent=agent,
        provider_config=providers[0],
    )
    app.registry.register(agent_tool)
    return app, agent, tool_search, agent_tool


def test_switch_model_updates_runtime_references() -> None:
    app, agent, tool_search, agent_tool = _app_with_runtime()
    new_client = object()

    with patch("codepacex.app.create_client", return_value=new_client) as mk:
        ok, message = app.switch_model("openai", "gpt-4o-mini")

    assert ok is True
    assert "openai/gpt-4o-mini" in message
    mk.assert_called_once()
    created_provider = mk.call_args.args[0]
    assert created_provider.name == "openai"
    assert created_provider.model == "gpt-4o-mini"
    assert created_provider.models == ["gpt-4o", "gpt-4o-mini"]

    assert app.client is new_client
    assert app._selected_provider is created_provider
    assert agent.client is new_client
    assert agent.protocol == "openai"
    assert agent.context_window == 128_000
    assert app.skill_executor.client is new_client
    assert app.skill_executor.protocol == "openai"
    assert tool_search._protocol == "openai"
    assert agent_tool._provider_config is created_provider


def test_command_context_includes_fallback_config() -> None:
    app, _agent, _tool_search, _agent_tool = _app_with_runtime()
    app.fallback = ["openai/gpt-4o-mini"]

    ctx = app._build_command_context("current")

    assert ctx.config["fallback"] == ["openai/gpt-4o-mini"]


def test_switch_model_rejects_while_streaming() -> None:
    app, agent, _tool_search, _agent_tool = _app_with_runtime()
    old_client = app.client
    app._streaming = True

    with patch("codepacex.app.create_client") as mk:
        ok, message = app.switch_model("openai", "gpt-4o")

    assert ok is False
    assert "正在生成回复" in message
    mk.assert_not_called()
    assert app.client is old_client
    assert agent.client is old_client
    assert app._selected_provider.name == "anthropic"


def test_switch_model_rejects_unknown_provider_or_model() -> None:
    app, _agent, _tool_search, _agent_tool = _app_with_runtime()

    ok, message = app.switch_model("missing", "gpt-4o")
    assert ok is False
    assert "未知 provider" in message

    ok, message = app.switch_model("openai", "not-a-model")
    assert ok is False
    assert "未知模型" in message


def test_switch_model_allows_same_protocol_with_existing_history() -> None:
    app, agent, _tool_search, _agent_tool = _app_with_runtime()
    same_protocol = ProviderConfig(
        name="anthropic-alt",
        protocol="anthropic",
        base_url="https://anthropic-alt.example",
        api_key="anthropic-alt-placeholder",
        default_model="claude-haiku",
        models=["claude-haiku"],
        context_window=200_000,
    )
    app.providers.append(same_protocol)
    app.conversation.add_user_message("hello")
    new_client = object()

    with patch("codepacex.app.create_client", return_value=new_client) as mk:
        ok, message = app.switch_model("anthropic-alt", "claude-haiku")

    assert ok is True
    assert "anthropic-alt/claude-haiku" in message
    mk.assert_called_once()
    assert app.client is new_client
    assert agent.client is new_client
    assert agent.protocol == "anthropic"


def test_switch_model_allows_cross_protocol_with_empty_history() -> None:
    app, agent, _tool_search, _agent_tool = _app_with_runtime()
    new_client = object()

    with patch("codepacex.app.create_client", return_value=new_client) as mk:
        ok, message = app.switch_model("openai", "gpt-4o-mini")

    assert ok is True
    assert "openai/gpt-4o-mini" in message
    mk.assert_called_once()
    assert app.client is new_client
    assert agent.client is new_client
    assert agent.protocol == "openai"


def test_switch_model_rejects_cross_protocol_with_existing_history() -> None:
    app, agent, tool_search, agent_tool = _app_with_runtime()
    old_client = app.client
    old_provider = app._selected_provider
    app.conversation.add_user_message("hello")

    with patch("codepacex.app.create_client") as mk:
        ok, message = app.switch_model("openai", "gpt-4o-mini")

    assert ok is False
    assert "anthropic -> openai" in message
    assert "thinking/reasoning/tool" in message
    assert "/clear" in message
    assert "新会话" in message
    mk.assert_not_called()
    assert app.client is old_client
    assert app._selected_provider is old_provider
    assert agent.client is old_client
    assert agent.protocol == "anthropic"
    assert agent.context_window == 200_000
    assert app.skill_executor.client is old_client
    assert app.skill_executor.protocol == "anthropic"
    assert tool_search._protocol == "anthropic"
    assert agent_tool._provider_config is old_provider


def test_switch_model_failure_keeps_existing_runtime_state() -> None:
    app, agent, tool_search, agent_tool = _app_with_runtime()
    old_client = app.client
    old_provider = app._selected_provider

    with patch("codepacex.app.create_client", side_effect=RuntimeError("boom")):
        ok, message = app.switch_model("openai", "gpt-4o-mini")

    assert ok is False
    assert "切换模型失败" in message
    assert app.client is old_client
    assert app._selected_provider is old_provider
    assert agent.client is old_client
    assert agent.protocol == "anthropic"
    assert agent.context_window == 200_000
    assert app.skill_executor.client is old_client
    assert app.skill_executor.protocol == "anthropic"
    assert tool_search._protocol == "anthropic"
    assert agent_tool._provider_config is old_provider


@pytest.mark.asyncio
async def test_model_test_current_model_does_not_change_runtime_state() -> None:
    app, agent, tool_search, agent_tool = _app_with_runtime()
    old_client = app.client
    old_provider = app._selected_provider
    app.conversation.add_user_message("existing")
    history_before = list(app.conversation.history)
    app.session = MagicMock()
    app.memory_manager = MagicMock()
    result = ModelTestResult(
        provider="anthropic",
        protocol="anthropic",
        model="claude-sonnet",
        base_url="https://anthropic.example",
        key_status="available",
        status=ModelTestStatus.OK,
        reason="completed",
    )

    with patch("codepacex.app.test_provider_model", AsyncMock(return_value=result)) as mk:
        actual = await app.test_model()

    assert actual is result
    mk.assert_awaited_once()
    tested_provider = mk.call_args.args[0]
    assert tested_provider.name == "anthropic"
    assert tested_provider.model == "claude-sonnet"
    assert tested_provider.max_output_tokens == 8
    assert tested_provider.thinking is False
    assert app.client is old_client
    assert app._selected_provider is old_provider
    assert agent.client is old_client
    assert agent.protocol == "anthropic"
    assert agent.context_window == 200_000
    assert app.skill_executor.client is old_client
    assert app.skill_executor.protocol == "anthropic"
    assert tool_search._protocol == "anthropic"
    assert agent_tool._provider_config is old_provider
    assert app.conversation.history == history_before
    app.session.assert_not_called()
    app.memory_manager.assert_not_called()


@pytest.mark.asyncio
async def test_model_test_specified_model_does_not_switch_active_model() -> None:
    app, agent, _tool_search, _agent_tool = _app_with_runtime()
    old_client = app.client
    old_provider = app._selected_provider
    app.conversation.add_user_message("existing")
    history_before = list(app.conversation.history)
    result = ModelTestResult(
        provider="openai",
        protocol="openai",
        model="gpt-4o-mini",
        base_url="https://openai.example/v1",
        key_status="available",
        status=ModelTestStatus.OK,
        reason="completed",
    )

    with patch("codepacex.app.test_provider_model", AsyncMock(return_value=result)) as mk:
        actual = await app.test_model("openai", "gpt-4o-mini")

    assert actual is result
    tested_provider = mk.call_args.args[0]
    assert tested_provider.name == "openai"
    assert tested_provider.model == "gpt-4o-mini"
    assert tested_provider.max_output_tokens == 8
    assert tested_provider.thinking is False
    assert app.client is old_client
    assert app._selected_provider is old_provider
    assert agent.client is old_client
    assert agent.protocol == "anthropic"
    assert agent.context_window == 200_000
    assert app.conversation.history == history_before


@pytest.mark.asyncio
async def test_model_test_rejects_unknown_provider_or_model() -> None:
    app, _agent, _tool_search, _agent_tool = _app_with_runtime()

    with patch("codepacex.app.test_provider_model") as mk:
        ok, message = await app.test_model("missing", "gpt-4o")
    assert ok is False
    assert "未知 provider" in message
    mk.assert_not_called()

    with patch("codepacex.app.test_provider_model") as mk:
        ok, message = await app.test_model("openai", "not-a-model")
    assert ok is False
    assert "未知模型" in message
    mk.assert_not_called()


@pytest.mark.asyncio
async def test_context_window_worker_ignores_stale_provider() -> None:
    app, agent, _tool_search, _agent_tool = _app_with_runtime()
    stale_provider = app._selected_provider
    active_provider = CodePaceXApp._provider_for_model(app.providers[1], "gpt-4o")
    app._selected_provider = active_provider
    agent.context_window = 128_000

    async def fake_resolve(provider: ProviderConfig) -> None:
        provider.set_fetched_context_window(999_000)

    with patch("codepacex.app.resolve_context_window", AsyncMock(side_effect=fake_resolve)):
        await app._resolve_context_window(stale_provider)

    assert agent.context_window == 128_000


@pytest.mark.asyncio
async def test_context_window_worker_updates_active_provider() -> None:
    app, agent, _tool_search, _agent_tool = _app_with_runtime()
    active_provider = CodePaceXApp._provider_for_model(app.providers[0], "claude-sonnet")
    active_provider.context_window = 0
    app._selected_provider = active_provider
    agent.context_window = 200_000

    async def fake_resolve(provider: ProviderConfig) -> None:
        provider.set_fetched_context_window(555_000)

    with patch("codepacex.app.resolve_context_window", AsyncMock(side_effect=fake_resolve)):
        await app._resolve_context_window(active_provider)

    assert agent.context_window == 555_000

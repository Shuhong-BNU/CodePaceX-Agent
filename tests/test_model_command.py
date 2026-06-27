"""验证 TUI 会话内 /model 切换的运行时同步。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codepacex.app import CodePaceXApp
from codepacex.config import ProviderConfig
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

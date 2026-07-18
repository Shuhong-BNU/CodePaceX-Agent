"""Characterize MCP cleanup for the non-interactive CLI lifecycle."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from codepacex.__main__ import _run_prompt
from codepacex.config import (
    AppConfig,
    MCPServerConfig,
    ProviderConfig,
    SandboxAppConfig,
)
from codepacex.experiments import (
    AgentMode,
    CompressionProfile,
    ExperimentProfile,
    PermissionStrategy,
    ToolLoading,
)
from codepacex.permissions import PermissionMode


def _config(*, with_mcp: bool = True) -> AppConfig:
    return AppConfig(
        providers=[ProviderConfig(
            name="test",
            protocol="openai-compat",
            base_url="http://localhost",
            model="test-model",
        )],
        mcp_servers=(
            [MCPServerConfig(name="fixture", command="fixture")]
            if with_mcp else []
        ),
        sandbox=SandboxAppConfig(enabled=False),
    )


def _manager() -> MagicMock:
    manager = MagicMock()
    manager.register_all_tools = AsyncMock(return_value=SimpleNamespace(
        errors=[], servers=[],
    ))
    manager.shutdown = AsyncMock()
    return manager


def _patch_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
    manager_factory: MagicMock,
) -> MagicMock:
    import codepacex.client as client_module
    import codepacex.mcp as mcp_module
    import codepacex.sandbox as sandbox_module
    import codepacex.tools as tools_module

    registry = MagicMock()
    monkeypatch.setattr(client_module, "create_client", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(client_module, "resolve_context_window", AsyncMock())
    monkeypatch.setattr(mcp_module, "MCPManager", manager_factory)
    monkeypatch.setattr(tools_module, "create_default_registry", MagicMock(return_value=registry))
    monkeypatch.setattr(
        sandbox_module,
        "configure_bash_sandbox",
        MagicMock(return_value=(None, None, "disabled")),
    )
    return registry


def _patch_normal_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    import codepacex.agent as agent_module
    import codepacex.agents.loader as loader_module
    import codepacex.agents.task_manager as task_module
    import codepacex.agents.trace as trace_module
    import codepacex.memory.instructions as instructions_module
    import codepacex.teams.manager as team_module
    import codepacex.tools.impl.tool_search as tool_search_module
    import codepacex.tools.install_skill as install_skill_module
    import codepacex.worktree as worktree_module

    class FakeAgent:
        def __init__(self, **_: object) -> None:
            self.agent_id = "test-agent"
            self.notification_fn = None

        async def run(self, _conversation):
            yield agent_module.LoopComplete(total_turns=1)

    team_manager = MagicMock()
    team_manager._teams = {}
    team_manager.drain_lead_mailbox.return_value = []

    monkeypatch.setattr(agent_module, "Agent", FakeAgent)
    monkeypatch.setattr(instructions_module, "load_instructions", MagicMock(return_value=""))
    monkeypatch.setattr(loader_module, "AgentLoader", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(task_module, "TaskManager", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(trace_module, "TraceManager", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(team_module, "TeamManager", MagicMock(return_value=team_manager))
    monkeypatch.setattr(worktree_module, "WorktreeManager", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(tool_search_module, "ToolSearchTool", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(install_skill_module, "InstallSkill", MagicMock(return_value=MagicMock()))


def _single_agent_profile() -> ExperimentProfile:
    return ExperimentProfile(
        tool_loading=ToolLoading.DEFERRED,
        compression_profile=CompressionProfile.SUMMARY_ONLY,
        permission_strategy=PermissionStrategy.DEFAULT,
        agent_mode=AgentMode.SINGLE,
    )


@pytest.mark.asyncio
async def test_normal_prompt_shutdowns_mcp_manager_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager()
    _patch_bootstrap(monkeypatch, MagicMock(return_value=manager))
    _patch_normal_runtime(monkeypatch)

    await _run_prompt(
        _config(), PermissionMode.DEFAULT, None, "hello",
        experiment_profile=_single_agent_profile(),
    )

    manager.shutdown.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_post_initialization_error_still_shutdowns_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import codepacex.memory.instructions as instructions_module

    manager = _manager()
    _patch_bootstrap(monkeypatch, MagicMock(return_value=manager))
    monkeypatch.setattr(
        instructions_module, "load_instructions",
        MagicMock(side_effect=RuntimeError("primary failure")),
    )

    with pytest.raises(RuntimeError, match="primary failure"):
        await _run_prompt(_config(), PermissionMode.DEFAULT, None, "hello")

    manager.shutdown.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_shutdown_error_does_not_replace_primary_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import codepacex.memory.instructions as instructions_module

    class PrimaryError(RuntimeError):
        pass

    manager = _manager()
    manager.shutdown.side_effect = RuntimeError("cleanup failure")
    _patch_bootstrap(monkeypatch, MagicMock(return_value=manager))
    monkeypatch.setattr(
        instructions_module, "load_instructions",
        MagicMock(side_effect=PrimaryError("primary failure")),
    )

    with caplog.at_level("DEBUG", logger="codepacex.__main__"):
        with pytest.raises(PrimaryError, match="primary failure"):
            await _run_prompt(_config(), PermissionMode.DEFAULT, None, "hello")

    manager.shutdown.assert_awaited_once_with()
    assert "Error shutting down MCP manager" in caplog.text


@pytest.mark.asyncio
async def test_partial_mcp_initialization_is_cleaned_up_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager()
    manager.register_all_tools.side_effect = RuntimeError("initialization failure")
    _patch_bootstrap(monkeypatch, MagicMock(return_value=manager))

    with pytest.raises(RuntimeError, match="initialization failure"):
        await _run_prompt(_config(), PermissionMode.DEFAULT, None, "hello")

    manager.shutdown.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_no_mcp_configuration_does_not_create_or_cleanup_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import codepacex.memory.instructions as instructions_module

    manager_factory = MagicMock()
    _patch_bootstrap(monkeypatch, manager_factory)
    monkeypatch.setattr(
        instructions_module, "load_instructions",
        MagicMock(side_effect=RuntimeError("primary failure")),
    )

    with pytest.raises(RuntimeError, match="primary failure"):
        await _run_prompt(
            _config(with_mcp=False), PermissionMode.DEFAULT, None, "hello",
        )

    manager_factory.assert_not_called()


@pytest.mark.asyncio
async def test_failed_manager_construction_has_no_invalid_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager_factory = MagicMock(side_effect=RuntimeError("construction failure"))
    _patch_bootstrap(monkeypatch, manager_factory)

    with pytest.raises(RuntimeError, match="construction failure"):
        await _run_prompt(_config(), PermissionMode.DEFAULT, None, "hello")

    manager_factory.assert_called_once_with()

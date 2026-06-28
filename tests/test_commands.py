"""验证 CodePaceX 的命令解析、注册和处理器。

覆盖正常流程、边界条件与错误路径，确保重构不会破坏既有行为契约。
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from codepacex.commands.parser import complete, parse_command
from codepacex.commands.registry import (
    Command,
    CommandContext,
    CommandRegistry,
    CommandType,
    UIController,
)

# ---------------------------------------------------------------------------
# 测试夹具（Fixtures）
# ---------------------------------------------------------------------------

def _make_command(
    name: str,
    aliases: list[str] | None = None,
    hidden: bool = False,
    handler: Any = None,
    arg_prompt: str = "",
) -> Command:
    return Command(
        name=name,
        aliases=aliases or [],
        description=f"Test {name}",
        type=CommandType.LOCAL,
        handler=handler or AsyncMock(),
        hidden=hidden,
        arg_prompt=arg_prompt,
    )

class MockUI:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.sent_messages: list[str] = []
        self._plan_mode = False

    def add_system_message(self, text: str) -> None:
        self.messages.append(text)

    def send_user_message(self, text: str) -> None:
        self.sent_messages.append(text)

    def set_plan_mode(self, enabled: bool) -> None:
        self._plan_mode = enabled

    def get_token_count(self) -> tuple[int, int]:
        return 10000, 5000

    def refresh_status(self) -> None:
        pass

def _make_context(args: str = "", ui: MockUI | None = None) -> CommandContext:
    return CommandContext(
        args=args,
        agent=None,
        conversation=None,
        session=None,
        session_manager=None,
        memory_manager=None,
        ui=ui or MockUI(),
        config={},
    )

# ---------------------------------------------------------------------------
# parse_command
# ---------------------------------------------------------------------------

class TestParseCommand:
    def test_normal_command(self) -> None:
        name, args, is_cmd = parse_command("/help")
        assert is_cmd is True
        assert name == "help"
        assert args == ""

    def test_command_with_args(self) -> None:
        name, args, is_cmd = parse_command("/compact 保留数据库相关内容")
        assert is_cmd is True
        assert name == "compact"
        assert args == "保留数据库相关内容"

    def test_command_case_insensitive(self) -> None:
        name, args, is_cmd = parse_command("/HELP")
        assert name == "help"
        assert is_cmd is True

    def test_only_slash(self) -> None:
        name, args, is_cmd = parse_command("/")
        assert is_cmd is True
        assert name == ""
        assert args == ""

    def test_not_a_command(self) -> None:
        name, args, is_cmd = parse_command("hello world")
        assert is_cmd is False
        assert name == ""
        assert args == ""

    def test_empty_input(self) -> None:
        name, args, is_cmd = parse_command("")
        assert is_cmd is False

    def test_whitespace_input(self) -> None:
        name, args, is_cmd = parse_command("   ")
        assert is_cmd is False

    def test_command_with_leading_spaces(self) -> None:
        name, args, is_cmd = parse_command("  /help  ")
        assert is_cmd is True
        assert name == "help"

    def test_command_with_multiple_args(self) -> None:
        name, args, is_cmd = parse_command("/session resume abc123")
        assert name == "session"
        assert args == "resume abc123"

# ---------------------------------------------------------------------------
# CommandRegistry
# ---------------------------------------------------------------------------

class TestCommandRegistry:
    def test_register_and_find(self) -> None:
        registry = CommandRegistry()
        cmd = _make_command("help", aliases=["h", "?"])
        registry.register_sync(cmd)
        assert registry.find("help") is cmd

    def test_find_by_alias(self) -> None:
        registry = CommandRegistry()
        cmd = _make_command("help", aliases=["h", "?"])
        registry.register_sync(cmd)
        assert registry.find("h") is cmd
        assert registry.find("?") is cmd

    def test_find_unknown(self) -> None:
        registry = CommandRegistry()
        assert registry.find("nonexistent") is None

    def test_list_commands_excludes_hidden(self) -> None:
        registry = CommandRegistry()
        registry.register_sync(_make_command("visible"))
        registry.register_sync(_make_command("secret", hidden=True))
        cmds = registry.list_commands()
        assert len(cmds) == 1
        assert cmds[0].name == "visible"

    def test_alias_conflict_raises(self) -> None:
        registry = CommandRegistry()
        registry.register_sync(_make_command("help", aliases=["h"]))
        with pytest.raises(ValueError, match="conflicts"):
            registry.register_sync(_make_command("hints", aliases=["h"]))

    def test_name_conflict_raises(self) -> None:
        registry = CommandRegistry()
        registry.register_sync(_make_command("help"))
        with pytest.raises(ValueError, match="conflicts"):
            registry.register_sync(_make_command("help"))

    def test_name_alias_cross_conflict(self) -> None:
        registry = CommandRegistry()
        registry.register_sync(_make_command("help", aliases=["h"]))
        with pytest.raises(ValueError, match="conflicts"):
            registry.register_sync(_make_command("h"))

    @pytest.mark.asyncio
    async def test_async_register(self) -> None:
        registry = CommandRegistry()
        cmd = _make_command("test")
        await registry.register(cmd)
        assert registry.find("test") is cmd

    @pytest.mark.asyncio
    async def test_async_register_conflict(self) -> None:
        registry = CommandRegistry()
        await registry.register(_make_command("test"))
        with pytest.raises(ValueError, match="conflicts"):
            await registry.register(_make_command("test"))

# ---------------------------------------------------------------------------
# complete
# ---------------------------------------------------------------------------

class TestComplete:
    def _build_registry(self) -> CommandRegistry:
        registry = CommandRegistry()
        registry.register_sync(_make_command("help", aliases=["h", "?"]))
        registry.register_sync(_make_command("compact", aliases=["c"]))
        registry.register_sync(_make_command("session"))
        registry.register_sync(_make_command("status", aliases=["s"]))
        registry.register_sync(_make_command("secret", hidden=True))
        return registry

    @staticmethod
    def _values(matches: list[tuple[str, str]]) -> list[str]:
        return [v for _, v in matches]

    def test_empty_prefix(self) -> None:
        registry = self._build_registry()
        matches = complete(registry, "/")
        values = self._values(matches)
        assert "/help" in values
        assert "/compact" in values
        assert "/secret" not in values

    def test_prefix_match(self) -> None:
        registry = self._build_registry()
        matches = complete(registry, "/com")
        assert self._values(matches) == ["/compact"]

    def test_multiple_matches(self) -> None:
        registry = self._build_registry()
        matches = complete(registry, "/s")
        values = self._values(matches)
        assert "/session" in values
        assert "/status" in values

    def test_alias_match(self) -> None:
        registry = self._build_registry()
        matches = complete(registry, "/h")
        values = self._values(matches)
        assert "/help" in values

    def test_no_match(self) -> None:
        registry = self._build_registry()
        matches = complete(registry, "/xyz")
        assert matches == []

    def test_hidden_excluded(self) -> None:
        registry = self._build_registry()
        matches = complete(registry, "/sec")
        assert matches == []

# ---------------------------------------------------------------------------
# Handler 测试
# ---------------------------------------------------------------------------

class TestHelpHandler:
    @pytest.mark.asyncio
    async def test_list_all(self) -> None:
        from codepacex.commands.handlers import register_all_commands
        from codepacex.commands.handlers.help import handle_help

        registry = CommandRegistry()
        register_all_commands(registry)
        ui = MockUI()
        ctx = _make_context(args="", ui=ui)
        ctx.config = {"registry": registry}
        await handle_help(ctx)
        assert len(ui.messages) == 1
        assert "可用命令" in ui.messages[0]
        assert "/help" in ui.messages[0]
        assert "/compact" in ui.messages[0]

    @pytest.mark.asyncio
    async def test_help_specific_command(self) -> None:
        from codepacex.commands.handlers import register_all_commands
        from codepacex.commands.handlers.help import handle_help

        registry = CommandRegistry()
        register_all_commands(registry)
        ui = MockUI()
        ctx = _make_context(args="compact", ui=ui)
        ctx.config = {"registry": registry}
        await handle_help(ctx)
        assert len(ui.messages) == 1
        assert "compact" in ui.messages[0]

    @pytest.mark.asyncio
    async def test_help_unknown_command(self) -> None:
        from codepacex.commands.handlers import register_all_commands
        from codepacex.commands.handlers.help import handle_help

        registry = CommandRegistry()
        register_all_commands(registry)
        ui = MockUI()
        ctx = _make_context(args="nonexistent", ui=ui)
        ctx.config = {"registry": registry}
        await handle_help(ctx)
        assert "未知命令" in ui.messages[0]

class TestPlanDoHandlers:

    @pytest.mark.asyncio
    async def test_plan_switches_mode(self) -> None:
        from codepacex.commands.handlers.plan import handle_plan

        ui = MockUI()
        ctx = _make_context(args="", ui=ui)
        await handle_plan(ctx)
        assert ui._plan_mode is True
        assert "Plan 模式" in ui.messages[0]

    @pytest.mark.asyncio
    async def test_plan_with_args_sends_message(self) -> None:
        from codepacex.commands.handlers.plan import handle_plan

        ui = MockUI()
        ctx = _make_context(args="设计登录模块", ui=ui)
        await handle_plan(ctx)
        assert ui._plan_mode is True
        assert "设计登录模块" in ui.sent_messages

class TestSkillHandler:
    @pytest.mark.asyncio
    async def test_skill_list_no_loader(self) -> None:
        from codepacex.commands.handlers.skill import handle_skill

        ui = MockUI()
        ctx = _make_context(args="list", ui=ui)
        await handle_skill(ctx)
        assert "未初始化" in ui.messages[0]

    @pytest.mark.asyncio
    async def test_skill_list_with_loader(self) -> None:
        from codepacex.commands.handlers.skill import handle_skill

        ui = MockUI()
        ctx = _make_context(args="list", ui=ui)
        loader = MagicMock()
        loader.get_catalog.return_value = [("commit", "分析 git diff")]
        loader.get_source_label.return_value = "builtin"
        ctx.config = {"skill_loader": loader}
        await handle_skill(ctx)
        assert "commit" in ui.messages[0]
        assert "builtin" in ui.messages[0]

    @pytest.mark.asyncio
    async def test_skill_unknown_subcmd(self) -> None:
        from codepacex.commands.handlers.skill import handle_skill

        ui = MockUI()
        ctx = _make_context(args="foobar", ui=ui)
        loader = MagicMock()
        ctx.config = {"skill_loader": loader}
        await handle_skill(ctx)
        assert "未知子命令" in ui.messages[0]

class TestStatusHandler:

    @pytest.mark.asyncio
    async def test_status_output(self) -> None:
        from codepacex.commands.handlers.status import handle_status

        ui = MockUI()
        agent = MagicMock()
        agent.permission_mode = MagicMock()
        agent.permission_mode.value = "default"
        agent.context_window = 200_000
        agent.registry = MagicMock()
        agent.registry.list_tools.return_value = []
        agent.registry.is_enabled.return_value = True
        agent.work_dir = "/test"

        ctx = _make_context(args="", ui=ui)
        ctx.agent = agent
        ctx.memory_manager = MagicMock()
        ctx.memory_manager.load.return_value = ""

        await handle_status(ctx)
        assert "CodePaceX 状态" in ui.messages[0]
        assert "default" in ui.messages[0]


class TestModelHandler:
    @staticmethod
    def _providers():
        from codepacex.config import ProviderConfig

        return [
            ProviderConfig(
                name="aliyun",
                protocol="openai-compat",
                base_url="https://dashscope.example/v1",
                api_key="dashscope-placeholder",
                default_model="qwen-plus",
                models=["qwen-plus", "qwen-turbo"],
            ),
            ProviderConfig(
                name="legacy",
                protocol="anthropic",
                base_url="https://anthropic.example",
                model="claude-sonnet",
                api_key="legacy-placeholder",
            ),
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("args", ["", "current"])
    async def test_model_current_output(self, args: str) -> None:
        from codepacex.commands.handlers.model import handle_model

        ui = MockUI()
        providers = self._providers()
        ctx = _make_context(args=args, ui=ui)
        ctx.config = {
            "providers": providers,
            "get_current_provider": lambda: providers[0],
        }

        await handle_model(ctx)

        assert "当前模型" in ui.messages[0]
        assert "Provider: aliyun" in ui.messages[0]
        assert "Protocol: openai-compat" in ui.messages[0]
        assert "Model: qwen-plus" in ui.messages[0]
        assert "Base URL: https://dashscope.example/v1" in ui.messages[0]
        assert "dashscope-placeholder" not in ui.messages[0]

    @pytest.mark.asyncio
    async def test_model_list_outputs_providers_and_models(self) -> None:
        from codepacex.commands.handlers.model import handle_model

        ui = MockUI()
        providers = self._providers()
        ctx = _make_context(args="list", ui=ui)
        ctx.config = {
            "providers": providers,
            "get_current_provider": lambda: providers[0],
        }

        await handle_model(ctx)

        text = ui.messages[0]
        assert "* aliyun" in text
        assert "qwen-plus [current]" in text
        assert "qwen-turbo" in text
        assert "legacy" in text
        assert "claude-sonnet" in text
        assert "key: available" in text
        providers[1].api_key = ""
        providers[1].api_key_env = "MISSING_TEST_KEY"
        ui.messages.clear()

        await handle_model(ctx)

        text = ui.messages[0]
        assert "key: missing" in text
        assert "dashscope-placeholder" not in text
        assert "legacy-placeholder" not in text

    @pytest.mark.asyncio
    async def test_model_use_switches_valid_target(self) -> None:
        from codepacex.commands.handlers.model import handle_model

        ui = MockUI()
        providers = self._providers()
        switch = AsyncMock(return_value=(True, "switched"))
        ctx = _make_context(args="use aliyun/qwen-turbo", ui=ui)
        ctx.config = {
            "providers": providers,
            "get_current_provider": lambda: providers[0],
            "switch_model": switch,
        }

        await handle_model(ctx)

        switch.assert_awaited_once_with("aliyun", "qwen-turbo")
        assert ui.messages == ["switched"]

    @pytest.mark.asyncio
    async def test_model_use_supports_model_names_with_slash(self) -> None:
        from codepacex.commands.handlers.model import handle_model
        from codepacex.config import ProviderConfig

        ui = MockUI()
        provider = ProviderConfig(
            name="openrouter",
            protocol="openai-compat",
            base_url="https://openrouter.example/v1",
            api_key="placeholder",
            default_model="openai/gpt-4o-mini",
            models=["openai/gpt-4o-mini"],
        )
        switch = AsyncMock(return_value=(True, "switched"))
        ctx = _make_context(args="use openrouter/openai/gpt-4o-mini", ui=ui)
        ctx.config = {
            "providers": [provider],
            "get_current_provider": lambda: provider,
            "switch_model": switch,
        }

        await handle_model(ctx)

        switch.assert_awaited_once_with("openrouter", "openai/gpt-4o-mini")

    @pytest.mark.asyncio
    async def test_model_use_unknown_provider(self) -> None:
        from codepacex.commands.handlers.model import handle_model

        ui = MockUI()
        providers = self._providers()
        ctx = _make_context(args="use missing/qwen-plus", ui=ui)
        ctx.config = {"providers": providers}

        await handle_model(ctx)

        assert "未知 provider" in ui.messages[0]

    @pytest.mark.asyncio
    async def test_model_use_unknown_model(self) -> None:
        from codepacex.commands.handlers.model import handle_model

        ui = MockUI()
        providers = self._providers()
        ctx = _make_context(args="use aliyun/not-a-model", ui=ui)
        ctx.config = {"providers": providers}

        await handle_model(ctx)

        assert "未知模型" in ui.messages[0]
        assert "qwen-plus" in ui.messages[0]

    @pytest.mark.asyncio
    async def test_model_use_bad_args(self) -> None:
        from codepacex.commands.handlers.model import handle_model

        ui = MockUI()
        ctx = _make_context(args="use aliyun", ui=ui)
        ctx.config = {"providers": self._providers()}

        await handle_model(ctx)

        assert "用法" in ui.messages[0]

    @pytest.mark.asyncio
    async def test_model_test_defaults_to_current_model(self) -> None:
        from codepacex.commands.handlers.model import handle_model
        from codepacex.model_test import ModelTestResult, ModelTestStatus

        ui = MockUI()
        providers = self._providers()
        result = ModelTestResult(
            provider="aliyun",
            protocol="openai-compat",
            model="qwen-plus",
            base_url="https://dashscope.example/v1",
            key_status="available",
            status=ModelTestStatus.OK,
            reason="completed",
            latency_ms=12,
        )
        test_model = AsyncMock(return_value=result)
        ctx = _make_context(args="test", ui=ui)
        ctx.config = {
            "providers": providers,
            "get_current_provider": lambda: providers[0],
            "test_model": test_model,
        }

        await handle_model(ctx)

        test_model.assert_awaited_once_with(None, None)
        text = ui.messages[0]
        assert "模型测试" in text
        assert "Provider: aliyun" in text
        assert "Protocol: openai-compat" in text
        assert "Model: qwen-plus" in text
        assert "Key: available" in text
        assert "Result: ok" in text
        assert "Latency: 12 ms" in text
        assert "dashscope-placeholder" not in text

    @pytest.mark.asyncio
    async def test_model_test_uses_specified_target(self) -> None:
        from codepacex.commands.handlers.model import handle_model
        from codepacex.model_test import ModelTestResult, ModelTestStatus

        ui = MockUI()
        providers = self._providers()
        result = ModelTestResult(
            provider="aliyun",
            protocol="openai-compat",
            model="qwen-turbo",
            base_url="https://dashscope.example/v1",
            key_status="available",
            status=ModelTestStatus.OK,
            reason="completed",
        )
        test_model = AsyncMock(return_value=result)
        ctx = _make_context(args="test aliyun/qwen-turbo", ui=ui)
        ctx.config = {"providers": providers, "test_model": test_model}

        await handle_model(ctx)

        test_model.assert_awaited_once_with("aliyun", "qwen-turbo")
        assert "Model: qwen-turbo" in ui.messages[0]

    @pytest.mark.asyncio
    async def test_model_test_supports_model_names_with_slash(self) -> None:
        from codepacex.commands.handlers.model import handle_model
        from codepacex.config import ProviderConfig
        from codepacex.model_test import ModelTestResult, ModelTestStatus

        ui = MockUI()
        provider = ProviderConfig(
            name="openrouter",
            protocol="openai-compat",
            base_url="https://openrouter.example/v1",
            api_key="placeholder",
            default_model="openai/gpt-4o-mini",
            models=["openai/gpt-4o-mini"],
        )
        result = ModelTestResult(
            provider="openrouter",
            protocol="openai-compat",
            model="openai/gpt-4o-mini",
            base_url="https://openrouter.example/v1",
            key_status="available",
            status=ModelTestStatus.OK,
            reason="completed",
        )
        test_model = AsyncMock(return_value=result)
        ctx = _make_context(args="test openrouter/openai/gpt-4o-mini", ui=ui)
        ctx.config = {"providers": [provider], "test_model": test_model}

        await handle_model(ctx)

        test_model.assert_awaited_once_with("openrouter", "openai/gpt-4o-mini")

    @pytest.mark.asyncio
    async def test_model_test_unknown_provider_or_model(self) -> None:
        from codepacex.commands.handlers.model import handle_model

        ui = MockUI()
        providers = self._providers()
        ctx = _make_context(args="test missing/qwen-plus", ui=ui)
        ctx.config = {"providers": providers, "test_model": AsyncMock()}

        await handle_model(ctx)

        assert "未知 provider" in ui.messages[0]

        ui.messages.clear()
        ctx.args = "test aliyun/not-a-model"
        await handle_model(ctx)

        assert "未知模型" in ui.messages[0]
        assert "qwen-plus" in ui.messages[0]

    @pytest.mark.asyncio
    async def test_model_test_outputs_failure_without_leaking_key(self) -> None:
        from codepacex.commands.handlers.model import handle_model
        from codepacex.model_test import ModelTestResult, ModelTestStatus

        ui = MockUI()
        providers = self._providers()
        result = ModelTestResult(
            provider="aliyun",
            protocol="openai-compat",
            model="qwen-plus",
            base_url="https://dashscope.example/v1",
            key_status="missing",
            status=ModelTestStatus.MISSING_KEY,
            reason="API key is missing.",
            suggestion="请检查 DASHSCOPE_API_KEY 是否已设置。",
        )
        ctx = _make_context(args="test", ui=ui)
        ctx.config = {
            "providers": providers,
            "get_current_provider": lambda: providers[0],
            "test_model": AsyncMock(return_value=result),
        }

        await handle_model(ctx)

        text = ui.messages[0]
        assert "Result: failed" in text
        assert "Reason: API key is missing." in text
        assert "Suggestion:" in text
        assert "dashscope-placeholder" not in text

class TestSessionHandler:
    @pytest.mark.asyncio
    async def test_session_no_manager(self) -> None:
        from codepacex.commands.handlers.session import handle_session

        ui = MockUI()
        ctx = _make_context(args="", ui=ui)
        ctx.session_manager = None
        await handle_session(ctx)
        assert "未初始化" in ui.messages[0]

    @pytest.mark.asyncio
    async def test_session_list_empty(self) -> None:
        from codepacex.commands.handlers.session import handle_session

        ui = MockUI()
        sm = MagicMock()
        sm.list.return_value = []
        ctx = _make_context(args="list", ui=ui)
        ctx.session_manager = sm
        await handle_session(ctx)
        assert "没有已保存的会话" in ui.messages[0]

    @pytest.mark.asyncio
    async def test_session_unknown_sub(self) -> None:
        from codepacex.commands.handlers.session import handle_session

        ui = MockUI()
        ctx = _make_context(args="foobar", ui=ui)
        ctx.session_manager = MagicMock()
        await handle_session(ctx)
        assert "用法" in ui.messages[0]

class TestMemoryHandler:
    @pytest.mark.asyncio
    async def test_memory_display(self) -> None:
        from codepacex.commands.handlers.memory import handle_memory

        ui = MockUI()
        mm = MagicMock()
        mm.get_display_text.return_value = "记忆内容"
        ctx = _make_context(args="", ui=ui)
        ctx.memory_manager = mm
        await handle_memory(ctx)
        assert "记忆内容" in ui.messages[0]

    @pytest.mark.asyncio
    async def test_memory_clear(self) -> None:
        from codepacex.commands.handlers.memory import handle_memory

        ui = MockUI()
        mm = MagicMock()
        ctx = _make_context(args="clear", ui=ui)
        ctx.memory_manager = mm
        await handle_memory(ctx)
        mm.clear.assert_called_once()
        assert "清空" in ui.messages[0]

    @pytest.mark.asyncio
    async def test_memory_no_manager(self) -> None:
        from codepacex.commands.handlers.memory import handle_memory

        ui = MockUI()
        ctx = _make_context(args="", ui=ui)
        ctx.memory_manager = None
        await handle_memory(ctx)
        assert "未初始化" in ui.messages[0]

# ---------------------------------------------------------------------------
# 集成测试：register_all_commands
# ---------------------------------------------------------------------------

class TestRegisterAllCommands:
    def test_all_commands_registered(self) -> None:
        from codepacex.commands.handlers import register_all_commands

        registry = CommandRegistry()
        register_all_commands(registry)
        cmds = registry.list_commands()
        names = {c.name for c in cmds}
        expected = {
            "help", "compact", "clear", "plan",
            "session", "model", "mcp", "memory", "permission",
            "rewind", "status", "skill",
        }
        assert names == expected

    def test_no_alias_conflicts(self) -> None:
        from codepacex.commands.handlers import register_all_commands

        registry = CommandRegistry()
        register_all_commands(registry)

    def test_aliases_work(self) -> None:
        from codepacex.commands.handlers import register_all_commands

        registry = CommandRegistry()
        register_all_commands(registry)
        assert registry.find("h") is not None
        assert registry.find("h").name == "help"
        assert registry.find("c").name == "compact"
        assert registry.find("p").name == "plan"
        assert registry.find("s").name == "status"
        assert registry.find("?").name == "help"

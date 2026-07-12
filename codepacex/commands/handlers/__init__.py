"""组织 codepacex.commands.handlers 包的公开接口与子模块。"""

from __future__ import annotations

from codepacex.commands.handlers.clear import CLEAR_COMMAND
from codepacex.commands.handlers.compact import COMPACT_COMMAND
from codepacex.commands.handlers.help import HELP_COMMAND
from codepacex.commands.handlers.mcp import MCP_COMMAND
from codepacex.commands.handlers.memory import MEMORY_COMMAND
from codepacex.commands.handlers.model import MODEL_COMMAND
from codepacex.commands.handlers.permission import PERMISSION_COMMAND
from codepacex.commands.handlers.plan import PLAN_COMMAND
from codepacex.commands.handlers.session import SESSION_COMMAND
from codepacex.commands.handlers.skill import SKILL_COMMAND
from codepacex.commands.handlers.sandbox import SANDBOX_COMMAND
from codepacex.commands.handlers.rewind import REWIND_COMMAND
from codepacex.commands.handlers.status import STATUS_COMMAND
from codepacex.commands.registry import CommandRegistry


ALL_COMMANDS = [
    HELP_COMMAND,
    COMPACT_COMMAND,
    CLEAR_COMMAND,
    PLAN_COMMAND,
    SESSION_COMMAND,
    MODEL_COMMAND,
    MCP_COMMAND,
    MEMORY_COMMAND,
    PERMISSION_COMMAND,
    REWIND_COMMAND,
    STATUS_COMMAND,
    SKILL_COMMAND,
    SANDBOX_COMMAND,
]


# 核心实现
def register_all_commands(registry: CommandRegistry) -> None:
    for cmd in ALL_COMMANDS:
        registry.register_sync(cmd)

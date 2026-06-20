"""提供 CodePaceX 的权限模式与工具类别决策矩阵能力。

主要包含权限模式、危险命令检测、路径沙箱和分级规则。该模块由所有工具执行前的权限检查调用，并维护默认拒绝、人工确认和工作区边界。
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from codepacex.tools.base import ToolCategory


DecisionEffect = Literal["allow", "deny", "ask"]


# 核心实现
class PermissionMode(str, Enum):
    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    PLAN = "plan"
    BYPASS = "bypassPermissions"


_MODE_MATRIX: dict[PermissionMode, dict[ToolCategory, DecisionEffect]] = {
    PermissionMode.DEFAULT: {"read": "allow", "write": "ask", "command": "ask"},
    PermissionMode.ACCEPT_EDITS: {"read": "allow", "write": "allow", "command": "ask"},
    PermissionMode.PLAN: {"read": "allow", "write": "ask", "command": "ask"},
    PermissionMode.BYPASS: {"read": "allow", "write": "allow", "command": "allow"},
}


def mode_decide(mode: PermissionMode, category: ToolCategory) -> DecisionEffect:
    return _MODE_MATRIX[mode][category]

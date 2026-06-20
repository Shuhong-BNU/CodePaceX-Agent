"""提供 CodePaceX 的 enter worktree能力。

主要包含工具参数模型、执行逻辑和结果封装。该模块由工具注册表与 Agent 调度器调用，并维护输入校验、权限分类和副作用范围。
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, Field

from codepacex.tools.base import Tool, ToolResult
from codepacex.worktree.slug import validate_slug

if TYPE_CHECKING:
    from codepacex.worktree.manager import WorktreeManager


# 核心实现
class EnterWorktreeParams(BaseModel):
    name: Optional[str] = Field(
        default=None,
        description=(
            'Optional name for the worktree. Each "/"-separated segment may '
            "contain only letters, digits, dots, underscores, and dashes; "
            "max 64 chars total. A random name is generated if not provided."
        ),
    )


class EnterWorktreeTool(Tool):
    name = "EnterWorktree"
    description = (
        "Creates an isolated worktree (via git) and switches the session into it"
    )
    params_model = EnterWorktreeParams
    category = "command"
    should_defer = True


    def __init__(self, worktree_manager: WorktreeManager) -> None:
        self._manager = worktree_manager


    async def execute(self, params: EnterWorktreeParams) -> ToolResult:
        if self._manager.get_current_session() is not None:
            return ToolResult(
                output="Already in a worktree session", is_error=True
            )

        slug = params.name or f"wt-{secrets.token_hex(4)}"

        err = validate_slug(slug)
        if err:
            return ToolResult(output=f"Invalid worktree name: {err}", is_error=True)

        try:
            wt = await self._manager.create(slug)
            session = await self._manager.enter(slug)
        except Exception as e:
            return ToolResult(
                output=f"Error creating worktree: {e}", is_error=True
            )

        branch_info = f" on branch {wt.branch}" if wt.branch else ""
        return ToolResult(
            output=(
                f"Created worktree at {session.worktree_path}{branch_info}. "
                "The session is now working in the worktree. "
                "Use ExitWorktree to leave mid-session, or exit the session to be prompted."
            )
        )

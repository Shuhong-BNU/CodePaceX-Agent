"""提供 CodePaceX 的子 Agent worktree 命名与上下文提示能力。

主要包含 Git worktree 创建、进入、清理和变更检测。该模块由子 Agent 隔离执行与团队协作调用，并维护分支状态、未提交改动和清理安全。
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codepacex.worktree.manager import WorktreeManager


WORKTREE_NOTICE_TEMPLATE = """\
[WORKTREE CONTEXT]
You have inherited the parent agent's conversation context.
You are currently working in an isolated Git Worktree: {wt_path}
The parent agent's working directory is: {parent_cwd}

IMPORTANT:
- File paths mentioned in the parent conversation refer to the PARENT directory.
- You must translate them to your local worktree path before reading or editing.
- Always re-read files before editing — your copy may differ from the parent's version.
[/WORKTREE CONTEXT]
"""


# 核心实现
def generate_worktree_name() -> str:
    """生成由 agent 前缀和随机十六进制片段组成的 worktree 名称。

    Go 使用 "agent-a" + hex.EncodeToString(b)[:7]，其中 b 是 4 字节随机数。
    4 字节 = 8 位十六进制，截取前 7 位。匹配  cleanup 正则 ^agent-a[0-9a-f]{7}$。
    """
    return f"agent-a{secrets.token_hex(4)[:7]}"


def build_worktree_notice(parent_cwd: str, wt_path: str) -> str:
    return WORKTREE_NOTICE_TEMPLATE.format(
        parent_cwd=parent_cwd,
        wt_path=wt_path,
    )

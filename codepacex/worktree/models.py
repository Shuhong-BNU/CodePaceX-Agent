"""提供 CodePaceX 的 worktree 与活动会话数据模型能力。

主要包含 Git worktree 创建、进入、清理和变更检测。该模块由子 Agent 隔离执行与团队协作调用，并维护分支状态、未提交改动和清理安全。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


# 核心实现
@dataclass
class Worktree:
    name: str
    path: str
    branch: str
    based_on: str
    head_commit: str
    created: datetime = field(default_factory=datetime.now)


@dataclass
class WorktreeSession:
    original_cwd: str
    worktree_path: str
    worktree_name: str
    original_branch: str
    original_head_commit: str
    session_id: str = ""
    hook_based: bool = False


"""提供 CodePaceX 的 tmux、iTerm2 与进程内后端选择能力。

主要包含团队成员、邮箱、共享任务和多后端协作。该模块由协调 Agent 与 worktree 管理器调用，并维护成员身份、消息投递和并发隔离。
"""

from __future__ import annotations

import os
import shutil

from codepacex.teams.models import BackendType


# 核心实现
class BackendDetectionError(Exception):
    pass


def _in_tmux_session() -> bool:
    return bool(os.environ.get("TMUX"))


def _in_iterm2() -> bool:
    return os.environ.get("TERM_PROGRAM") == "iTerm.app"


def _it2_available() -> bool:
    return shutil.which("it2") is not None


def _tmux_installed() -> bool:
    return shutil.which("tmux") is not None


def detect_backend(
    teammate_mode: str = "",
    is_interactive: bool = True,
) -> BackendType:
    """Default to in-process for real-time progress tracking."""
    return BackendType.IN_PROCESS


def detect_pane_backend(
    teammate_mode: str = "",
    is_interactive: bool = True,
) -> BackendType:
    """根据配置和当前终端能力选择 teammate pane 后端。

    优先级：tmux（已在 session 内）> iTerm2 > tmux（已安装）> in-process 兜底。
    当没有任何外部终端复用器可用时，静默回退到 in-process，而不是抛异常。
    """
    if teammate_mode == "in-process" or not is_interactive:
        return BackendType.IN_PROCESS

    if _in_tmux_session():
        return BackendType.TMUX

    if _in_iterm2() and _it2_available():
        return BackendType.ITERM2

    if _tmux_installed():
        return BackendType.TMUX

    # 没有可用的 pane 后端时静默回退到 in-process，
    # 而不是抛出异常中断 team 创建流程
    return BackendType.IN_PROCESS

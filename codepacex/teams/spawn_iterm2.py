"""提供 CodePaceX 的 iTerm2 pane teammate 启动能力。

主要包含团队成员、邮箱、共享任务和多后端协作。该模块由协调 Agent 与 worktree 管理器调用，并维护成员身份、消息投递和并发隔离。
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

log = logging.getLogger(__name__)


# 核心实现
@dataclass
class ITermPaneInfo:
    session_id: str


class ITermSpawnError(Exception):
    pass


def _run_it2(*args: str) -> str:
    result = subprocess.run(
        ["it2", *args],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise ITermSpawnError(f"it2 {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def spawn_iterm2_teammate(
    team_name: str,
    teammate_name: str,
    worktree_path: str,
    prompt: str,
    agent_type: str = "",
    model: str = "",
    mailbox_dir: str = "",
) -> ITermPaneInfo:
    from codepacex.teams.spawn_tmux import build_cli_command

    cli_cmd = build_cli_command(
        team_name=team_name,
        teammate_name=teammate_name,
        worktree_path=worktree_path,
        prompt=prompt,
        agent_type=agent_type,
        model=model,
        mailbox_dir=mailbox_dir,
    )

    try:
        session_id = _run_it2("split-pane", "--command", f"/bin/zsh -c '{cli_cmd}'")
    except ITermSpawnError as e:
        raise ITermSpawnError(f"Failed to spawn iTerm2 pane for {teammate_name}: {e}") from e

    log.info("Spawned iTerm2 teammate %s in session %s", teammate_name, session_id)
    return ITermPaneInfo(session_id=session_id)

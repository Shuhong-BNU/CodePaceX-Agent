"""组织 codepacex.teams 包的公开接口与子模块。"""

from codepacex.teams.mailbox import Mailbox, MailboxMessage, create_message
from codepacex.teams.models import (
    AgentTeam,
    BackendType,
    TeammateInfo,
    resolve_team_dir,
    unique_team_name,
)
from codepacex.teams.progress import TeammateProgress, ToolActivity
from codepacex.teams.registry import AgentNameRegistry
from codepacex.teams.shared_task import SharedTask, SharedTaskStore


__all__ = [
    "AgentTeam",
    "AgentNameRegistry",
    "BackendType",
    "Mailbox",
    "MailboxMessage",
    "SharedTask",
    "SharedTaskStore",
    "TeammateInfo",
    "TeammateProgress",
    "ToolActivity",
    "create_message",
    "resolve_team_dir",
    "unique_team_name",
]


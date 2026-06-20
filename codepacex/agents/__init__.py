"""组织 codepacex.agents 包的公开接口与子模块。"""

from codepacex.agents.parser import AgentDef, AgentParseError, parse_agent_file
from codepacex.agents.loader import AgentLoader
from codepacex.agents.tool_filter import resolve_agent_tools
from codepacex.agents.fork import build_forked_messages, ForkError
from codepacex.agents.trace import TraceManager, TraceNode
from codepacex.agents.task_manager import TaskManager, BackgroundTask
from codepacex.agents.notification import format_task_notification, inject_task_notifications


__all__ = [
    "AgentDef",
    "AgentParseError",
    "parse_agent_file",
    "AgentLoader",
    "resolve_agent_tools",
    "build_forked_messages",
    "ForkError",
    "TraceManager",
    "TraceNode",
    "TaskManager",
    "BackgroundTask",
    "format_task_notification",
    "inject_task_notifications",
]


"""提供 CodePaceX 的 Hook 事件名称和触发点定义能力。

主要包含 Hook 配置、条件匹配、事件分发和动作执行。该模块由 Agent 生命周期与工具调用链调用，并维护外部动作的超时和失败隔离。
"""

from __future__ import annotations

from enum import StrEnum


# 核心实现
class LifecycleEvent(StrEnum):
    # 会话（Session）级别
    SESSION_START = "session_start"
    SESSION_END = "session_end"


    # 轮次（Turn）级别
    TURN_START = "turn_start"
    TURN_END = "turn_end"


    # 工具（Tool）级别
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"

    # 消息（Message）级别
    PRE_SEND = "pre_send"
    POST_RECEIVE = "post_receive"

    # 系统（System）级别
    STARTUP = "startup"
    SHUTDOWN = "shutdown"
    ERROR = "error"
    COMPACT = "compact"
    PERMISSION_REQUEST = "permission_request"
    FILE_CHANGE = "file_change"
    COMMAND_EXECUTE = "command_execute"


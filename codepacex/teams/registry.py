"""提供 CodePaceX 的团队内 Agent 名称唯一性管理能力。

主要包含团队成员、邮箱、共享任务和多后端协作。该模块由协调 Agent 与 worktree 管理器调用，并维护成员身份、消息投递和并发隔离。
"""

from __future__ import annotations

import threading


# 核心实现
class AgentNameRegistry:
    _instance: AgentNameRegistry | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._names: dict[str, str] = {}  # name -> agent_id


    @classmethod
    def instance(cls) -> AgentNameRegistry:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance


    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            cls._instance = None


    def register(self, name: str, agent_id: str) -> None:
        self._names[name] = agent_id

    def resolve(self, name_or_id: str) -> str | None:
        if name_or_id in self._names:
            return self._names[name_or_id]
        if name_or_id in self._names.values():
            return name_or_id
        return None

    def unregister(self, name: str) -> None:
        self._names.pop(name, None)


    def list_all(self) -> dict[str, str]:
        return dict(self._names)

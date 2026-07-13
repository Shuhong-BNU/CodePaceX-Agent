"""提供 CodePaceX 的父子 Agent 调用链追踪能力。

主要包含子 Agent 定义、上下文分叉、后台任务和工具过滤。该模块由主 Agent 与任务管理器调用，并维护父子上下文和工具权限边界。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field


# 核心实现
@dataclass
class TraceNode:
    agent_id: str
    parent_id: str | None
    trace_id: str
    agent_type: str
    input_tokens: int = 0
    output_tokens: int = 0
    tool_call_count: int = 0
    request_count: int = 0
    request_usages: list[tuple[int, int]] = field(default_factory=list)
    start_time: float = field(default_factory=time.monotonic)
    end_time: float | None = None
    status: str = "running"


class TraceManager:
    def __init__(self) -> None:
        self._nodes: dict[str, TraceNode] = {}
        self._peak_parallel_by_parent: dict[str, int] = {}


    def create(
        self,
        agent_type: str,
        parent_id: str | None = None,
        trace_id: str | None = None,
    ) -> TraceNode:
        agent_id = uuid.uuid4().hex[:12]
        if trace_id is None:
            trace_id = uuid.uuid4().hex[:12]

        node = TraceNode(
            agent_id=agent_id,
            parent_id=parent_id,
            trace_id=trace_id,
            agent_type=agent_type,
        )
        self._nodes[agent_id] = node
        if parent_id is not None:
            running = sum(
                item.parent_id == parent_id and item.end_time is None
                for item in self._nodes.values()
            )
            self._peak_parallel_by_parent[parent_id] = max(
                self._peak_parallel_by_parent.get(parent_id, 0), running,
            )
        return node

    def update(self, agent_id: str, **kwargs: object) -> None:
        node = self._nodes.get(agent_id)
        if node is None:
            return
        for key, value in kwargs.items():
            if hasattr(node, key):
                setattr(node, key, value)


    def complete(self, agent_id: str, status: str = "completed") -> None:
        node = self._nodes.get(agent_id)
        if node is None:
            return
        node.end_time = time.monotonic()
        node.status = status


    def get(self, agent_id: str) -> TraceNode | None:
        return self._nodes.get(agent_id)

    def get_tree(self, trace_id: str) -> list[TraceNode]:
        return [n for n in self._nodes.values() if n.trace_id == trace_id]


    def remove(self, agent_id: str) -> None:
        self._nodes.pop(agent_id, None)

    def complete_all_running(self, parent_id: str) -> None:
        for node in self._nodes.values():
            if node.parent_id == parent_id and node.status == "running":
                node.status = "completed"
                node.end_time = time.monotonic()

    def get_total_tokens(self, trace_id: str) -> tuple[int, int]:
        total_in = 0
        total_out = 0
        for node in self._nodes.values():
            if node.trace_id == trace_id:
                total_in += node.input_tokens
                total_out += node.output_tokens
        return total_in, total_out

    def benchmark_summary(self, parent_id: str) -> dict[str, object]:
        """Return aggregate child telemetry without prompts or model content."""
        children = [node for node in self._nodes.values() if node.parent_id == parent_id]
        return {
            "child_count": len(children),
            "completed_child_count": sum(node.status == "completed" for node in children),
            "failed_child_count": sum(node.status == "failed" for node in children),
            "child_input_tokens": sum(node.input_tokens for node in children),
            "child_output_tokens": sum(node.output_tokens for node in children),
            "child_request_count": sum(node.request_count for node in children),
            "child_request_usages": [
                {"input_tokens": input_tokens, "output_tokens": output_tokens}
                for node in children
                for input_tokens, output_tokens in node.request_usages
            ],
            "child_tool_call_count": sum(node.tool_call_count for node in children),
            "maximum_parallel_children": self._peak_parallel_by_parent.get(parent_id, 0),
        }

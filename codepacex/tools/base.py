"""提供 CodePaceX 的工具抽象、结果模型与流式事件协议能力。

主要包含工具参数模型、执行逻辑和结果封装。该模块由工具注册表与 Agent 调度器调用，并维护输入校验、权限分类和副作用范围。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".tox", ".mypy_cache"}

MAX_OUTPUT_CHARS = 10000

ToolCategory = Literal["read", "write", "command"]
PathAccessMode = Literal["read", "write"]
PathScope = Literal["default", "workspace"]


@dataclass(frozen=True)
class PathAccess:
    """Declare a path argument that must be checked before execution."""

    field: str
    mode: PathAccessMode
    scope: PathScope = "default"


# 核心实现
@dataclass
class ToolResult:
    output: str
    is_error: bool = False


class Tool(ABC):
    name: str
    description: str
    params_model: type[BaseModel]
    category: ToolCategory = "read"
    is_concurrency_safe: bool = False
    is_system_tool: bool = False
    should_defer: bool = False
    path_accesses: tuple[PathAccess, ...] = ()
    requires_explicit_authorization: bool = False

    @property
    def is_read_only(self) -> bool:
        return self.category == "read"


    def get_schema(self) -> dict[str, Any]:
        schema = self.params_model.model_json_schema()
        schema.pop("title", None)
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": schema,
        }

    @abstractmethod
    async def execute(self, params: BaseModel) -> ToolResult: ...


# --- 流式事件 ---


@dataclass
class TextDelta:
    text: str


@dataclass
class ToolCallStart:
    tool_name: str
    tool_id: str


@dataclass
class ToolCallDelta:
    text: str


@dataclass
class ToolCallComplete:
    tool_id: str
    tool_name: str
    arguments: dict[str, Any]


@dataclass
class ThinkingDelta:
    text: str


@dataclass
class ThinkingComplete:
    thinking: str
    signature: str


@dataclass
class RuntimeManifestEvent:
    """Hashes of the final, provider-specific SDK payload without raw content."""

    provider: str
    protocol: str
    model_id: str
    system_sha256: str
    tools_sha256: str
    messages_sha256: str
    request_index: int | None = None
    experiment_profile_hash: str | None = None
    runtime_contract_hash: str | None = None
    combined_runtime_hash: str | None = None


@dataclass
class StreamEnd:
    stop_reason: str
    input_tokens: int = 0
    output_tokens: int = 0
    # API 返回的 prompt cache 用量。Anthropic 把缓存前缀 token 分为
    # "read"（cache 命中，按 10% 计费）和 "creation"（cache 写入）。
    # input_tokens 已排除这两部分，因此实际 prompt 大小 =
    # input + cache_read + cache_creation。OpenAI 系列只暴露
    # cache_read（通过 *_tokens_details.cached_tokens），没有 creation
    # 计数，所以 cache_creation 在那边始终为 0。
    cache_read: int = 0
    cache_creation: int = 0
    # Exact provider usage payload when the SDK exposes one.  Consumers must
    # preserve absent fields rather than inferring token accounting.
    provider_usage: dict[str, Any] | None = None


StreamEvent = (
    TextDelta | ThinkingDelta | ThinkingComplete | ToolCallStart | ToolCallDelta
    | ToolCallComplete | RuntimeManifestEvent | StreamEnd
)

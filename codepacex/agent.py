"""提供 CodePaceX 的 Agent 执行循环、工具调度与生命周期管理能力。

主要包含核心数据结构与执行流程。该模块由 CodePaceX 运行时调用，并维护状态一致性和异常传播。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from pydantic import ValidationError

from codepacex.client import LLMClient, create_client
from codepacex.config import ProviderConfig
from codepacex.context import (
    CompactBoundary,
    CompactCircuitBreaker,
    CompactEvent,
    ContentReplacementRecord,
    ContentReplacementState,
    RecoveryState,
    append_replacement_records,
    apply_tool_result_budget,
    auto_compact,
    create_replacement_state,
    ensure_session_dir,
    load_replacement_records,
    reconstruct_replacement_state,
)
from codepacex.conversation import ConversationManager, ToolResultBlock, ToolUseBlock
from codepacex.conversation import ThinkingBlock as ConvThinkingBlock
from codepacex.experiments import (
    CompressionProfile,
    ExperimentProfile,
    combined_runtime_hash,
)
from codepacex.memory.auto_memory import MemoryManager
from codepacex.model_fallback import (
    classify_fallback_error,
    iter_fallback_decisions,
    model_ref_for_provider,
)
from codepacex.permissions import (
    Decision,
    PermissionChecker,
    PermissionMode,
)
from codepacex.hooks import HookContext, HookEngine, ToolRejectedError
from codepacex.hooks.engine import HookNotification
from codepacex.prompts import build_environment_context, build_plan_mode_reminder, build_system_prompt
from codepacex.tools import ToolRegistry
from codepacex.tools.base import (
    MAX_OUTPUT_CHARS,
    RuntimeManifestEvent,
    StreamEnd,
    StreamEvent,
    TextDelta,
    ThinkingComplete,
    ThinkingDelta,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
    ToolResult,
)

log = logging.getLogger(__name__)

MEMORY_EXTRACTION_INTERVAL = 5
MAX_TOKENS_CEILING = 64000
MAX_OUTPUT_TOKENS_RECOVERIES = 3


# ---------------------------------------------------------------------------
# AgentEvent 事件类型
# ---------------------------------------------------------------------------

@dataclass
class StreamText:
    text: str


@dataclass
class ThinkingText:
    text: str


@dataclass
class RetryEvent:
    reason: str
    wait: float = 0.0


@dataclass
class ToolUseEvent:
    tool_name: str
    tool_id: str
    arguments: dict[str, Any]


@dataclass
class ToolResultEvent:
    tool_id: str
    tool_name: str
    output: str
    is_error: bool
    elapsed: float


@dataclass
class TurnComplete:
    turn: int


@dataclass
class LoopComplete:
    total_turns: int


@dataclass
class UsageEvent:
    input_tokens: int
    output_tokens: int
    request_input_tokens: int | None = None
    request_output_tokens: int | None = None
    provider_usage: dict[str, Any] | None = None
    provider: str | None = None
    model_id: str | None = None
    request_index: int | None = None


@dataclass
class ErrorEvent:
    message: str


@dataclass
class CompactNotification:
    before_tokens: int
    message: str
    # 结构化 boundary（摘要 + 原文保留尾部），UI/session 层用它持久化 compact_boundary 记录。
    # 失败路径下为 None。
    boundary: "CompactBoundary | None" = None


@dataclass
class CompressionEvent:
    trigger: str
    success: bool
    tokens_before: int
    tokens_after: int | None = None
    attachment_count: int | None = None
    error_category: str | None = None


@dataclass
class PermissionDecisionEvent:
    tool_use_id: str
    tool_name: str
    final_effect: str
    mandatory_safety: bool
    hook_effect: str | None
    hitl_required: bool
    hitl_response: str | None
    persistable: bool
    executed: bool
    execution_path: str


@dataclass
class HookEvent:
    hook_id: str
    event: str
    output: str
    success: bool


class PermissionResponse(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ALLOW_ALWAYS = "allow_always"


@dataclass
class PermissionRequest:
    tool_name: str
    description: str
    future: asyncio.Future[PermissionResponse]
    tool_use_id: str = ""


AgentEvent = (
    StreamText
    | ThinkingText
    | RetryEvent
    | ToolUseEvent
    | ToolResultEvent
    | TurnComplete
    | LoopComplete
    | UsageEvent
    | ErrorEvent
    | PermissionRequest
    | CompactNotification
    | HookEvent
    | RuntimeManifestEvent
    | PermissionDecisionEvent
    | CompressionEvent
)


# ---------------------------------------------------------------------------
# LLM 响应收集器
# ---------------------------------------------------------------------------

@dataclass
class ThinkingBlock:
    thinking: str
    signature: str


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCallComplete] = field(default_factory=list)
    thinking_blocks: list[ThinkingBlock] = field(default_factory=list)
    provider_usage: dict[str, Any] | None = None
    stop_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    request_index: int | None = None


class StreamCollector:
    def __init__(
        self,
        runtime_event_indexer: (
            Callable[[RuntimeManifestEvent], RuntimeManifestEvent] | None
        ) = None,
    ) -> None:
        self.response = LLMResponse()
        self._runtime_event_indexer = runtime_event_indexer

    async def consume(
        self, stream: AsyncIterator[StreamEvent]
    ) -> AsyncIterator[AgentEvent]:
        async for event in stream:
            if isinstance(event, TextDelta):
                self.response.text += event.text
                yield StreamText(text=event.text)
            elif isinstance(event, ThinkingDelta):
                yield ThinkingText(text=event.text)
            elif isinstance(event, ThinkingComplete):
                self.response.thinking_blocks.append(
                    ThinkingBlock(thinking=event.thinking, signature=event.signature)
                )
            elif isinstance(event, RuntimeManifestEvent):
                if self._runtime_event_indexer is not None:
                    event = self._runtime_event_indexer(event)
                self.response.request_index = event.request_index
                yield event
            elif isinstance(event, ToolCallStart):
                pass
            elif isinstance(event, ToolCallDelta):
                pass
            elif isinstance(event, ToolCallComplete):
                self.response.tool_calls.append(event)
                yield ToolUseEvent(
                    tool_name=event.tool_name,
                    tool_id=event.tool_id,
                    arguments=event.arguments,
                )
            elif isinstance(event, StreamEnd):
                self.response.stop_reason = event.stop_reason
                self.response.input_tokens = event.input_tokens
                self.response.output_tokens = event.output_tokens
                self.response.cache_read = event.cache_read
                self.response.cache_creation = event.cache_creation
                self.response.provider_usage = event.provider_usage


# ---------------------------------------------------------------------------
# tool 批量执行
# ---------------------------------------------------------------------------

@dataclass
class ToolBatch:
    concurrent: bool
    calls: list[ToolCallComplete]


def partition_tool_calls(
    tool_calls: list[ToolCallComplete],
    registry: ToolRegistry,
) -> list[ToolBatch]:
    batches: list[ToolBatch] = []
    for tc in tool_calls:
        tool = registry.get(tc.tool_name)
        safe = (
            tool is not None
            and tool.is_read_only
            and tool.is_concurrency_safe
            and registry.is_enabled(tc.tool_name)
        )

        if safe and batches and batches[-1].concurrent:
            batches[-1].calls.append(tc)
        else:
            batches.append(ToolBatch(concurrent=safe, calls=[tc]))
    return batches


# ---------------------------------------------------------------------------
# streaming 执行器 — 在 LLM streaming 期间启动 tool 执行
# ---------------------------------------------------------------------------

@dataclass
class _ToolDecision:
    decision: Decision
    mandatory_safety: bool = False
    hook_effect: str | None = None


@dataclass
class ToolExecutionOutcome:
    tool_id: str
    tool_name: str
    result: ToolResult
    elapsed: float
    is_unknown: bool
    final_effect: str
    mandatory_safety: bool
    hook_effect: str | None
    hitl_required: bool
    hitl_response: str | None
    persistable: bool
    executed: bool
    execution_path: str


@dataclass
class _ModelRuntime:
    client: LLMClient
    protocol: str
    context_window: int
    provider: ProviderConfig | None = None
    used_fallback: bool = False


class StreamingExecutor:
    def __init__(self) -> None:
        self._tasks: list[
            tuple[int, asyncio.Task[ToolExecutionOutcome], ToolExecutionOutcome | None]
        ] = []
        self._order = 0

    def submit(
        self,
        coro: Any,
        cancelled_outcome: ToolExecutionOutcome | None = None,
    ) -> None:
        task = asyncio.create_task(coro)
        self._tasks.append((self._order, task, cancelled_outcome))
        self._order += 1

    async def collect_results(self) -> list[ToolExecutionOutcome]:
        if not self._tasks:
            return []
        ordered = sorted(self._tasks, key=lambda item: item[0])
        tasks = [task for _, task, _ in ordered]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        out: list[ToolExecutionOutcome] = []
        for r, (_, _, fallback) in zip(results, ordered):
            if isinstance(r, BaseException):
                out.append(fallback or ToolExecutionOutcome(
                    tool_id="",
                    tool_name="",
                    result=ToolResult(output=f"Tool execution error: {r}", is_error=True),
                    elapsed=0.0,
                    is_unknown=False,
                    final_effect="allow",
                    mandatory_safety=False,
                    hook_effect=None,
                    hitl_required=False,
                    hitl_response=None,
                    persistable=False,
                    executed=False,
                    execution_path="streaming",
                ))
            else:
                out.append(r)
        return out

    async def cancel_and_reap(self) -> list[ToolExecutionOutcome]:
        ordered = sorted(self._tasks, key=lambda item: item[0])
        tasks = [task for _, task, _ in ordered]
        try:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
            else:
                results = []
            return [
                result if isinstance(result, ToolExecutionOutcome) else fallback
                for result, (_, _, fallback) in zip(results, ordered)
                if isinstance(result, ToolExecutionOutcome) or fallback is not None
            ]
        finally:
            self._tasks.clear()


# ---------------------------------------------------------------------------
# Agent 主循环
# ---------------------------------------------------------------------------

class Agent:
    def __init__(
        self,
        client: LLMClient,
        registry: ToolRegistry,
        protocol: str,
        work_dir: str = ".",
        max_iterations: int = 50,
        permission_checker: PermissionChecker | None = None,
        context_window: int = 200_000,
        instructions_content: str = "",
        memory_manager: MemoryManager | None = None,
        hook_engine: HookEngine | None = None,
        active_provider: ProviderConfig | None = None,
        providers: list[ProviderConfig] | None = None,
        fallback: list[str] | None = None,
        experiment_profile: ExperimentProfile | None = None,
    ) -> None:
        self.client = client
        self.registry = registry
        self.protocol = protocol
        self.active_provider = active_provider
        self.providers = providers or []
        self.fallback = fallback or []
        self.experiment_profile = experiment_profile
        self.work_dir = work_dir
        self.max_iterations = max_iterations
        self.permission_checker = permission_checker
        self.permission_mode: PermissionMode = (
            permission_checker.mode if permission_checker else PermissionMode.DEFAULT
        )
        self.context_window = context_window
        self.session_dir = ensure_session_dir(work_dir)
        self.compact_breaker = CompactCircuitBreaker()
        self.replacement_state: ContentReplacementState = create_replacement_state()
        # 保存重建工作上下文所需的快照，在 Layer 2 压缩对话后使用：
        # 最近的文件读取和 skill 调用。每次 ReadFile / skill 调用时记录，
        # auto_compact 触发阈值时消费。
        self.recovery_state: RecoveryState = RecoveryState()
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self._runtime_request_index = 0
        self.instructions_content = instructions_content
        self.memory_manager = memory_manager
        self.hook_engine = hook_engine
        self._loop_count = 0
        # 记忆提取合并策略（inProgress + pendingContext）：
        # _extracting: 标记是否有提取正在进行
        # _pending_extraction: 提取期间又触发了新请求，标记需要尾随提取
        self._extracting = False
        self._pending_extraction = False
        self._consolidator: Any | None = None
        if memory_manager is not None:
            from codepacex.memory.consolidation import MemoryConsolidator

            self._consolidator = MemoryConsolidator(work_dir)
        self.session_id: str = ""
        self.active_skills: dict[str, str] = {}
        self._skill_catalog: str = ""
        self._agent_catalog: str = ""
        self._agent_catalog_list: list[tuple[str, str]] = []
        self.agent_id: str = uuid.uuid4().hex[:12]
        self.parent_id: str | None = None
        self.trace_id: str | None = None
        self.coordinator_mode: bool = False
        self.team_name: str = ""
        self._team_manager: Any = None
        self.notification_fn: Callable[[], list[str]] | None = None
        self.file_history: Any = None

        # 非阻塞 memory recall：prefetch task 与主 LLM 调用并行，工具执行后注入
        self.memory_recall_task: Any | None = None
        self._memory_recall_consumed: bool = False

    @property
    def _transcript_path(self) -> str:
        if self.session_id:
            return str(Path(self.work_dir) / ".codepacex" / "sessions" / f"{self.session_id}.jsonl")
        return ""

    def _next_runtime_request_index(self) -> int:
        self._runtime_request_index += 1
        return self._runtime_request_index

    def _index_runtime_event(self, event: RuntimeManifestEvent) -> RuntimeManifestEvent:
        event.request_index = self._next_runtime_request_index()
        if self.experiment_profile is not None:
            profile_hash = self.experiment_profile.profile_hash()
            event.experiment_profile_hash = profile_hash
            event.runtime_contract_hash = self.experiment_profile.runtime_contract_hash()
            event.combined_runtime_hash = combined_runtime_hash(
                profile_hash=profile_hash,
                system_sha256=event.system_sha256,
                tools_sha256=event.tools_sha256,
            )
        return event

    def _compression_recovery_inputs(
        self, protocol: str,
    ) -> tuple[RecoveryState | None, list[dict[str, Any]] | None]:
        if (
            self.experiment_profile is not None
            and self.experiment_profile.compression_profile
            is CompressionProfile.SUMMARY_ONLY
        ):
            return None, None
        return self.recovery_state, self.registry.get_all_schemas(protocol)

    @staticmethod
    def _runtime_event_payload(event: RuntimeManifestEvent) -> dict[str, Any]:
        return {
            "type": "runtime_manifest",
            "request_index": event.request_index,
            "provider": event.provider,
            "protocol": event.protocol,
            "model_id": event.model_id,
            "system_sha256": event.system_sha256,
            "tools_sha256": event.tools_sha256,
            "messages_sha256": event.messages_sha256,
            "tools_bytes": event.tools_bytes,
            "experiment_profile_hash": event.experiment_profile_hash,
            "runtime_contract_hash": event.runtime_contract_hash,
            "combined_runtime_hash": event.combined_runtime_hash,
        }

    @staticmethod
    def _compression_event_payload(event: CompressionEvent) -> dict[str, Any]:
        return {
            "type": "compression",
            "trigger": event.trigger,
            "success": event.success,
            "tokens_before": event.tokens_before,
            "tokens_after": event.tokens_after,
            "attachment_count": event.attachment_count,
            "error_category": event.error_category,
        }

    def _usage_from_stream_end(
        self, runtime_event: RuntimeManifestEvent, stream_end: StreamEnd,
    ) -> UsageEvent:
        """Account for a completed SDK request without inventing provider fields."""
        self.total_input_tokens += stream_end.input_tokens
        self.total_output_tokens += stream_end.output_tokens
        return UsageEvent(
            input_tokens=self.total_input_tokens,
            output_tokens=self.total_output_tokens,
            request_input_tokens=stream_end.input_tokens,
            request_output_tokens=stream_end.output_tokens,
            provider_usage=stream_end.provider_usage,
            provider=runtime_event.provider,
            model_id=runtime_event.model_id,
            request_index=runtime_event.request_index,
        )

    @staticmethod
    def _usage_event_payload(event: UsageEvent) -> dict[str, Any]:
        return {
            "type": "usage",
            "input_tokens": event.input_tokens,
            "output_tokens": event.output_tokens,
            "request_input_tokens": event.request_input_tokens,
            "request_output_tokens": event.request_output_tokens,
            "provider_usage": event.provider_usage,
            "provider": event.provider,
            "model_id": event.model_id,
            "request_index": event.request_index,
        }

    @property
    def plan_mode(self) -> bool:
        return self.permission_mode == PermissionMode.PLAN

    _plan_path_cache: Path | None = None

    def _get_plan_path(self) -> Path:
        if self._plan_path_cache is not None:
            return self._plan_path_cache
        import random
        import datetime
        _ADJECTIVES = ["bold", "bright", "calm", "cool", "deep", "fair", "fast", "fine",
                       "glad", "keen", "kind", "lean", "mild", "neat", "pure", "safe",
                       "slim", "soft", "tall", "warm", "wise", "grand", "swift", "vivid"]
        _NOUNS = ["sketch", "draft", "spark", "bloom", "trail", "ridge", "creek", "grove",
                  "cliff", "cloud", "field", "forge", "frost", "haven", "pearl", "stone",
                  "storm", "river", "tower", "delta", "flame", "orbit", "pulse", "shore"]
        plans_dir = Path(self.work_dir) / ".codepacex" / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%m%d-%H%M")
        slug = f"{random.choice(_ADJECTIVES)}-{random.choice(_NOUNS)}-{ts}"
        self._plan_path_cache = plans_dir / f"{slug}.md"
        return self._plan_path_cache

    def set_permission_mode(self, mode: PermissionMode) -> None:
        self.permission_mode = mode
        if self.permission_checker:
            self.permission_checker.mode = mode

    def activate_skill(self, name: str, prompt_body: str) -> None:
        self.active_skills[name] = prompt_body

    def clear_active_skills(self) -> None:
        self.active_skills.clear()

    def set_skill_catalog(self, catalog: str) -> None:
        self._skill_catalog = catalog


    def set_agent_catalog(self, catalog: str, catalog_list: list[tuple[str, str]] | None = None) -> None:
        self._agent_catalog = catalog
        if catalog_list is not None:
            self._agent_catalog_list = catalog_list

    def _build_hook_context(self, event: str, **kwargs: str | dict) -> HookContext:
        return HookContext(
            event_name=event,
            tool_name=str(kwargs.get("tool_name", "")),
            tool_args=kwargs.get("tool_args", {}),
            file_path=str(kwargs.get("file_path", "")),
            message=str(kwargs.get("message", "")),
            error=str(kwargs.get("error", "")),
        )

    def _infer_file_path(self, args: dict) -> str:
        return str(args.get("file_path", args.get("path", "")))

    def _drain_hook_events(self) -> list[HookEvent]:
        if not self.hook_engine:
            return []
        return [
            HookEvent(
                hook_id=n.hook_id,
                event=n.event,
                output=n.output,
                success=n.success,
            )
            for n in self.hook_engine.drain_notifications()
        ]

    async def _prepare_runtime_request(
        self,
        conversation: ConversationManager,
        runtime: _ModelRuntime,
        env_context: str,
        *,
        apply_budget: bool = True,
    ) -> tuple[ConversationManager | None, list[dict[str, Any]], list[AgentEvent], str]:
        events: list[AgentEvent] = []
        runtime_events: list[RuntimeManifestEvent] = []
        compression_usage: list[UsageEvent] = []

        def on_runtime(event: RuntimeManifestEvent) -> None:
            runtime_events.append(self._index_runtime_event(event))

        def on_usage(event: RuntimeManifestEvent, stream_end: StreamEnd) -> None:
            compression_usage.append(self._usage_from_stream_end(event, stream_end))
        tokens_before = conversation.current_tokens()
        recovery, recovery_tools = self._compression_recovery_inputs(runtime.protocol)
        compact_result = await auto_compact(
            conversation,
            runtime.client,
            runtime.context_window,
            self.session_dir,
            protocol=runtime.protocol,
            breaker=self.compact_breaker,
            recovery=recovery,
            tool_schemas=recovery_tools,
            transcript_path=self._transcript_path,
            runtime_event_sink=on_runtime,
            usage_event_sink=on_usage,
        )
        events.extend(runtime_events)
        events.extend(compression_usage)
        if isinstance(compact_result, CompactEvent):
            events.append(CompressionEvent(
                trigger="automatic", success=True,
                tokens_before=compact_result.before_tokens,
            ))
            events.append(
                CompactNotification(
                    before_tokens=compact_result.before_tokens,
                    message=f"上下文已压缩（压缩前 {compact_result.before_tokens:,} tokens）",
                    boundary=compact_result.boundary,
                )
            )
            conversation.inject_environment(env_context)
            mem = self.memory_manager.load() if self.memory_manager else ""
            conversation.inject_long_term_memory(self.instructions_content, mem)
        elif isinstance(compact_result, str):
            events.append(CompressionEvent(
                trigger="automatic", success=False, tokens_before=tokens_before,
                error_category="compression_error",
            ))
            return None, [], events, compact_result

        tools = self.registry.get_all_schemas(runtime.protocol)
        if not apply_budget:
            return None, tools, events, ""

        api_conv, new_records = apply_tool_result_budget(
            conversation, self.session_dir, self.replacement_state
        )
        if new_records:
            append_replacement_records(self.session_dir, new_records)
        return api_conv, tools, events, ""

    async def run(self, conversation: ConversationManager) -> AsyncIterator[AgentEvent]:
        self._runtime_request_index = 0
        active_executor: list[StreamingExecutor | None] = [None]
        try:
            async for event in self._run(conversation, active_executor):
                yield event
        finally:
            if active_executor[0] is not None:
                await active_executor[0].cancel_and_reap()

    async def _run(
        self,
        conversation: ConversationManager,
        active_executor: list[StreamingExecutor | None],
    ) -> AsyncIterator[AgentEvent]:
        self._current_conversation = conversation
        fallback_history_exists = bool(conversation.history)
        env_context = build_environment_context(
            self.work_dir, self.active_skills, self._skill_catalog, self._agent_catalog
        )
        conversation.inject_environment(env_context)

        memory_content = self.memory_manager.load() if self.memory_manager else ""
        conversation.inject_long_term_memory(self.instructions_content, memory_content)

        if self.hook_engine:
            ctx = self._build_hook_context("session_start")
            await self.hook_engine.run_hooks("session_start", ctx)
            for he in self._drain_hook_events():
                yield he

        iteration = 0
        consecutive_unknown = 0
        max_tokens_escalated = False
        output_recoveries = 0
        turn_runtime = _ModelRuntime(
            client=self.client,
            protocol=self.protocol,
            context_window=self.context_window,
            provider=self.active_provider,
        )
        fallback_success_announced = False
        tried_fallback_refs: set[str] = set()

        while True:
            iteration += 1

            if iteration > self.max_iterations:
                yield ErrorEvent(
                    message=f"Agent reached maximum iterations ({self.max_iterations})"
                )
                break

            if self.hook_engine:
                ctx = self._build_hook_context("turn_start")
                await self.hook_engine.run_hooks("turn_start", ctx)
                for he in self._drain_hook_events():
                    yield he

            self._consume_mailbox(conversation)
            if self.notification_fn:
                for note in self.notification_fn():
                    conversation.add_system_reminder(note)

            # Layer 2: 接近 context window 上限时自动 compact（操作原始对话）
            _api_conv, tools, prep_events, prep_error = await self._prepare_runtime_request(
                conversation, turn_runtime, env_context, apply_budget=False
            )
            for event in prep_events:
                yield event
            if prep_error:
                yield ErrorEvent(message=prep_error)

            if self.hook_engine:
                ctx = self._build_hook_context("pre_send")
                await self.hook_engine.run_hooks("pre_send", ctx)
                for he in self._drain_hook_events():
                    yield he

            hook_prompts = (
                self.hook_engine.get_prompt_messages() if self.hook_engine else None
            )
            system = build_system_prompt(
                hook_prompts=hook_prompts,
                coordinator_mode=self.coordinator_mode,
                agent_catalog=self._agent_catalog_list or None,
            )

            if self.plan_mode:
                plan_path = str(self._get_plan_path())
                if self.permission_checker:
                    self.permission_checker.plan_file_path = plan_path
                plan_exists = self._get_plan_path().exists()
                plan_reminder = build_plan_mode_reminder(
                    plan_path, plan_exists, iteration
                )
                conversation.add_system_reminder(plan_reminder)

            if self.hook_engine:
                for note in self.hook_engine.drain_notifications():
                    conversation.add_system_reminder(
                        f"Hook [{note.hook_id}] {note.event}: {note.output}"
                    )

            deferred_names = self.registry.get_deferred_tool_names()
            if deferred_names:
                conversation.add_system_reminder(
                    "The following deferred tools are available via ToolSearch. "
                    "Their schemas are NOT loaded - use ToolSearch with "
                    'query "select:<name>[,<name>...]" to load tool schemas before calling them:\n'
                    + "\n".join(deferred_names)
                )

            # pre_send、plan reminders、hook 通知和 deferred tool reminders 可能刚刚
            # 写入对话，因此主请求在发送前再重建一次 api_conv。fallback runtime
            # 也会在切换后走同一重建路径，避免复用 primary runtime 的 prompt。
            tools = self.registry.get_all_schemas(turn_runtime.protocol)
            api_conv, _new_records = apply_tool_result_budget(
                conversation, self.session_dir, self.replacement_state
            )
            if _new_records:
                append_replacement_records(self.session_dir, _new_records)

            while True:
                collector = StreamCollector(self._index_runtime_event)
                streaming_executor = StreamingExecutor()
                active_executor[0] = streaming_executor
                streamed_count = 0
                streaming_decisions: dict[str, _ToolDecision] = {}
                streaming_prefix_open = True
                stream_started = False
                try:
                    llm_stream = turn_runtime.client.stream(
                        api_conv, system=system, tools=tools
                    )
                    async for event in collector.consume(llm_stream):
                        if isinstance(event, ToolUseEvent) and streaming_prefix_open:
                            tc = collector.response.tool_calls[-1]
                            tool = self.registry.get(tc.tool_name)
                            # Pre Hooks are assessed before dispatch. Hook events
                            # and post Hooks are emitted after ordered result
                            # collection, preserving audit order without disabling
                            # the read-only streaming fast path.
                            if (
                                tool is not None
                                and tool.is_read_only
                                and tool.is_concurrency_safe
                                and self.registry.is_enabled(tc.tool_name)
                            ):
                                decision = await self._assess_tool(tc)
                                streaming_decisions[tc.tool_id] = decision
                                if decision.decision.effect in {"allow", "deny"}:
                                    streaming_executor.submit(
                                        self._execute_single_tool_direct(
                                            tc, decision, execution_path="streaming",
                                        ),
                                        self._cancelled_tool_outcome(
                                            tc, decision, "streaming",
                                        ),
                                    )
                                    streamed_count += 1
                                else:
                                    streaming_prefix_open = False
                            else:
                                streaming_prefix_open = False
                        if not isinstance(event, RuntimeManifestEvent):
                            stream_started = True
                        yield event
                    break
                except asyncio.CancelledError:
                    await streaming_executor.cancel_and_reap()
                    active_executor[0] = None
                    raise
                except Exception as e:
                    cancelled_outcomes = await streaming_executor.cancel_and_reap()
                    active_executor[0] = None
                    for outcome in cancelled_outcomes:
                        yield self._permission_decision_event(outcome)
                    if stream_started:
                        raise

                    failed_ref = (
                        model_ref_for_provider(turn_runtime.provider).label
                        if turn_runtime.provider is not None
                        else "current model"
                    )
                    if turn_runtime.provider is not None:
                        tried_fallback_refs.add(failed_ref)

                    fallback_error = classify_fallback_error(
                        e, turn_runtime.provider
                    )
                    if not fallback_error.recoverable or not self.fallback:
                        raise

                    next_runtime: _ModelRuntime | None = None
                    decisions = iter_fallback_decisions(
                        self.fallback,
                        self.providers,
                        turn_runtime.provider,
                        has_history=fallback_history_exists,
                        tried=tried_fallback_refs,
                    )
                    for decision in decisions:
                        if decision.skipped:
                            tried_fallback_refs.add(decision.ref.label)
                            if decision.skip_reason == "cross_protocol_history":
                                yield RetryEvent(
                                    reason=(
                                        f"跳过备用模型 {decision.ref.label}: "
                                        "当前会话已有历史，不能从 "
                                        f"{decision.current_protocol} 安全 fallback 到 "
                                        f"{decision.target_protocol}。"
                                        "请使用同协议备用模型，或开启新会话。"
                                    )
                                )
                            continue

                        candidate = decision.candidate
                        if candidate is None:
                            continue
                        if not candidate.provider.resolve_api_key():
                            tried_fallback_refs.add(candidate.ref.label)
                            yield RetryEvent(
                                reason=(
                                    f"跳过备用模型 {candidate.ref.label}: missing_key。"
                                    "请检查 API Key 配置。"
                                )
                            )
                            continue
                        yield RetryEvent(
                            reason=(
                                f"当前模型 {failed_ref} 请求失败: "
                                f"{fallback_error.status.value}。"
                                f"正在尝试备用模型 {candidate.ref.label}。"
                            )
                        )
                        try:
                            fallback_client = create_client(candidate.provider)
                        except asyncio.CancelledError:
                            raise
                        except Exception as candidate_error:
                            tried_fallback_refs.add(candidate.ref.label)
                            classified = classify_fallback_error(
                                candidate_error, candidate.provider
                            )
                            yield RetryEvent(
                                reason=(
                                    f"备用模型 {candidate.ref.label} 不可用: "
                                    f"{classified.status.value}。"
                                )
                            )
                            continue
                        candidate_runtime = _ModelRuntime(
                            client=fallback_client,
                            protocol=candidate.provider.protocol,
                            context_window=candidate.provider.get_context_window(),
                            provider=candidate.provider,
                            used_fallback=True,
                        )
                        (
                            fallback_api_conv,
                            fallback_tools,
                            fallback_prep_events,
                            fallback_prep_error,
                        ) = await self._prepare_runtime_request(
                            conversation, candidate_runtime, env_context
                        )
                        for event in fallback_prep_events:
                            yield event
                        if fallback_prep_error or fallback_api_conv is None:
                            tried_fallback_refs.add(candidate.ref.label)
                            detail = (
                                f" 原因: {fallback_prep_error}"
                                if fallback_prep_error
                                else ""
                            )
                            yield RetryEvent(
                                reason=(
                                    f"跳过备用模型 {candidate.ref.label}: "
                                    "context_window 不足或上下文重建失败。"
                                    "请减少上下文、清空会话，或选择更大 context_window 的备用模型。"
                                    f"{detail}"
                                )
                            )
                            continue
                        api_conv = fallback_api_conv
                        tools = fallback_tools
                        next_runtime = candidate_runtime
                        break

                    if next_runtime is None:
                        yield RetryEvent(
                            reason=(
                                "备用模型链已尝试完，但没有可用模型完成本轮请求。"
                                f"将返回当前错误: {fallback_error.status.value}。"
                            )
                        )
                        raise

                    turn_runtime = next_runtime

            response = collector.response
            if turn_runtime.used_fallback and not fallback_success_announced:
                ref = (
                    model_ref_for_provider(turn_runtime.provider).label
                    if turn_runtime.provider is not None
                    else "fallback model"
                )
                yield RetryEvent(reason=f"本轮已使用备用模型 {ref} 完成。")
                fallback_success_announced = True

            if self.hook_engine:
                ctx = self._build_hook_context("post_receive", message=response.text)
                await self.hook_engine.run_hooks("post_receive", ctx)
                for he in self._drain_hook_events():
                    yield he

            self.total_input_tokens += response.input_tokens
            self.total_output_tokens += response.output_tokens
            yield UsageEvent(
                input_tokens=self.total_input_tokens,
                output_tokens=self.total_output_tokens,
                request_input_tokens=response.input_tokens,
                request_output_tokens=response.output_tokens,
                provider_usage=response.provider_usage,
                provider=turn_runtime.provider.name if turn_runtime.provider else None,
                model_id=turn_runtime.provider.model if turn_runtime.provider else None,
                request_index=response.request_index,
            )

            conv_thinking = [
                ConvThinkingBlock(thinking=tb.thinking, signature=tb.signature)
                for tb in response.thinking_blocks
            ]

            if response.stop_reason == "max_tokens":
                if not max_tokens_escalated:
                    cancelled_outcomes = await streaming_executor.cancel_and_reap()
                    active_executor[0] = None
                    for outcome in cancelled_outcomes:
                        yield self._permission_decision_event(outcome)
                    turn_runtime.client.set_max_output_tokens(MAX_TOKENS_CEILING)
                    max_tokens_escalated = True
                    if response.text:
                        conversation.add_assistant_message(
                            response.text, thinking_blocks=conv_thinking
                        )
                        conversation.add_user_message(
                            "Output token limit hit. Resume directly from where you stopped. "
                            "Do not apologize or repeat previous content. Pick up mid-thought if needed."
                        )
                    yield RetryEvent(reason="max_tokens escalation")
                    continue
                elif output_recoveries < MAX_OUTPUT_TOKENS_RECOVERIES:
                    cancelled_outcomes = await streaming_executor.cancel_and_reap()
                    active_executor[0] = None
                    for outcome in cancelled_outcomes:
                        yield self._permission_decision_event(outcome)
                    output_recoveries += 1
                    conversation.add_assistant_message(
                        response.text, thinking_blocks=conv_thinking
                    )
                    conversation.add_user_message(
                        "Output token limit hit. Resume directly from where you stopped. "
                        "Break remaining work into smaller pieces."
                    )
                    yield RetryEvent(
                        reason=f"max_tokens recovery {output_recoveries}/{MAX_OUTPUT_TOKENS_RECOVERIES}"
                    )
                    continue
            else:
                output_recoveries = 0

            if not response.tool_calls:
                conversation.add_assistant_message(
                    response.text, thinking_blocks=conv_thinking
                )
                self._loop_count += 1
                if (
                    self._loop_count % MEMORY_EXTRACTION_INTERVAL == 0
                    and self.memory_manager
                ):
                    asyncio.ensure_future(self._extract_memories(conversation))
                if self._consolidator is not None:
                    asyncio.ensure_future(self._consolidator.maybe_run())
                if self.hook_engine:
                    ctx = self._build_hook_context("turn_end")
                    await self.hook_engine.run_hooks("turn_end", ctx)
                    ctx = self._build_hook_context("session_end")
                    await self.hook_engine.run_hooks("session_end", ctx)
                    for he in self._drain_hook_events():
                        yield he
                if self.file_history is not None:
                    summary = response.text[:60] + "..." if len(response.text) > 60 else response.text
                    self.file_history.make_snapshot(len(conversation.history), summary)
                yield LoopComplete(total_turns=iteration)
                break

            tool_uses = [
                ToolUseBlock(
                    tool_use_id=tc.tool_id,
                    tool_name=tc.tool_name,
                    arguments=tc.arguments,
                )
                for tc in response.tool_calls
            ]
            conversation.add_assistant_message(
                response.text, tool_uses, thinking_blocks=conv_thinking
            )
            # 在 assistant 回复加入历史后锚定实际用量：基线（input + cache + output）
            # 覆盖到当前位置，因此下一轮迭代顶部的 auto-compact 检查只需对
            # 接下来追加的 tool results 做字符估算。
            conversation.record_usage_anchor(
                response.input_tokens,
                response.output_tokens,
                response.cache_read,
                response.cache_creation,
            )

            tool_results: list[ToolResultBlock] = []
            # Read-only, pre-authorized prefix calls may already be running while
            # the model is still streaming. Results are still appended in original
            # tool-call order, preserving provider protocol invariants.
            for br in await streaming_executor.collect_results():
                if br.is_unknown:
                    consecutive_unknown += 1
                else:
                    consecutive_unknown = 0
                content = self._maybe_persist_or_truncate(br.tool_id, br.result.output)
                tool_results.append(ToolResultBlock(tool_use_id=br.tool_id, content=content, is_error=br.result.is_error))
                for he in self._drain_hook_events():
                    yield he
                yield self._permission_decision_event(br)
                if self.hook_engine and br.executed:
                    source_call = next(
                        tc for tc in response.tool_calls if tc.tool_id == br.tool_id
                    )
                    hook_ctx = self._build_hook_context(
                        "post_tool_use", tool_name=br.tool_name,
                        tool_args=source_call.arguments,
                        file_path=self._infer_file_path(source_call.arguments),
                    )
                    await self.hook_engine.run_hooks("post_tool_use", hook_ctx)
                    for he in self._drain_hook_events():
                        yield he
                yield ToolResultEvent(tool_id=br.tool_id, tool_name=br.tool_name, output=br.result.output, is_error=br.result.is_error, elapsed=br.elapsed)
            active_executor[0] = None

            batches = partition_tool_calls(response.tool_calls[streamed_count:], self.registry)

            for batch in batches:
                predecisions: dict[str, _ToolDecision] = {
                    tc.tool_id: streaming_decisions[tc.tool_id]
                    for tc in batch.calls if tc.tool_id in streaming_decisions
                }
                can_run_direct = batch.concurrent and len(batch.calls) > 1
                if can_run_direct:
                    for tc in batch.calls:
                        if tc.tool_id not in predecisions:
                            predecisions[tc.tool_id] = await self._assess_tool(tc)
                    can_run_direct = can_run_direct and all(
                        decision.decision.effect in {"allow", "deny"}
                        for decision in predecisions.values()
                    )
                if can_run_direct:
                    batch_results = await self._execute_batch_parallel(
                        batch.calls, predecisions,
                    )
                    for br in batch_results:
                        if br.is_unknown:
                            consecutive_unknown += 1
                        else:
                            consecutive_unknown = 0
                        content = self._maybe_persist_or_truncate(
                            br.tool_id, br.result.output
                        )
                        tool_results.append(
                            ToolResultBlock(
                                tool_use_id=br.tool_id,
                                content=content,
                                is_error=br.result.is_error,
                            )
                        )
                        yield self._permission_decision_event(br)
                        if self.hook_engine and br.executed:
                            source_call = next(
                                tc for tc in batch.calls if tc.tool_id == br.tool_id
                            )
                            hook_ctx = self._build_hook_context(
                                "post_tool_use", tool_name=br.tool_name,
                                tool_args=source_call.arguments,
                                file_path=self._infer_file_path(source_call.arguments),
                            )
                            await self.hook_engine.run_hooks("post_tool_use", hook_ctx)
                            for he in self._drain_hook_events():
                                yield he
                        yield ToolResultEvent(
                            tool_id=br.tool_id,
                            tool_name=br.tool_name,
                            output=br.result.output,
                            is_error=br.result.is_error,
                            elapsed=br.elapsed,
                        )
                else:
                    for tc in batch.calls:
                        outcome: ToolExecutionOutcome | None = None
                        decision = predecisions.get(tc.tool_id) or await self._assess_tool(tc)
                        for he in self._drain_hook_events():
                            yield he
                        async for item in self._execute_tool(tc, decision=decision):
                            if isinstance(item, PermissionRequest):
                                yield item
                            else:
                                outcome = item

                        if outcome is None:
                            outcome = ToolExecutionOutcome(
                                tc.tool_id, tc.tool_name,
                                ToolResult("Error: no result from tool", is_error=True),
                                0.0, False, decision.decision.effect,
                                decision.mandatory_safety, decision.hook_effect,
                                False, None, decision.decision.persistable, False,
                                "sequential",
                            )

                        if outcome.is_unknown:
                            consecutive_unknown += 1
                        else:
                            consecutive_unknown = 0

                        yield self._permission_decision_event(outcome)
                        if self.hook_engine and outcome.executed:
                            file_path = self._infer_file_path(tc.arguments)
                            hook_ctx = self._build_hook_context(
                                "post_tool_use",
                                tool_name=tc.tool_name,
                                tool_args=tc.arguments,
                                file_path=file_path,
                            )
                            await self.hook_engine.run_hooks("post_tool_use", hook_ctx)
                            for he in self._drain_hook_events():
                                yield he

                        content = self._maybe_persist_or_truncate(
                            tc.tool_id, outcome.result.output
                        )
                        tool_results.append(
                            ToolResultBlock(
                                tool_use_id=tc.tool_id,
                                content=content,
                                is_error=outcome.result.is_error,
                            )
                        )
                        yield ToolResultEvent(
                            tool_id=tc.tool_id,
                            tool_name=tc.tool_name,
                            output=outcome.result.output,
                            is_error=outcome.result.is_error,
                            elapsed=outcome.elapsed,
                        )

            if consecutive_unknown >= 3:
                yield ErrorEvent(
                    message="Agent terminated: too many consecutive unknown tool calls"
                )
                break

            exit_plan_called = any(
                tc.tool_name == "ExitPlanMode" for tc in response.tool_calls
            )
            conversation.add_tool_results_message(tool_results)

            # 非阻塞 memory recall：工具执行完后检查 prefetch 是否就绪
            if self.memory_recall_task and not self._memory_recall_consumed:
                if self.memory_recall_task.done():
                    try:
                        recall = self.memory_recall_task.result()
                        if recall:
                            conversation.add_system_reminder(recall)
                    except Exception:
                        pass
                    self._memory_recall_consumed = True

            if exit_plan_called:
                yield TurnComplete(turn=iteration)
                yield LoopComplete(total_turns=iteration)
                break

            if self.hook_engine:
                ctx = self._build_hook_context("turn_end")
                await self.hook_engine.run_hooks("turn_end", ctx)
                for he in self._drain_hook_events():
                    yield he
            yield TurnComplete(turn=iteration)


    def _consume_mailbox(self, conversation: ConversationManager) -> None:
        if not self.team_name or not self._team_manager:
            return
        try:
            mailbox = self._team_manager.get_mailbox(self.team_name)
            if mailbox is None:
                return
            messages = mailbox.consume(self.agent_id)
            for msg in messages:
                prefix = f"[Message from {msg.from_agent}]"
                if msg.message_type != "text":
                    prefix = f"[{msg.message_type} from {msg.from_agent}]"
                content = f"{prefix} {msg.content}"
                conversation.add_user_message(content)
        except Exception as e:
            log.debug("Mailbox consumption failed: %s", e)

    def _build_permission_description(self, tc: ToolCallComplete) -> str:
        """为 HITL 权限确认生成人类可读的操作描述。"""
        return PermissionChecker.describe_tool_action(tc.tool_name, tc.arguments)

    async def _assess_tool(self, tc: ToolCallComplete) -> _ToolDecision:
        """Compute policy and Hook constraints once, retaining audit metadata."""
        tool = self.registry.get(tc.tool_name)
        if tool is None:
            return _ToolDecision(
                Decision("deny", f"unknown tool: {tc.tool_name}", persistable=False)
            )
        if not self.registry.is_enabled(tc.tool_name):
            return _ToolDecision(
                Decision("deny", f"disabled tool: {tc.tool_name}", persistable=False)
            )
        assessment = None
        assessment_error = ""
        if self.permission_checker:
            try:
                assessment = self.permission_checker.assess(tool, tc.arguments)
            except Exception as exc:
                assessment_error = f"权限安全检查失败: {exc}"
        if assessment is not None and assessment.mandatory_denied:
            try:
                decision = self.permission_checker.finalize(assessment)
            except Exception as exc:
                decision = Decision(
                    "deny", f"危险命令最终决策失败: {exc}", persistable=False,
                )
            return _ToolDecision(decision, mandatory_safety=True)
        hook_effect = None
        hook_reason = ""
        if self.hook_engine:
            file_path = self._infer_file_path(tc.arguments)
            hook_ctx = self._build_hook_context(
                "pre_tool_use", tool_name=tc.tool_name, tool_args=tc.arguments, file_path=file_path
            )
            try:
                rejection = await self.hook_engine.run_pre_tool_hooks(hook_ctx)
            except Exception as exc:
                rejection = ToolRejectedError(tc.tool_name, f"Hook检查失败: {exc}", "pre_tool_use")
            if rejection is not None:
                hook_effect, hook_reason = "deny", f"Hook rejected: {rejection.reason}"
        if assessment is not None:
            try:
                decision = self.permission_checker.finalize(
                    assessment, hook_effect=hook_effect, hook_reason=hook_reason
                )
                return _ToolDecision(
                    decision,
                    mandatory_safety=assessment.mandatory_safety,
                    hook_effect=hook_effect,
                )
            except Exception as exc:
                assessment_error = f"权限最终决策失败: {exc}"
        if assessment_error:
            if hook_effect == "deny":
                decision = Decision("deny", hook_reason, persistable=False)
            else:
                decision = Decision("ask", assessment_error, persistable=False)
            return _ToolDecision(
                decision,
                mandatory_safety=bool(
                    assessment is not None and assessment.mandatory_safety
                ),
                hook_effect=hook_effect,
            )
        if hook_effect:
            return _ToolDecision(
                Decision(effect="deny", reason=hook_reason, persistable=False),
                hook_effect=hook_effect,
            )
        return _ToolDecision(Decision("allow", "no permission restriction"))

    async def _decide_tool(self, tc: ToolCallComplete) -> Decision:
        return (await self._assess_tool(tc)).decision

    @staticmethod
    def _permission_decision_event(
        outcome: ToolExecutionOutcome,
    ) -> PermissionDecisionEvent:
        """Single final telemetry exit for every tool execution path."""
        return PermissionDecisionEvent(
            tool_use_id=outcome.tool_id,
            tool_name=outcome.tool_name,
            final_effect=outcome.final_effect,
            mandatory_safety=outcome.mandatory_safety,
            hook_effect=outcome.hook_effect,
            hitl_required=outcome.hitl_required,
            hitl_response=outcome.hitl_response,
            persistable=outcome.persistable,
            executed=outcome.executed,
            execution_path=outcome.execution_path,
        )

    @staticmethod
    def _permission_event_payload(event: PermissionDecisionEvent) -> dict[str, Any]:
        return {
            "type": "permission_decision",
            "tool_use_id": event.tool_use_id,
            "tool_name": event.tool_name,
            "final_effect": event.final_effect,
            "mandatory_safety": event.mandatory_safety,
            "hook_effect": event.hook_effect,
            "hitl_required": event.hitl_required,
            "hitl_response": event.hitl_response,
            "persistable": event.persistable,
            "executed": event.executed,
            "execution_path": event.execution_path,
        }

    @staticmethod
    def _cancelled_tool_outcome(
        tc: ToolCallComplete, decision: _ToolDecision, execution_path: str,
    ) -> ToolExecutionOutcome:
        return ToolExecutionOutcome(
            tc.tool_id, tc.tool_name,
            ToolResult("Tool execution cancelled", is_error=True), 0.0, False,
            decision.decision.effect, decision.mandatory_safety,
            decision.hook_effect, False, None, decision.decision.persistable,
            False, execution_path,
        )

    async def _execute_single_tool_direct(
        self, tc: ToolCallComplete, decision: _ToolDecision | None = None,
        *, execution_path: str = "direct",
    ) -> ToolExecutionOutcome:
        tool = self.registry.get(tc.tool_name)
        start = time.monotonic()
        decision = decision or await self._assess_tool(tc)

        if tool is None:
            return ToolExecutionOutcome(
                tool_id=tc.tool_id,
                tool_name=tc.tool_name,
                result=ToolResult(output=f"Error: unknown tool '{tc.tool_name}'", is_error=True),
                elapsed=time.monotonic() - start,
                is_unknown=True,
                final_effect="deny", mandatory_safety=False,
                hook_effect=decision.hook_effect, hitl_required=False,
                hitl_response=None, persistable=False, executed=False,
                execution_path=execution_path,
            )

        if not self.registry.is_enabled(tc.tool_name):
            return ToolExecutionOutcome(
                tool_id=tc.tool_id,
                tool_name=tc.tool_name,
                result=ToolResult(output=f"Error: tool '{tc.tool_name}' is disabled", is_error=True),
                elapsed=time.monotonic() - start,
                is_unknown=False,
                final_effect="deny", mandatory_safety=False,
                hook_effect=decision.hook_effect, hitl_required=False,
                hitl_response=None, persistable=False, executed=False,
                execution_path=execution_path,
            )

        if decision.decision.effect != "allow":
            return ToolExecutionOutcome(
                tool_id=tc.tool_id, tool_name=tc.tool_name,
                result=ToolResult(
                    output=f"Permission denied: {decision.decision.reason}", is_error=True,
                ),
                elapsed=time.monotonic() - start, is_unknown=False,
                final_effect=decision.decision.effect,
                mandatory_safety=decision.mandatory_safety,
                hook_effect=decision.hook_effect, hitl_required=False,
                hitl_response=None, persistable=decision.decision.persistable,
                executed=False, execution_path=execution_path,
            )

        executed = False
        try:
            params = tool.params_model.model_validate(tc.arguments)
            executed = True
            result = await tool.execute(params)
        except asyncio.CancelledError:
            result = ToolResult(output="Tool execution cancelled", is_error=True)
        except ValidationError as e:
            result = ToolResult(output=f"Parameter validation error: {e}", is_error=True)
        except Exception as e:
            result = ToolResult(output=f"Tool execution error: {e}", is_error=True)

        self._snapshot_for_recovery(tc, result)

        return ToolExecutionOutcome(
            tool_id=tc.tool_id,
            tool_name=tc.tool_name,
            result=result,
            elapsed=time.monotonic() - start,
            is_unknown=False,
            final_effect=decision.decision.effect,
            mandatory_safety=decision.mandatory_safety,
            hook_effect=decision.hook_effect,
            hitl_required=False,
            hitl_response=None,
            persistable=decision.decision.persistable,
            executed=executed,
            execution_path=execution_path,
        )


    async def _execute_batch_parallel(
        self, calls: list[ToolCallComplete], decisions: dict[str, _ToolDecision],
    ) -> list[ToolExecutionOutcome]:
        tasks = [
            self._execute_single_tool_direct(
                tc, decisions[tc.tool_id], execution_path="parallel",
            )
            for tc in calls
        ]
        return list(await asyncio.gather(*tasks))

    async def _execute_tool(
        self, tc: ToolCallComplete, *, decision: _ToolDecision | None = None
    ) -> AsyncIterator[PermissionRequest | ToolExecutionOutcome]:
        tool = self.registry.get(tc.tool_name)
        start = time.monotonic()
        is_unknown = False
        decision = decision or await self._assess_tool(tc)

        if tool is None:
            yield await self._execute_single_tool_direct(
                tc, decision, execution_path="sequential",
            )
            return

        if not self.registry.is_enabled(tc.tool_name):
            yield await self._execute_single_tool_direct(
                tc, decision, execution_path="sequential",
            )
            return

        hitl_required = decision.decision.effect == "ask"
        hitl_response: str | None = None
        if decision.decision.effect != "allow":
            if decision.decision.effect == "deny":
                result = ToolResult(
                    output=f"Permission denied: {decision.decision.reason}",
                    is_error=True,
                )
                yield ToolExecutionOutcome(
                    tc.tool_id, tc.tool_name, result, time.monotonic() - start,
                    is_unknown, "deny", decision.mandatory_safety,
                    decision.hook_effect, False, None,
                    decision.decision.persistable, False, "sequential",
                )
                return

            if decision.decision.effect == "ask":
                loop = asyncio.get_running_loop()
                future: asyncio.Future[PermissionResponse] = loop.create_future()
                desc = self._build_permission_description(tc)
                # 向调用方 yield 权限请求事件，由调用方处理
                yield PermissionRequest(
                    tool_name=tc.tool_name,
                    description=f"{desc}\nReason: {decision.decision.reason}",
                    future=future,
                    tool_use_id=tc.tool_id,
                )
                response = await future
                hitl_response = (
                    response.value if isinstance(response, PermissionResponse) else str(response)
                )

                if response in {PermissionResponse.DENY, "deny"}:
                    result = ToolResult(
                        output="Permission denied: 用户拒绝了此操作",
                        is_error=True,
                    )
                    yield ToolExecutionOutcome(
                        tc.tool_id, tc.tool_name, result, time.monotonic() - start,
                        is_unknown, "ask", decision.mandatory_safety,
                        decision.hook_effect, True, hitl_response,
                        decision.decision.persistable, False, "sequential",
                    )
                    return

                if (
                    response == PermissionResponse.ALLOW_ALWAYS
                    and decision.decision.persistable
                ):
                    from codepacex.permissions.rules import Rule, extract_content
                    content = extract_content(tc.tool_name, tc.arguments)
                    pattern = f"{content[:60]}*" if len(content) > 60 else f"{content}*"
                    # 持久化规则写入本地文件
                    rule = Rule(tool_name=tc.tool_name, pattern=pattern, effect="allow")
                    assert self.permission_checker is not None
                    self.permission_checker.rule_engine.append_local_rule(rule)
                    # 同时加入会话级放行集合，本轮立即生效无需磁盘读取
                    self.permission_checker.add_session_allow(tc.tool_name, content)

        executed = False
        try:
            params = tool.params_model.model_validate(tc.arguments)
            executed = True
            result = await tool.execute(params)
        except ValidationError as e:
            result = ToolResult(
                output=f"Parameter validation error: {e}", is_error=True
            )
        except Exception as e:
            result = ToolResult(
                output=f"Tool execution error: {e}", is_error=True
            )

        self._snapshot_for_recovery(tc, result)

        yield ToolExecutionOutcome(
            tc.tool_id, tc.tool_name, result, time.monotonic() - start,
            is_unknown, decision.decision.effect, decision.mandatory_safety,
            decision.hook_effect, hitl_required, hitl_response,
            decision.decision.persistable, executed, "sequential",
        )

    def _snapshot_for_recovery(
        self, tc: ToolCallComplete, result: ToolResult
    ) -> None:
        """捕获 ReadFile 刚交给模型的内容，以便 Layer 2 压缩对话后
        auto_compact 能重新附加这些数据。每次 ReadFile 多一次磁盘读取，
        比从 tool 输出中反向解析行号要划算。
        """
        if result.is_error or tc.tool_name != "ReadFile":
            return
        path = tc.arguments.get("file_path") if isinstance(tc.arguments, dict) else None
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            return
        self.recovery_state.record_file_read(path, content)

    async def _extract_memories(
        self, conversation: ConversationManager
    ) -> None:
        """触发记忆提取， inProgress + pendingContext 合并策略。

        当提取正在进行时，新的触发不会启动并发提取，而是标记 _pending_extraction。
        当前提取完成后检查该标志，如果有 pending 则立即执行一次尾随提取，
        防止多个触发器同时执行导致重复提取。
        """
        if not self.memory_manager:
            return

        # 合并策略：正在提取时暂存新请求，等当前提取完成后尾随执行
        if self._extracting:
            log.debug("[extractMemories] extraction in progress — stashing for trailing run")
            self._pending_extraction = True
            return

        self._extracting = True
        try:
            await self.memory_manager.extract(
                self.client, conversation, self.protocol
            )
        except Exception as e:
            log.debug("Memory extraction failed: %s", e)
        finally:
            self._extracting = False
            # 检查是否有尾随提取请求
            if self._pending_extraction:
                self._pending_extraction = False
                log.debug("[extractMemories] running trailing extraction for stashed context")
                # 递归调用自身处理尾随请求
                await self._extract_memories(conversation)

    async def manual_compact(
        self,
        conversation: ConversationManager,
        event_callback: Callable[[AgentEvent], None] | None = None,
    ) -> CompactNotification | ErrorEvent:
        # auto_compact 会用摘要替换 conversation.history，所有 tool-result 内容
        # （原始或已替换的）都将被丢弃。这里跳过 apply_tool_result_budget —
        # 它在主循环中的唯一目的是为 LLM 调用生成 api_conv，而本路径不需要
        # 发起看到替换结果的 LLM 调用（auto_compact 内部的摘要调用操作的是原始对话）。
        runtime_events: list[RuntimeManifestEvent] = []
        usage_events: list[UsageEvent] = []

        def on_runtime(event: RuntimeManifestEvent) -> None:
            runtime_events.append(self._index_runtime_event(event))

        def on_usage(event: RuntimeManifestEvent, stream_end: StreamEnd) -> None:
            usage_events.append(self._usage_from_stream_end(event, stream_end))

        recovery, recovery_tools = self._compression_recovery_inputs(self.protocol)
        result = await auto_compact(
            conversation,
            self.client,
            self.context_window,
            self.session_dir,
            protocol=self.protocol,
            manual=True,
            breaker=self.compact_breaker,
            recovery=recovery,
            tool_schemas=recovery_tools,
            transcript_path=self._transcript_path,
            runtime_event_sink=on_runtime,
            usage_event_sink=on_usage,
        )
        if event_callback is not None:
            for event in [*runtime_events, *usage_events]:
                event_callback(event)
        if isinstance(result, CompactEvent):
            if event_callback is not None:
                event_callback(CompressionEvent(
                    trigger="manual", success=True, tokens_before=result.before_tokens,
                ))
            env_context = build_environment_context(
            self.work_dir, self.active_skills, self._skill_catalog, self._agent_catalog
        )
            conversation.inject_environment(env_context)
            memory_content = self.memory_manager.load() if self.memory_manager else ""
            conversation.inject_long_term_memory(
                self.instructions_content, memory_content
            )
            return CompactNotification(
                before_tokens=result.before_tokens,
                message=f"上下文已压缩（压缩前 {result.before_tokens:,} tokens）",
                boundary=result.boundary,
            )
        if event_callback is not None and isinstance(result, str):
            event_callback(CompressionEvent(
                trigger="manual", success=False,
                tokens_before=conversation.current_tokens(),
                error_category="compression_error",
            ))
        return ErrorEvent(message=result or "压缩失败：对话历史为空或未达到压缩条件")

    async def run_to_completion(
        self, task: str, conversation: ConversationManager | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        if conversation is None:
            conversation = ConversationManager()

            env_context = build_environment_context(
                self.work_dir, self.active_skills, self._skill_catalog, self._agent_catalog
            )
            conversation.inject_environment(env_context)

            if self.instructions_content:
                memory_content = self.memory_manager.load() if self.memory_manager else ""
                conversation.inject_long_term_memory(
                    self.instructions_content, memory_content
                )

        if task:
            conversation.add_user_message(task)

        self._runtime_request_index = 0

        hook_prompts = (
            self.hook_engine.get_prompt_messages() if self.hook_engine else None
        )
        system = build_system_prompt(
            hook_prompts=hook_prompts,
            coordinator_mode=self.coordinator_mode,
        )

        tools = self.registry.get_all_schemas(self.protocol)

        log.info(
            "[run_to_completion] agent=%s tools=%d names=%s coordinator=%s",
            self.agent_id,
            len(tools),
            [t["name"] for t in tools][:10],
            self.coordinator_mode,
        )

        last_text = ""

        for iteration in range(1, self.max_iterations + 1):
            if self.hook_engine:
                ctx = self._build_hook_context("turn_start")
                await self.hook_engine.run_hooks("turn_start", ctx)

            self._consume_mailbox(conversation)
            if self.notification_fn:
                for note in self.notification_fn():
                    conversation.add_system_reminder(note)

            compact_runtime_events: list[RuntimeManifestEvent] = []
            compact_usage_events: list[UsageEvent] = []

            def on_compact_runtime(event: RuntimeManifestEvent) -> None:
                compact_runtime_events.append(self._index_runtime_event(event))

            def on_compact_usage(event: RuntimeManifestEvent, stream_end: StreamEnd) -> None:
                compact_usage_events.append(self._usage_from_stream_end(event, stream_end))

            compact_tokens_before = conversation.current_tokens()
            recovery, recovery_tools = self._compression_recovery_inputs(self.protocol)
            compact_result = await auto_compact(
                conversation,
                self.client,
                self.context_window,
                self.session_dir,
                protocol=self.protocol,
                breaker=self.compact_breaker,
                recovery=recovery,
                tool_schemas=recovery_tools,
                transcript_path=self._transcript_path,
                runtime_event_sink=on_compact_runtime,
                usage_event_sink=on_compact_usage,
            )
            if event_callback:
                for runtime_event in compact_runtime_events:
                    event_callback(self._runtime_event_payload(runtime_event))
                for usage_event in compact_usage_events:
                    event_callback(self._usage_event_payload(usage_event))
            if isinstance(compact_result, CompactEvent):
                if event_callback:
                    event_callback(self._compression_event_payload(CompressionEvent(
                        trigger="automatic", success=True,
                        tokens_before=compact_result.before_tokens,
                    )))
                conversation.inject_environment(env_context)
            elif isinstance(compact_result, str) and event_callback:
                event_callback(self._compression_event_payload(CompressionEvent(
                    trigger="automatic", success=False,
                    tokens_before=compact_tokens_before,
                    error_category="compression_error",
                )))

            deferred_names = self.registry.get_deferred_tool_names()
            if deferred_names:
                conversation.add_system_reminder(
                    "The following deferred tools are available via ToolSearch. "
                    "Their schemas are NOT loaded - use ToolSearch with "
                    'query "select:<name>[,<name>...]" to load tool schemas before calling them:\n'
                    + "\n".join(deferred_names)
                )

            api_conv, _new_records = apply_tool_result_budget(
                conversation, self.session_dir, self.replacement_state
            )
            if _new_records:
                append_replacement_records(self.session_dir, _new_records)

            collector = StreamCollector(self._index_runtime_event)
            llm_stream = self.client.stream(api_conv, system=system, tools=tools)
            async for stream_event in collector.consume(llm_stream):
                if event_callback and isinstance(stream_event, RuntimeManifestEvent):
                    event_callback(self._runtime_event_payload(stream_event))

            response = collector.response
            self.total_input_tokens += response.input_tokens
            self.total_output_tokens += response.output_tokens

            if event_callback:
                event_callback(self._usage_event_payload(UsageEvent(
                    input_tokens=self.total_input_tokens,
                    output_tokens=self.total_output_tokens,
                    request_input_tokens=response.input_tokens,
                    request_output_tokens=response.output_tokens,
                    provider_usage=response.provider_usage,
                    provider=(
                        self.active_provider.name if self.active_provider is not None else None
                    ),
                    model_id=(
                        self.active_provider.model if self.active_provider is not None else None
                    ),
                    request_index=response.request_index,
                )))

            if response.text:
                last_text = response.text
                if event_callback:
                    event_callback({
                        "type": "stream_text",
                        "text": response.text,
                    })

            log.info(
                "[run_to_completion] agent=%s iter=%d tool_calls=%d text_len=%d stop=%s",
                self.agent_id, iteration, len(response.tool_calls),
                len(response.text), response.stop_reason,
            )

            if not response.tool_calls:
                conversation.add_assistant_message(response.text)
                if self.file_history is not None:
                    summary = response.text[:60] + "..." if len(response.text) > 60 else response.text
                    self.file_history.make_snapshot(len(conversation.history), summary)
                break

            tool_uses = [
                ToolUseBlock(
                    tool_use_id=tc.tool_id,
                    tool_name=tc.tool_name,
                    arguments=tc.arguments,
                )
                for tc in response.tool_calls
            ]
            conversation.add_assistant_message(response.text, tool_uses)
            # assistant 回复已在历史中，锚定实际用量；下一轮迭代只需对
            # 下方追加的 tool results 做字符估算。
            conversation.record_usage_anchor(
                response.input_tokens,
                response.output_tokens,
                response.cache_read,
                response.cache_creation,
            )

            tool_results: list[ToolResultBlock] = []
            for tc in response.tool_calls:
                if event_callback:
                    event_callback({
                        "type": "tool_use",
                        "toolName": tc.tool_name,
                        "args": tc.arguments,
                    })
                outcome = await self._execute_tool_noninteractive(tc)
                permission_event = self._permission_decision_event(outcome)
                if event_callback:
                    event_callback(self._permission_event_payload(permission_event))
                content = self._maybe_persist_or_truncate(
                    tc.tool_id, outcome.result.output,
                )
                tool_results.append(
                    ToolResultBlock(
                        tool_use_id=tc.tool_id,
                        content=content,
                        is_error=outcome.result.is_error,
                    )
                )

            conversation.add_tool_results_message(tool_results)

            if self.hook_engine:
                ctx = self._build_hook_context("turn_end")
                await self.hook_engine.run_hooks("turn_end", ctx)

        return last_text

    async def _execute_tool_noninteractive(
        self, tc: ToolCallComplete
    ) -> ToolExecutionOutcome:
        tool = self.registry.get(tc.tool_name)
        start = time.monotonic()
        decision = await self._assess_tool(tc)

        if tool is None:
            return await self._execute_single_tool_direct(
                tc, decision, execution_path="noninteractive",
            )

        if not self.registry.is_enabled(tc.tool_name):
            return await self._execute_single_tool_direct(
                tc, decision, execution_path="noninteractive",
            )

        if decision.decision.effect != "allow":
            if decision.decision.effect == "deny":
                result = ToolResult(
                    output=f"Permission denied: {decision.decision.reason}",
                    is_error=True,
                )
                hitl_required = False
            else:
                result = ToolResult(
                    output=("Permission denied: non-interactive agent cannot prompt "
                            f"user ({decision.decision.reason})"),
                    is_error=True,
                )
                hitl_required = True
            return ToolExecutionOutcome(
                tc.tool_id, tc.tool_name, result, time.monotonic() - start,
                False, decision.decision.effect, decision.mandatory_safety,
                decision.hook_effect, hitl_required,
                "deny" if hitl_required else None,
                decision.decision.persistable, False, "noninteractive",
            )

        executed = False
        try:
            params = tool.params_model.model_validate(tc.arguments)
            executed = True
            result = await tool.execute(params)
        except ValidationError as e:
            result = ToolResult(
                output=f"Parameter validation error: {e}", is_error=True
            )
        except Exception as e:
            result = ToolResult(
                output=f"Tool execution error: {e}", is_error=True
            )

        if self.hook_engine and executed:
            file_path = self._infer_file_path(tc.arguments)
            hook_ctx = self._build_hook_context(
                "post_tool_use",
                tool_name=tc.tool_name,
                tool_args=tc.arguments,
                file_path=file_path,
            )
            await self.hook_engine.run_hooks("post_tool_use", hook_ctx)

        return ToolExecutionOutcome(
            tc.tool_id, tc.tool_name, result, time.monotonic() - start,
            False, "allow", decision.mandatory_safety, decision.hook_effect,
            False, None, decision.decision.persistable, executed, "noninteractive",
        )

    def _maybe_persist_or_truncate(self, tool_use_id: str, text: str) -> str:
        from codepacex.context.manager import (
            SINGLE_RESULT_CHAR_LIMIT,
            make_persisted_preview,
            persist_tool_result,
        )

        if len(text) > SINGLE_RESULT_CHAR_LIMIT:
            fp = persist_tool_result(tool_use_id, text, self.session_dir)
            return make_persisted_preview(text, fp)
        if len(text) > MAX_OUTPUT_CHARS:
            return text[:MAX_OUTPUT_CHARS] + "\n… (output truncated)"
        return text

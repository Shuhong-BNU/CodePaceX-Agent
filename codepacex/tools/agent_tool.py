"""提供 CodePaceX 的子 Agent、后台 Agent 和团队成员启动工具能力。

主要包含工具参数模型、执行逻辑和结果封装。该模块由工具注册表与 Agent 调度器调用，并维护输入校验、权限分类和副作用范围。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from codepacex.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from codepacex.agent import Agent
    from codepacex.agents.loader import AgentLoader
    from codepacex.agents.task_manager import TaskManager
    from codepacex.agents.trace import TraceManager
    from codepacex.client import LLMClient

log = logging.getLogger(__name__)


def _billable_request_usage(event: dict[str, Any]) -> tuple[int, int]:
    """Use total provider tokens when the frozen price assumes no cache discount."""
    input_tokens = int(event.get("request_input_tokens") or 0)
    output_tokens = int(event.get("request_output_tokens") or 0)
    raw = event.get("provider_usage")
    if isinstance(raw, dict):
        for key in ("prompt_tokens", "input_tokens"):
            if key in raw and raw[key] is not None:
                input_tokens = int(raw[key])
                break
        for key in ("completion_tokens", "output_tokens"):
            if key in raw and raw[key] is not None:
                output_tokens = int(raw[key])
                break
    return input_tokens, output_tokens


def _capture_foreground_child_event(
    event: dict[str, Any],
    request_usages: list[tuple[int, int]],
    runtime_manifests: list[dict[str, Any]],
) -> bool:
    """Persist only the child telemetry needed by the parent trace summary.

    Returns whether *event* is one actual child tool invocation.  Usage remains
    separate because the Multi-Agent runner already accounts for child usage
    through ``child_request_usages``; runtime manifests add provenance only.
    """
    event_type = event.get("type")
    if event_type == "usage":
        request_usages.append(_billable_request_usage(event))
    elif event_type == "runtime_manifest":
        runtime_manifests.append(dict(event))
    return event_type == "tool_use"


def _update_child_trace(
    trace_manager: Any,
    trace_node_id: str,
    child_agent: Any,
    request_usages: list[tuple[int, int]],
    runtime_manifests: list[dict[str, Any]],
    child_tool_call_count: int,
) -> None:
    """Update one child trace from the shared event accumulator.

    The event callback is the only source for per-request evidence.  Keep
    usage on the trace summary's existing single path and never infer tool
    calls from turns or token totals.
    """
    trace_manager.update(
        trace_node_id,
        input_tokens=sum(item[0] for item in request_usages),
        output_tokens=sum(item[1] for item in request_usages),
        request_count=child_agent._runtime_request_index,
        request_usages=request_usages,
        runtime_manifests=runtime_manifests,
        tool_call_count=child_tool_call_count,
    )


# 核心实现
class AgentToolParams(BaseModel):
    prompt: str
    description: str
    subagent_type: str | None = None
    model: str | None = None
    run_in_background: bool = False
    name: str | None = None
    isolation: str | None = None
    team_name: str | None = Field(
        default=None,
        description=(
            "REQUIRED when creating team members. Spawns the agent as a long-running "
            "teammate under this team (created via TeamCreate). Unlike regular sub-agents, "
            "team members run in their own terminal, persist after the lead returns, and "
            "communicate with each other via SendMessage. Without team_name the agent "
            "runs as a one-shot sub-agent that blocks and returns inline."
        ),
    )


PERMISSION_MODE_MAP = {
    "default": "DEFAULT",
    "acceptEdits": "ACCEPT_EDITS",
    "bypassPermissions": "BYPASS",
}


def _child_permission_checker(
    parent_agent: Agent,
    definition: Any,
    *,
    work_dir: str,
    permission_mode_override: str | None = None,
) -> Any:
    """Build an isolated child checker from the parent's effective profile.

    An experiment profile changes the parent's permission policy at runtime,
    rather than its agent-definition ``permissionMode``.  Preserve those
    profile-controlled switches for every in-process child while keeping each
    child checker (and its session approvals and path sandbox) independent.
    Without a profile, preserve the existing definition-only child behaviour.
    ``team_name`` children are the sole exception: their long-standing
    noninteractive teammate contract is bypassPermissions, independent of the
    agent definition.  The override changes only that effective mode; the
    checker, rule engine, sandbox, and session state remain child-local.
    """
    from codepacex.experiments import PermissionStrategy
    from codepacex.permissions import (
        DangerousCommandDetector,
        PathSandbox,
        PermissionChecker,
        PermissionMode,
        RuleEngine,
    )

    pm_enum = getattr(
        PermissionMode,
        PERMISSION_MODE_MAP.get(
            permission_mode_override or definition.permission_mode, "DEFAULT",
        ),
        PermissionMode.DEFAULT,
    )
    parent_checker = parent_agent.permission_checker
    profile = parent_agent.experiment_profile
    rule_engine = RuleEngine()
    session_allow_all = False
    sandbox_enabled = False

    if profile is not None and parent_checker is not None:
        # Explicit rules still win over a profile's preauthorization.  Clone
        # the sources instead of sharing the checker's session-local state.
        rule_engine = parent_checker.rule_engine.clone()
        strategy = profile.permission_strategy
        session_allow_all = (
            strategy is PermissionStrategy.SESSION_ALLOW
            and parent_checker.session_allow_all
        )
        sandbox_enabled = (
            strategy is PermissionStrategy.SANDBOX_AUTO_ALLOW
            and parent_checker.sandbox_enabled
        )

    return PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=PathSandbox(work_dir),
        rule_engine=rule_engine,
        mode=pm_enum,
        sandbox_enabled=sandbox_enabled,
        session_allow_all=session_allow_all,
    )


TEAMMATE_ADDENDUM = (
    "\n\nIMPORTANT: You are running as an agent in a team.\n"
    "Just writing a response in text is not visible to others\n"
    "on your team - you MUST use the SendMessage tool.\n"
    "The user interacts primarily with the team lead.\n"
    "Your work is coordinated through the task system\n"
    "and teammate messaging.\n\n"
    "You are working in an isolated Git worktree. "
    "All file paths you use MUST be relative to your current working directory. "
    "Do NOT use absolute paths from the original project — they are outside your sandbox and will be rejected."
)


class AgentTool(Tool):
    name = "Agent"
    description = (
        "Launch a sub-agent to handle a task in an isolated context. "
        "Use subagent_type to select a predefined agent type (e.g. Explore, Plan, general-purpose), "
        "or leave it empty to fork the current conversation. "
        "Use team_name to spawn a teammate in an existing team."
    )
    params_model = AgentToolParams
    category = "command"
    is_concurrency_safe = False


    def __init__(
        self,
        agent_loader: AgentLoader,
        task_manager: TaskManager,
        trace_manager: TraceManager,
        parent_agent: Agent,
        enable_fork: bool = False,
        provider_config: Any = None,
        worktree_manager: Any = None,
        team_manager: Any = None,
    ) -> None:
        self._agent_loader = agent_loader
        self._task_manager = task_manager
        self._trace_manager = trace_manager
        self._parent_agent = parent_agent
        self._enable_fork = enable_fork
        self._provider_config = provider_config
        self._worktree_manager = worktree_manager
        self._team_manager = team_manager

    def set_provider_config(self, provider_config: Any) -> None:
        self._provider_config = provider_config

    def _background_child_trace_callbacks(
        self, trace_node_id: str, sub_agent: Any,
    ) -> tuple[Any, Any]:
        """Build the TaskManager callbacks for one asynchronous child.

        Ordinary background children and in-process teammates both run through
        TaskManager, so they share one event accumulator and one terminal trace
        finalizer instead of silently diverging from foreground evidence.
        """
        request_usages: list[tuple[int, int]] = []
        runtime_manifests: list[dict[str, Any]] = []
        child_tool_call_count = 0
        terminalized = False

        def capture_child_event(event: dict[str, Any]) -> None:
            nonlocal child_tool_call_count
            if _capture_foreground_child_event(
                event, request_usages, runtime_manifests,
            ):
                child_tool_call_count += 1
            _update_child_trace(
                self._trace_manager, trace_node_id, sub_agent,
                request_usages, runtime_manifests, child_tool_call_count,
            )

        def finalize_background_trace(background_task: Any) -> None:
            nonlocal terminalized
            if terminalized:
                return
            terminalized = True
            _update_child_trace(
                self._trace_manager, trace_node_id, sub_agent,
                request_usages, runtime_manifests, child_tool_call_count,
            )
            # TraceManager's established aggregate schema recognizes completed
            # and failed terminal child states.  A cancellation therefore
            # closes as failed instead of remaining running.
            trace_status = (
                "completed" if background_task.status == "completed" else "failed"
            )
            self._trace_manager.complete(trace_node_id, trace_status)

        return capture_child_event, finalize_background_trace

    async def execute(self, params: BaseModel) -> ToolResult:
        p: AgentToolParams = params  # type: ignore[assignment]

        if p.team_name:
            return await self._execute_as_teammate(p)

        isolation = ""
        if p.subagent_type:
            defn = self._agent_loader.get(p.subagent_type)
            if defn and defn.isolation:
                isolation = defn.isolation

        if isolation == "worktree":
            return await self._execute_with_worktree(p)

        from codepacex.agents.fork import ForkError, build_forked_messages
        from codepacex.agents.parser import AgentDef
        from codepacex.agents.tool_filter import resolve_agent_tools
        from codepacex.agent import Agent as AgentClass
        from codepacex.conversation import ConversationManager

        definition: AgentDef | None = None
        conversation: ConversationManager

        if p.subagent_type:
            definition = self._agent_loader.get(p.subagent_type)
            if definition is None:
                return ToolResult(
                    output=f"Unknown agent type: '{p.subagent_type}'. "
                    f"Available types: {', '.join(t for t, _ in self._agent_loader.list_agents())}",
                    is_error=True,
                )
            conversation = ConversationManager()
        else:
            if not self._enable_fork:
                return ToolResult(
                    output="Fork mode is not enabled. "
                    "Set 'enable_fork: true' in config.yaml to use fork, "
                    "or specify a subagent_type parameter.",
                    is_error=True,
                )
            try:
                parent_conv = getattr(self._parent_agent, '_current_conversation', None)
                if parent_conv is None:
                    return ToolResult(
                        output="Cannot fork: no active conversation in parent agent.",
                        is_error=True,
                    )
                conversation = build_forked_messages(parent_conv, p.prompt)
            except ForkError as e:
                return ToolResult(output=str(e), is_error=True)

            definition = AgentDef(
                agent_type="fork",
                when_to_use="Forked from parent agent",
                system_prompt="",
                disallowed_tools=[],
                model="inherit",
                max_turns=self._parent_agent.max_iterations,
                permission_mode="bypassPermissions",
                source="builtin",
            )

        # 选择 LLM 客户端
        client = self._select_llm(p, definition)

        # 判断是否后台运行
        is_background = p.run_in_background or definition.background
        if self._enable_fork:
            is_background = True

        # 过滤工具（coordinator 模式可能缩减了注册表，这里用完整注册表）
        _base_registry = getattr(self._parent_agent, '_full_registry', None) or self._parent_agent.registry
        filtered_registry = resolve_agent_tools(
            _base_registry, definition, is_background
        )

        # 为子 agent 创建权限检查器
        checker = _child_permission_checker(
            self._parent_agent, definition,
            work_dir=self._parent_agent.work_dir,
        )

        # 创建子 agent
        sub_agent = AgentClass(
            client=client,
            registry=filtered_registry,
            protocol=self._parent_agent.protocol,
            work_dir=self._parent_agent.work_dir,
            max_iterations=definition.max_turns,
            permission_checker=checker,
            context_window=self._parent_agent.context_window,
            instructions_content=definition.system_prompt,
            hook_engine=self._parent_agent.hook_engine,
            experiment_profile=self._parent_agent.experiment_profile,
        )
        sub_agent.parent_id = self._parent_agent.agent_id
        sub_agent.trace_id = self._parent_agent.trace_id or self._parent_agent.agent_id

        # fork 子 agent 继承父 agent 的替换状态，确保共享的 tool_use_id 做出一致的
        # 决策——这样父子共享的 prompt cache 前缀才能保持字节级一致
        if p.subagent_type is None:
            from codepacex.context import clone_replacement_state
            sub_agent.replacement_state = clone_replacement_state(
                self._parent_agent.replacement_state
            )

        # 注册追踪节点
        trace_node = self._trace_manager.create(
            agent_type=definition.agent_type,
            parent_id=self._parent_agent.agent_id,
            trace_id=sub_agent.trace_id,
        )
        sub_agent.agent_id = trace_node.agent_id

        agent_name = p.name or p.subagent_type or f"agent-{trace_node.agent_id}"
        is_fork = p.subagent_type is None

        if is_background:
            capture_child_event, finalize_background_trace = (
                self._background_child_trace_callbacks(trace_node.agent_id, sub_agent)
            )

            if is_fork:
                sub_agent._fork_conversation = conversation
            try:
                task_id = self._task_manager.launch(
                    agent=sub_agent,
                    task="" if is_fork else p.prompt,
                    name=agent_name,
                    fork_conversation=conversation if is_fork else None,
                    event_callback=capture_child_event,
                    completion_callback=finalize_background_trace,
                )
            except Exception as e:
                self._trace_manager.complete(trace_node.agent_id, "failed")
                return ToolResult(
                    output=f"Sub-agent launch failed: {e}", is_error=True,
                )
            return ToolResult(
                output=f"Sub-agent launched in background.\n"
                f"Task ID: {task_id}\n"
                f"Agent: {agent_name}\n"
                f"Type: {definition.agent_type}\n"
                f"The system will notify automatically when it completes.\n"
                f"Do NOT wait, sleep, or poll. Report the task ID to the user and move on.",
            )

        # 前台同步执行
        request_usages: list[tuple[int, int]] = []
        runtime_manifests: list[dict[str, Any]] = []
        child_tool_call_count = 0

        def capture_usage(event: dict[str, Any]) -> None:
            nonlocal child_tool_call_count
            if _capture_foreground_child_event(
                event, request_usages, runtime_manifests,
            ):
                child_tool_call_count += 1

        result_text = ""
        run_error: Exception | None = None
        try:
            if is_fork:
                result_text = await sub_agent.run_to_completion(
                    "", conversation, event_callback=capture_usage,
                )
            else:
                result_text = await sub_agent.run_to_completion(
                    p.prompt, event_callback=capture_usage,
                )
        except Exception as e:
            run_error = e

        _update_child_trace(
            self._trace_manager, trace_node.agent_id, sub_agent,
            request_usages, runtime_manifests, child_tool_call_count,
        )
        self._trace_manager.complete(
            trace_node.agent_id, "failed" if run_error is not None else "completed",
        )

        if run_error is not None:
            return ToolResult(output=f"Sub-agent failed: {run_error}", is_error=True)

        return ToolResult(output=result_text or "(sub-agent returned no output)")

    async def _execute_as_teammate(self, p: AgentToolParams) -> ToolResult:
        if self._team_manager is None:
            return ToolResult(output="TeamManager not configured.", is_error=True)
        if self._worktree_manager is None:
            return ToolResult(output="WorktreeManager not configured for team spawn.", is_error=True)

        from codepacex.agents.fork import ForkError, build_forked_messages
        from codepacex.agents.parser import AgentDef
        from codepacex.agents.tool_filter import build_teammate_tools
        from codepacex.agent import Agent as AgentClass
        from codepacex.conversation import ConversationManager
        from codepacex.teams.models import BackendType, TeammateInfo
        from codepacex.teams.registry import AgentNameRegistry

        team = self._team_manager.get_team(p.team_name)
        if team is None:
            return ToolResult(output=f"Team '{p.team_name}' not found. Create it first with TeamCreate.", is_error=True)

        base_name = p.name or p.subagent_type or "worker"
        existing_names = {m.name for m in team.members}
        teammate_name = base_name
        if teammate_name in existing_names:
            counter = 2
            while f"{base_name}-{counter}" in existing_names:
                counter += 1
            teammate_name = f"{base_name}-{counter}"

        # 1. 加载 agent 定义
        definition: AgentDef
        conversation: ConversationManager | None = None
        is_fork = False

        if p.subagent_type:
            defn = self._agent_loader.get(p.subagent_type)
            if defn is None:
                return ToolResult(
                    output=f"Unknown agent type: '{p.subagent_type}'. "
                    f"Available: {', '.join(t for t, _ in self._agent_loader.list_agents())}",
                    is_error=True,
                )
            definition = defn
        else:
            if self._enable_fork:
                try:
                    parent_conv = getattr(self._parent_agent, '_current_conversation', None)
                    if parent_conv is None:
                        return ToolResult(output="Cannot fork: no active conversation.", is_error=True)
                    conversation = build_forked_messages(parent_conv, p.prompt)
                    is_fork = True
                except ForkError as e:
                    return ToolResult(output=str(e), is_error=True)

            definition = AgentDef(
                agent_type="teammate",
                when_to_use="Team member",
                system_prompt="",
                disallowed_tools=[],
                model="inherit",
                max_turns=self._parent_agent.max_iterations,
                permission_mode="bypassPermissions",
                source="builtin",
            )

        # 2. 创建 worktree
        wt_name = f"team-{p.team_name}/{teammate_name}"
        try:
            wt = await self._worktree_manager.create(wt_name, "HEAD")
        except Exception as e:
            return ToolResult(output=f"Failed to create worktree for teammate: {e}", is_error=True)

        # 3. 选择 LLM
        client = self._select_llm(p, definition)

        # 4. 检测后端类型
        backend = self._team_manager.detect_backend()

        # 5. 构建队友的工具集
        trace_node = self._trace_manager.create(
            agent_type=definition.agent_type,
            parent_id=self._parent_agent.agent_id,
            trace_id=self._parent_agent.trace_id or self._parent_agent.agent_id,
        )
        agent_id = trace_node.agent_id

        _has_full = getattr(self._parent_agent, '_full_registry', None) is not None
        full_registry = getattr(self._parent_agent, '_full_registry', None) or self._parent_agent.registry
        _full_tools = [t.name for t in full_registry.list_tools()]
        log.info(
            "[teammate] has_full_registry=%s full_tools=%d names=%s backend=%s def_tools=%s def_disallowed=%s",
            _has_full, len(_full_tools), _full_tools,
            backend.value,
            getattr(definition, 'tools', []),
            getattr(definition, 'disallowed_tools', []),
        )
        teammate_registry = build_teammate_tools(
            parent_registry=full_registry,
            team_manager=self._team_manager,
            team_name=p.team_name,
            agent_id=agent_id,
            agent_name=teammate_name,
            backend_type=backend.value,
            definition=definition,
        )
        _tm_tools = [t.name for t in teammate_registry.list_tools()]
        log.info("[teammate] result_tools=%d names=%s", len(_tm_tools), _tm_tools)

        # 6. 创建子 agent 并附加队友专属指令
        instructions = (definition.system_prompt or "") + TEAMMATE_ADDENDUM

        checker = _child_permission_checker(
            self._parent_agent,
            definition,
            work_dir=wt.path,
            permission_mode_override="bypassPermissions",
        )

        sub_agent = AgentClass(
            client=client,
            registry=teammate_registry,
            protocol=self._parent_agent.protocol,
            work_dir=wt.path,
            max_iterations=definition.max_turns,
            permission_checker=checker,
            context_window=self._parent_agent.context_window,
            instructions_content=instructions,
            hook_engine=self._parent_agent.hook_engine,
            experiment_profile=self._parent_agent.experiment_profile,
        )
        sub_agent.parent_id = self._parent_agent.agent_id
        sub_agent.trace_id = self._parent_agent.trace_id or self._parent_agent.agent_id
        sub_agent.agent_id = agent_id
        sub_agent.team_name = p.team_name
        sub_agent._team_manager = self._team_manager

        # 7. 注册名称和成员信息
        AgentNameRegistry.instance().register(teammate_name, agent_id)

        member = TeammateInfo(
            name=teammate_name,
            agent_id=agent_id,
            agent_type=definition.agent_type,
            model=p.model or definition.model,
            worktree_path=wt.path,
            backend_type=backend.value,
            is_active=True,
        )
        self._team_manager.register_member(p.team_name, member)

        # 8. 按后端类型启动队友
        if backend in (BackendType.TMUX, BackendType.ITERM2):
            return self._spawn_pane_teammate(
                p, team, member, backend, wt, agent_id, teammate_name
            )

        # 进程内模式：直接用 task_manager 执行并通知结果
        capture_child_event, finalize_background_trace = (
            self._background_child_trace_callbacks(trace_node.agent_id, sub_agent)
        )
        try:
            task_id = self._task_manager.launch(
                agent=sub_agent,
                task="" if is_fork else p.prompt,
                name=teammate_name,
                fork_conversation=conversation if is_fork else None,
                event_callback=capture_child_event,
                completion_callback=finalize_background_trace,
            )
        except Exception as e:
            self._trace_manager.complete(trace_node.agent_id, "failed")
            return ToolResult(
                output=f"Teammate launch failed: {e}", is_error=True,
            )

        return ToolResult(
            output=(
                f"Teammate '{teammate_name}' spawned in team '{p.team_name}'.\n"
                f"Agent ID: {agent_id}\n"
                f"Backend: {backend.value}\n"
                f"Worktree: {wt.path}\n"
                f"Task ID: {task_id}\n"
                f"The system will notify when it completes."
            )
        )


    def _spawn_pane_teammate(
        self, p: Any, team: Any, member: Any, backend: Any, wt: Any,
        agent_id: str, teammate_name: str,
    ) -> ToolResult:
        from codepacex.teams.models import BackendType

        mailbox = self._team_manager.get_mailbox(p.team_name)
        mailbox_dir = str(mailbox._base_dir) if mailbox else ""

        try:
            if backend == BackendType.TMUX:
                from codepacex.teams.spawn_tmux import spawn_tmux_teammate
                pane_info = spawn_tmux_teammate(
                    team_name=p.team_name,
                    teammate_name=teammate_name,
                    worktree_path=wt.path,
                    prompt=p.prompt,
                    agent_type=p.subagent_type or "",
                    model=p.model or "",
                    mailbox_dir=mailbox_dir,
                )
                self._team_manager.register_pane_id(agent_id, pane_info.pane_id)
            elif backend == BackendType.ITERM2:
                from codepacex.teams.spawn_iterm2 import spawn_iterm2_teammate
                pane_info = spawn_iterm2_teammate(
                    team_name=p.team_name,
                    teammate_name=teammate_name,
                    worktree_path=wt.path,
                    prompt=p.prompt,
                    agent_type=p.subagent_type or "",
                    model=p.model or "",
                    mailbox_dir=mailbox_dir,
                )
        except Exception as e:
            log.warning("Pane spawn failed, falling back to in-process: %s", e)
            return ToolResult(
                output=f"Pane spawn failed ({e}), teammate not started. Retry or set teammate_mode to in-process.",
                is_error=True,
            )

        return ToolResult(
            output=(
                f"Teammate '{teammate_name}' spawned in team '{p.team_name}'.\n"
                f"Agent ID: {agent_id}\n"
                f"Backend: {backend.value} (pane)\n"
                f"Worktree: {wt.path}\n"
                f"The teammate is running in an independent process."
            )
        )


    def _select_llm(
        self,
        params: AgentToolParams,
        definition: AgentDef,
    ) -> LLMClient:
        from codepacex.agents.parser import AgentDef

        model_override = params.model or (
            definition.model if definition.model != "inherit" else None
        )

        if model_override and model_override != "inherit":
            client = self._create_client_for_model(model_override)
            if client is not None:
                return client

        return self._parent_agent.client


    async def _execute_with_worktree(self, p: AgentToolParams) -> ToolResult:
        if self._worktree_manager is None:
            return ToolResult(
                output="Worktree isolation is not available: WorktreeManager not configured.",
                is_error=True,
            )

        from codepacex.agents.parser import AgentDef
        from codepacex.agents.tool_filter import resolve_agent_tools
        from codepacex.agent import Agent as AgentClass
        from codepacex.conversation import ConversationManager
        from codepacex.worktree.integration import (
            build_worktree_notice,
            generate_worktree_name,
        )

        definition: AgentDef | None = None
        if p.subagent_type:
            definition = self._agent_loader.get(p.subagent_type)
            if definition is None:
                return ToolResult(
                    output=f"Unknown agent type: '{p.subagent_type}'. "
                    f"Available types: {', '.join(t for t, _ in self._agent_loader.list_agents())}",
                    is_error=True,
                )
        else:
            definition = AgentDef(
                agent_type="worktree-agent",
                when_to_use="Isolated worktree agent",
                system_prompt="",
                disallowed_tools=[],
                model="inherit",
                max_turns=self._parent_agent.max_iterations,
                permission_mode="bypassPermissions",
                source="builtin",
            )

        wt_name = generate_worktree_name()
        try:
            wt = await self._worktree_manager.create(wt_name, "HEAD")
        except Exception as e:
            return ToolResult(
                output=f"Failed to create worktree: {e}",
                is_error=True,
            )

        notice = build_worktree_notice(self._parent_agent.work_dir, wt.path)
        task = notice + "\n\n" + p.prompt

        client = self._select_llm(p, definition)

        _base_registry = getattr(self._parent_agent, '_full_registry', None) or self._parent_agent.registry
        filtered_registry = resolve_agent_tools(
            _base_registry, definition, False
        )

        checker = _child_permission_checker(
            self._parent_agent, definition, work_dir=wt.path,
        )

        sub_agent = AgentClass(
            client=client,
            registry=filtered_registry,
            protocol=self._parent_agent.protocol,
            work_dir=wt.path,
            max_iterations=definition.max_turns,
            permission_checker=checker,
            context_window=self._parent_agent.context_window,
            instructions_content=definition.system_prompt,
            hook_engine=self._parent_agent.hook_engine,
            experiment_profile=self._parent_agent.experiment_profile,
        )
        sub_agent.parent_id = self._parent_agent.agent_id
        sub_agent.trace_id = self._parent_agent.trace_id or self._parent_agent.agent_id

        trace_node = self._trace_manager.create(
            agent_type=definition.agent_type,
            parent_id=self._parent_agent.agent_id,
            trace_id=sub_agent.trace_id,
        )
        sub_agent.agent_id = trace_node.agent_id

        request_usages: list[tuple[int, int]] = []
        runtime_manifests: list[dict[str, Any]] = []
        child_tool_call_count = 0

        def capture_child_event(event: dict[str, Any]) -> None:
            nonlocal child_tool_call_count
            if _capture_foreground_child_event(
                event, request_usages, runtime_manifests,
            ):
                child_tool_call_count += 1

        result_text = ""
        run_error: Exception | None = None
        try:
            result_text = await sub_agent.run_to_completion(
                task, event_callback=capture_child_event,
            )
        except Exception as e:
            run_error = e

        # Child events may precede a timeout or failure.  Retain their trace
        # evidence before cleanup, while keeping request usage on this existing
        # single summary path (never the ledger) to avoid duplicate accounting.
        _update_child_trace(
            self._trace_manager, trace_node.agent_id, sub_agent,
            request_usages, runtime_manifests, child_tool_call_count,
        )
        self._trace_manager.complete(
            trace_node.agent_id, "failed" if run_error is not None else "completed",
        )

        cleanup_error: Exception | None = None
        cleanup = None
        try:
            cleanup = await self._worktree_manager.auto_cleanup(wt_name, wt.head_commit)
        except Exception as e:
            cleanup_error = e

        if run_error is not None:
            output = f"Sub-agent in worktree failed: {run_error}"
            if cleanup is not None and cleanup.kept:
                output += f"\n[Worktree preserved at {cleanup.path}, branch {cleanup.branch}]"
            return ToolResult(output=output, is_error=True)
        if cleanup_error is not None:
            return ToolResult(
                output=f"Sub-agent worktree cleanup failed: {cleanup_error}",
                is_error=True,
            )

        assert cleanup is not None
        if cleanup.kept:
            result_text = (result_text or "") + (
                f"\n[Worktree preserved at {cleanup.path}, branch {cleanup.branch}]"
            )

        return ToolResult(output=result_text or "(sub-agent returned no output)")


    def _create_client_for_model(self, model_alias: str) -> LLMClient | None:
        if self._provider_config is None:
            return None

        from codepacex.client import create_client
        from codepacex.config import ProviderConfig

        model_map = {
            "haiku": "claude-haiku-4-5-20251001",
            "sonnet": "claude-sonnet-4-6-20250514",
            "opus": "claude-opus-4-6-20250514",
        }
        model_id = model_map.get(model_alias, model_alias)

        config = ProviderConfig(
            name=f"sub-{model_alias}",
            protocol=self._provider_config.protocol,
            base_url=self._provider_config.base_url,
            model=model_id,
            api_key=self._provider_config.api_key,
            api_key_env=getattr(self._provider_config, "api_key_env", ""),
            context_window=self._provider_config.context_window,
        )
        try:
            return create_client(config)
        except Exception:
            return None

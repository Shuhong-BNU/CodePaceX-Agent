"""提供 CodePaceX 的命令行入口与运行模式调度能力。

主要包含核心数据结构与执行流程。该模块由 CodePaceX 运行时调用，并维护状态一致性和异常传播。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

import yaml

from codepacex.config import ConfigError, load_config
from codepacex.experiments import (
    AgentMode,
    ExperimentProfile,
    PermissionStrategy,
    ToolLoading,
    load_experiment_profile,
)
from codepacex.hooks import HookConfigError, HookEngine, load_hooks
from codepacex.permissions import PermissionMode


def _deny_noninteractive_permission(event) -> str:
    """Resolve a CLI permission prompt without granting unattended access."""
    from codepacex.agent import PermissionResponse

    if not event.future.done():
        event.future.set_result(PermissionResponse.DENY)
    return f"Non-interactive mode denied permission for {event.tool_name}: {event.description}"


# 核心实现
def main() -> None:
    # 先确保 .codepacex/ 目录存在，否则下面写 debug.log 会因目录不存在而崩溃
    Path(".codepacex").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(message)s",
        filename=".codepacex/debug.log",
        filemode="w",
    )

    parser = argparse.ArgumentParser(prog="codepacex", description="CodePaceX AI coding assistant")
    parser.add_argument(
        "--mode",
        choices=[m.value for m in PermissionMode],
        default=None,
        help="Permission mode (overrides config.yaml)",
    )
    parser.add_argument(
        "-p",
        metavar="PROMPT",
        default=None,
        help="Run non-interactively: execute the prompt and print the result to stdout",
    )
    parser.add_argument(
        "--output-format",
        choices=["text", "stream-json"],
        default="text",
        help="Output format for -p mode: 'text' (default) prints final text, 'stream-json' emits NDJSON events",
    )
    parser.add_argument(
        "--experiment-profile",
        type=Path,
        default=None,
        help="Validated benchmark runtime profile (only valid with -p)",
    )
    parser.add_argument(
        "--remote",
        action="store_true",
        default=False,
        help="Start in remote mode: WebSocket server on 0.0.0.0:18888 with browser UI",
    )
    args = parser.parse_args()

    try:
        config = load_config()
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    mode_str = args.mode if args.mode else config.permission_mode
    permission_mode = PermissionMode(mode_str)

    try:
        hooks = load_hooks(config.raw_hooks)
    except HookConfigError as e:
        print(f"Hook config error: {e}", file=sys.stderr)
        sys.exit(1)

    hook_engine = HookEngine(hooks) if hooks else None

    experiment_profile: ExperimentProfile | None = None
    if args.experiment_profile is not None:
        if args.p is None:
            print("Error: --experiment-profile is only valid with -p", file=sys.stderr)
            sys.exit(1)
        try:
            experiment_profile = load_experiment_profile(args.experiment_profile)
        except (OSError, ValueError, yaml.YAMLError) as e:
            print(f"Experiment profile error: {e}", file=sys.stderr)
            sys.exit(1)

    if args.p is not None:
        output_format = getattr(args, "output_format", "text")
        asyncio.run(_run_prompt(
            config, permission_mode, hook_engine, args.p, output_format,
            experiment_profile=experiment_profile,
        ))
        return

    # Remote 模式：启动 WebSocket 服务器，浏览器访问 http://localhost:18888
    if args.remote:
        from codepacex.remote import RemoteServer

        server = RemoteServer(
            providers=config.providers,
            fallback=config.fallback,
            mcp_servers=config.mcp_servers,
            hook_engine=hook_engine,
        )
        asyncio.run(server.run())
        return

    from codepacex.app import CodePaceXApp
    from codepacex.driver import NoAltScreenDriver

    app = CodePaceXApp(
        providers=config.providers,
        fallback=config.fallback,
        permission_mode=permission_mode,
        mcp_servers=config.mcp_servers,
        hook_engine=hook_engine,
        enable_fork=config.enable_fork,
        enable_verification_agent=config.enable_verification_agent,
        worktree_config=config.worktree,
        teammate_mode=config.teammate_mode,
        enable_coordinator_mode=config.enable_coordinator_mode,
        sandbox_config=config.sandbox,
        driver_class=NoAltScreenDriver,
    )
    app.run()


async def _run_prompt(
    config,
    permission_mode,
    hook_engine,
    prompt: str,
    output_format: str = "text",
    *,
    experiment_profile: ExperimentProfile | None = None,
) -> None:
    mcp_manager = None

    def set_mcp_manager(manager) -> None:
        nonlocal mcp_manager
        mcp_manager = manager

    try:
        await _run_prompt_impl(
            config,
            permission_mode,
            hook_engine,
            prompt,
            output_format,
            experiment_profile=experiment_profile,
            _set_mcp_manager=set_mcp_manager,
        )
    finally:
        if mcp_manager is not None:
            try:
                await mcp_manager.shutdown()
            except Exception:
                logging.getLogger(__name__).debug(
                    "Error shutting down MCP manager", exc_info=True,
                )


async def _run_prompt_impl(
    config,
    permission_mode,
    hook_engine,
    prompt: str,
    output_format: str = "text",
    *,
    experiment_profile: ExperimentProfile | None = None,
    _set_mcp_manager,
) -> None:
    from codepacex.agent import (
        Agent,
        CompactNotification,
        CompressionEvent,
        ErrorEvent,
        LoopComplete,
        PermissionRequest,
        PermissionDecisionEvent,
        RetryEvent,
        RuntimeManifestEvent,
        StreamText,
        ThinkingText,
        ToolResultEvent,
        ToolUseEvent,
        TurnComplete,
        UsageEvent,
    )
    from codepacex.client import create_client, resolve_context_window
    from codepacex.conversation import ConversationManager
    from codepacex.memory.instructions import load_instructions
    from codepacex.mcp import MCPManager
    from codepacex.permissions import (
        DangerousCommandDetector,
        PathSandbox,
        PermissionChecker,
        RuleEngine,
    )
    from codepacex.tools import create_default_registry
    from codepacex.sandbox import configure_bash_sandbox
    from codepacex.agents.loader import AgentLoader
    from codepacex.agents.task_manager import TaskManager
    from codepacex.agents.trace import TraceManager
    from codepacex.tools.agent_tool import AgentTool
    from codepacex.tools.impl.tool_search import ToolSearchTool
    from codepacex.tools.install_skill import InstallSkill
    from codepacex.teams.manager import TeamManager
    from codepacex.teams.models import BackendType
    from codepacex.tools.team_create import TeamCreateTool
    from codepacex.tools.team_delete import TeamDeleteTool
    from codepacex.worktree import WorktreeManager
    from codepacex.config import WorktreeConfig

    is_json = output_format == "stream-json"

    def emit_json(obj: dict) -> None:
        """输出一行 NDJSON 到 stdout"""
        print(json.dumps(obj, ensure_ascii=False), flush=True)

    provider = config.providers[0]
    client = create_client(
        provider, max_retries=0 if experiment_profile is not None else None,
    )
    # 第 2 层：尽力从 provider 自动拉取模型的 context window（缓存在 provider 上）。
    # 不会抛异常或阻塞启动；失败则退化到映射表。
    await resolve_context_window(provider)
    work_dir = os.getcwd()
    home = Path.home()

    registry = create_default_registry()
    backend, _sandbox_config, sandbox_state = configure_bash_sandbox(
        registry,
        enabled=config.sandbox.enabled,
        network_enabled=config.sandbox.network_enabled,
        work_dir=work_dir,
    )
    checker = PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=PathSandbox(work_dir),
        rule_engine=RuleEngine(
            user_rules_path=home / ".codepacex" / "permissions.yaml",
            project_rules_path=Path(work_dir) / ".codepacex" / "permissions.yaml",
            local_rules_path=Path(work_dir) / ".codepacex" / "permissions.local.yaml",
        ),
        mode=permission_mode,
        sandbox_enabled=bool(
            (
                config.sandbox.auto_allow
                or (
                    experiment_profile is not None
                    and experiment_profile.permission_strategy
                    is PermissionStrategy.SANDBOX_AUTO_ALLOW
                )
            )
            and backend is not None and sandbox_state == "available"
        ),
        session_allow_all=bool(
            experiment_profile is not None
            and experiment_profile.permission_strategy
            is PermissionStrategy.SESSION_ALLOW
        ),
    )

    mcp_manager: MCPManager | None = None
    mcp_instructions: list[str] = []
    if config.mcp_servers:
        mcp_manager = MCPManager()
        _set_mcp_manager(mcp_manager)
        mcp_manager.load_configs(config.mcp_servers)
        connected = await mcp_manager.register_all_tools(
            registry,
            defer_tools=(
                experiment_profile is None
                or experiment_profile.tool_loading is ToolLoading.DEFERRED
            ),
        )
        if experiment_profile is not None and connected.errors:
            raise RuntimeError(
                "benchmark MCP initialization failed: " + "; ".join(connected.errors)
            )
        mcp_instructions = [
            server.instructions for server in connected.servers if server.instructions
        ]

    instructions = load_instructions(work_dir)
    if mcp_instructions:
        instructions = "\n\n".join([instructions, *mcp_instructions]).strip()
    registry.register(ToolSearchTool(registry, protocol=provider.protocol))
    registry.register(InstallSkill())

    agent = Agent(
        client=client,
        registry=registry,
        protocol=provider.protocol,
        work_dir=work_dir,
        permission_checker=checker,
        context_window=provider.get_context_window(),
        instructions_content=instructions,
        hook_engine=hook_engine,
        active_provider=provider,
        providers=config.providers,
        fallback=config.fallback,
        experiment_profile=experiment_profile,
    )

    wt_cfg = config.worktree or WorktreeConfig()
    wt_manager = WorktreeManager(
        repo_root=work_dir,
        symlink_directories=wt_cfg.symlink_directories,
    )
    trace_manager = TraceManager()
    task_manager = TaskManager()
    agent_loader = AgentLoader(work_dir, enable_verification=config.enable_verification_agent)
    agent_loader.load_all()
    team_manager = TeamManager(worktree_manager=wt_manager, trace_manager=trace_manager)

    multi_agent_enabled = (
        experiment_profile is None
        or experiment_profile.agent_mode is AgentMode.MULTI
    )
    if multi_agent_enabled:
        agent_tool = AgentTool(
            agent_loader=agent_loader,
            task_manager=task_manager,
            trace_manager=trace_manager,
            parent_agent=agent,
            enable_fork=config.enable_fork,
            provider_config=provider,
            worktree_manager=wt_manager,
            team_manager=team_manager,
        )
        registry.register(agent_tool)
        registry.register(TeamCreateTool(
            team_manager=team_manager,
            parent_agent=agent,
            teammate_mode="in-process",
            is_interactive=False,
            enable_coordinator_mode=config.enable_coordinator_mode,
        ))
        registry.register(TeamDeleteTool(team_manager=team_manager, parent_agent=agent))

    def drain_notifications() -> list[str]:
        notes: list[str] = []
        for t in task_manager.poll_completed():
            notes.append(
                f"<task-notification>\n<task_id>{t.id}</task_id>\n"
                f"<status>{t.status}</status>\n<result>{t.result}</result>\n"
                f"</task-notification>"
            )
        notes.extend(team_manager.drain_lead_mailbox())
        return notes

    def drain_mailbox_only() -> list[str]:
        return team_manager.drain_lead_mailbox()

    agent.notification_fn = drain_mailbox_only

    def emit_agent_experiment_summary() -> None:
        if not is_json or experiment_profile is None:
            return
        emit_json({
            "type": "experiment_agent_summary",
            "agent_mode": experiment_profile.agent_mode.value,
            "maximum_workers": 3,
            **trace_manager.benchmark_summary(agent.agent_id),
        })

    # 使用事件驱动的 agent.run()，支持 text 和 stream-json 两种输出格式
    conv = ConversationManager()
    conv.add_user_message(prompt)

    start = time.monotonic()
    text_buf = ""
    total_input = 0
    total_output = 0
    tool_calls: list[dict] = []

    async for event in agent.run(conv):
        if isinstance(event, StreamText):
            text_buf += event.text
            if is_json:
                emit_json({"type": "assistant", "text": event.text})

        elif isinstance(event, ThinkingText):
            if is_json:
                emit_json({"type": "thinking", "text": event.text})

        elif isinstance(event, ToolUseEvent):
            tool_calls.append({"name": event.tool_name, "is_error": False})
            if is_json:
                emit_json({
                    "type": "tool_use",
                    "tool_name": event.tool_name,
                    "tool_id": event.tool_id,
                    "args": event.arguments,
                })

        elif isinstance(event, ToolResultEvent):
            # 回填最后一个同名 tool_call 的 is_error
            if tool_calls:
                tool_calls[-1]["is_error"] = event.is_error
            if is_json:
                emit_json({
                    "type": "tool_result",
                    "tool_name": event.tool_name,
                    "tool_id": event.tool_id,
                    "output": event.output,
                    "is_error": event.is_error,
                    "elapsed": round(event.elapsed, 3),
                })

        elif isinstance(event, UsageEvent):
            total_input = event.input_tokens
            total_output = event.output_tokens
            if is_json:
                emit_json({
                    "type": "usage",
                    "input_tokens": event.input_tokens,
                    "output_tokens": event.output_tokens,
                    "request_input_tokens": event.request_input_tokens,
                    "request_output_tokens": event.request_output_tokens,
                    "provider_usage": event.provider_usage,
                    "provider": event.provider,
                    "model_id": event.model_id,
                    "request_index": event.request_index,
                })

        elif isinstance(event, RuntimeManifestEvent):
            if is_json:
                emit_json({
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
                })

        elif isinstance(event, PermissionDecisionEvent):
            if is_json:
                emit_json({
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
                })

        elif isinstance(event, CompressionEvent):
            if is_json:
                emit_json({
                    "type": "compression",
                    "trigger": event.trigger,
                    "success": event.success,
                    "tokens_before": event.tokens_before,
                    "tokens_after": event.tokens_after,
                    "attachment_count": event.attachment_count,
                    "error_category": event.error_category,
                })

        elif isinstance(event, TurnComplete):
            if is_json:
                emit_json({"type": "turn_complete", "turn": event.turn})

        elif isinstance(event, LoopComplete):
            # 最终结果：stream-json 输出 result 行，text 模式直接打印文本
            elapsed_ms = int((time.monotonic() - start) * 1000)
            if is_json:
                emit_json({
                    "type": "result",
                    "result": text_buf,
                    "duration_ms": elapsed_ms,
                    "num_turns": event.total_turns,
                    "tool_calls": tool_calls,
                    "usage": {
                        "input_tokens": total_input,
                        "output_tokens": total_output,
                    },
                    "stop_reason": "end_turn",
                })
            else:
                print(text_buf, end="", flush=True)
            break

        elif isinstance(event, ErrorEvent):
            if is_json:
                emit_json({"type": "error", "message": event.message})
            else:
                print(f"Error: {event.message}", file=sys.stderr, flush=True)

        elif isinstance(event, CompactNotification):
            if is_json:
                emit_json({"type": "compact", "message": event.message})

        elif isinstance(event, RetryEvent):
            if is_json:
                emit_json({"type": "retry", "reason": event.reason})

        elif isinstance(event, PermissionRequest):
            # -p 无法向用户确认；所有 ask 必须 fail closed。
            denial = _deny_noninteractive_permission(event)
            if is_json:
                emit_json({"type": "error", "message": denial})
            else:
                print(f"Error: {denial}", file=sys.stderr, flush=True)

    # 如果有 team 在运行，轮询等待 teammate 完成
    if not team_manager._teams:
        emit_agent_experiment_summary()
        return

    for i in range(90):
        await asyncio.sleep(2)
        running = {k: not t.done() for k, t in task_manager._async_tasks.items()}
        completed_ids = [t.id for t in task_manager._tasks.values() if t.status != "running"]
        print(f"[poll {i}] running={running} completed={completed_ids} teams={list(team_manager._teams.keys())} queue_size={task_manager._notify_queue.qsize()}", file=sys.stderr, flush=True)
        notes = drain_notifications()
        if not notes:
            has_running = any(v for v in running.values())
            if not has_running:
                print(f"[poll {i}] no running tasks, breaking", file=sys.stderr, flush=True)
                break
            continue
        for note in notes:
            conv.add_system_reminder(note)
        # 后续 team 轮询仍用 run_to_completion，避免重复事件循环
        last_result = await agent.run_to_completion(
            "Teammate notifications received. Process them and continue.", conv
        )
        if is_json:
            emit_json({"type": "assistant", "text": last_result})
        else:
            print(last_result, flush=True)

    emit_agent_experiment_summary()


if __name__ == "__main__":
    main()

"""提供 CodePaceX 的 compact能力。

主要包含斜杠命令的解析、注册、补全与处理器。该模块由终端应用的命令分发层调用，并维护命令参数和 UI 状态一致性。
"""

from __future__ import annotations

from codepacex.commands.registry import Command, CommandContext, CommandType


# 核心实现
async def handle_compact(ctx: CommandContext) -> None:
    if ctx.agent is None:
        ctx.ui.add_system_message("Agent 未初始化")
        return


    input_tokens, _ = ctx.ui.get_token_count()
    if input_tokens < 5000:
        ctx.ui.add_system_message(f"当前 token 数 {input_tokens:,}，无需压缩")
        return

    from codepacex.agent import CompactNotification, ErrorEvent


    result = await ctx.agent.manual_compact(ctx.conversation)
    if isinstance(result, CompactNotification):
        # 持久化 compact_boundary，使后续 resume 可重建压缩后的状态。
        # manual_compact 已重写了 ctx.conversation；下一次 _send_message
        # 会重新捕获 history_cursor，所以这里无需手动重置。
        if ctx.session is not None and result.boundary is not None:
            from codepacex.memory.session import make_compact_boundary

            ctx.session.append_record(
                make_compact_boundary(result.boundary.summary, result.boundary.keep)
            )
        ctx.ui.add_system_message(result.message)
    elif isinstance(result, ErrorEvent):
        ctx.ui.add_system_message(f"压缩失败: {result.message}")


COMPACT_COMMAND = Command(
    name="compact",
    aliases=["c"],
    description="压缩上下文",
    usage="/compact [保留重点]",
    type=CommandType.LOCAL,
    handler=handle_compact,
)


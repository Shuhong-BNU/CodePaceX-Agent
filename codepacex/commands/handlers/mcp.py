"""提供 CodePaceX 的 mcp能力。

主要包含斜杠命令的解析、注册、补全与处理器。该模块由终端应用的命令分发层调用，并维护命令参数和 UI 状态一致性。
"""

from __future__ import annotations

from codepacex.commands.registry import Command, CommandContext, CommandType


# 核心实现
async def handle_mcp(ctx: CommandContext) -> None:
    app = ctx.ui
    info = getattr(app, "_mcp_server_info", "")
    if not info:
        ctx.ui.add_system_message("No MCP servers connected")
        return

    lines = ["MCP 状态", "─────────────"]
    lines.append(info)

    mcp_mgr = getattr(app, "mcp_manager", None)
    if mcp_mgr and hasattr(mcp_mgr, "_clients"):
        for name, client in mcp_mgr._clients.items():
            tool_names = [
                t.name for t in ctx.agent.registry.list_tools()
                if t.name.startswith(f"mcp__{name}__")
            ]
            lines.append(f"\n  {name}: {len(tool_names)} tools")
            for tn in tool_names[:10]:
                short = tn.replace(f"mcp__{name}__", "")
                lines.append(f"    - {short}")
            if len(tool_names) > 10:
                lines.append(f"    … and {len(tool_names) - 10} more")

    ctx.ui.add_system_message("\n".join(lines))


MCP_COMMAND = Command(
    name="mcp",
    aliases=[],
    description="显示 MCP 服务器状态",
    usage="/mcp",
    type=CommandType.LOCAL,
    handler=handle_mcp,
)

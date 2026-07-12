"""Interactive operating-system sandbox controls."""

from __future__ import annotations

from codepacex.commands.registry import Command, CommandContext, CommandType
from codepacex.sandbox import SandboxConfig, create_sandbox


def _status(ctx: CommandContext) -> str:
    bash = ctx.agent.registry.get("Bash") if ctx.agent else None
    backend = getattr(bash, "sandbox", None)
    config = getattr(bash, "sandbox_config", None)
    checker = getattr(ctx.agent, "permission_checker", None)
    if backend is None:
        detected = create_sandbox()
        availability = "unsupported" if detected is None else (
            "available" if detected.available() else "unavailable"
        )
        return "OS sandbox: disabled\nBackend: " + (
            type(detected).__name__ if detected else "none"
        ) + f" ({availability})"
    return (
        "OS sandbox: enabled\n"
        f"Backend: {type(backend).__name__} ({'available' if backend.available() else 'unavailable'})\n"
        f"Auto allow: {'yes' if checker and checker.sandbox_enabled else 'no'}\n"
        f"Network: {'enabled' if config and config.network_enabled else 'disabled'}"
    )


async def handle_sandbox(ctx: CommandContext) -> None:
    if ctx.agent is None:
        ctx.ui.add_system_message("Agent is not initialized")
        return
    arg = ctx.args.strip().lower()
    if not arg or arg == "status":
        ctx.ui.add_system_message(_status(ctx))
        return
    bash = ctx.agent.registry.get("Bash")
    if bash is None:
        ctx.ui.add_system_message("Bash tool is not registered")
        return
    if arg == "off":
        bash.sandbox = None
        bash.sandbox_config = None
        if ctx.agent.permission_checker:
            ctx.agent.permission_checker.sandbox_enabled = False
        ctx.ui.add_system_message("OS sandbox disabled")
        ctx.ui.refresh_status()
        return
    if arg not in {"on", "on-auto"}:
        ctx.ui.add_system_message("Usage: /sandbox [status|on|on-auto|off]")
        return
    backend = create_sandbox()
    if backend is None or not backend.available():
        ctx.ui.add_system_message("OS sandbox backend is unavailable; permissions remain unchanged")
        return
    work_dir = ctx.agent.work_dir
    bash.work_dir = work_dir
    bash.sandbox = backend
    bash.sandbox_config = SandboxConfig(
        allow_write=[work_dir, "/tmp"],
        deny_write=[f"{work_dir}/.codepacex/config.yaml", f"{work_dir}/.codepacex/config.local.yaml"],
    )
    if ctx.agent.permission_checker:
        ctx.agent.permission_checker.sandbox_enabled = arg == "on-auto"
    ctx.ui.add_system_message("OS sandbox enabled" + (" with auto allow" if arg == "on-auto" else ""))
    ctx.ui.refresh_status()


SANDBOX_COMMAND = Command(
    name="sandbox",
    description="Show or configure the OS command sandbox",
    usage="/sandbox [status|on|on-auto|off]",
    type=CommandType.LOCAL,
    handler=handle_sandbox,
)

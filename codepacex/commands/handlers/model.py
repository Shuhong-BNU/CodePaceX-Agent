"""提供 CodePaceX 的模型查看与会话内切换命令。"""

from __future__ import annotations

import inspect
from typing import Any

from codepacex.commands.registry import Command, CommandContext, CommandType


def _providers(ctx: CommandContext) -> list[Any]:
    return list(ctx.config.get("providers", []) if ctx.config else [])


def _current_provider(ctx: CommandContext) -> Any:
    getter = ctx.config.get("get_current_provider") if ctx.config else None
    if callable(getter):
        return getter()
    return ctx.config.get("current_provider") if ctx.config else None


def _provider_models(provider: Any) -> list[str]:
    models = list(getattr(provider, "models", []) or [])
    model = getattr(provider, "model", "")
    if not models and model:
        models = [model]
    return models


def _key_status(provider: Any) -> str:
    try:
        return "available" if provider.resolve_api_key() else "missing"
    except Exception:
        return "missing"


async def _call_switch(ctx: CommandContext, provider_name: str, model: str) -> tuple[bool, str]:
    switch_model = ctx.config.get("switch_model") if ctx.config else None
    if not callable(switch_model):
        return False, "当前界面不支持运行时模型切换。"
    result = switch_model(provider_name, model)
    if inspect.isawaitable(result):
        result = await result
    if isinstance(result, tuple) and len(result) == 2:
        return bool(result[0]), str(result[1])
    return True, str(result)


async def handle_model(ctx: CommandContext) -> None:
    args = ctx.args.strip()
    subcmd, _, rest = args.partition(" ")
    subcmd = subcmd.lower()

    if not subcmd or subcmd == "current":
        provider = _current_provider(ctx)
        if provider is None:
            ctx.ui.add_system_message("当前没有已选择的模型。")
            return
        lines = [
            "当前模型",
            "────────",
            f"Provider: {provider.name}",
            f"Protocol: {provider.protocol}",
            f"Model: {provider.model}",
            f"Base URL: {provider.base_url}",
        ]
        ctx.ui.add_system_message("\n".join(lines))
        return

    if subcmd == "list":
        current = _current_provider(ctx)
        lines = ["可用模型", "────────"]
        for provider in _providers(ctx):
            active_provider = (
                current is not None
                and getattr(current, "name", "") == provider.name
            )
            marker = "*" if active_provider else " "
            lines.append(
                f"{marker} {provider.name} ({provider.protocol}) "
                f"key: {_key_status(provider)}"
            )
            for model in _provider_models(provider):
                current_marker = (
                    " [current]"
                    if active_provider and getattr(current, "model", "") == model
                    else ""
                )
                lines.append(f"    - {model}{current_marker}")
        ctx.ui.add_system_message("\n".join(lines))
        return

    if subcmd == "use":
        target = rest.strip()
        if "/" not in target:
            ctx.ui.add_system_message("用法: /model use <provider>/<model>")
            return
        provider_name, model = target.split("/", 1)
        provider_name = provider_name.strip()
        model = model.strip()
        if not provider_name or not model:
            ctx.ui.add_system_message("用法: /model use <provider>/<model>")
            return

        provider = next((p for p in _providers(ctx) if p.name == provider_name), None)
        if provider is None:
            ctx.ui.add_system_message(f"未知 provider: {provider_name}")
            return
        models = _provider_models(provider)
        if model not in models:
            ctx.ui.add_system_message(
                f"未知模型: {provider_name}/{model}\n"
                f"可用模型: {', '.join(models) if models else '(none)'}"
            )
            return

        ok, message = await _call_switch(ctx, provider_name, model)
        ctx.ui.add_system_message(message)
        if ok:
            ctx.ui.refresh_status()
        return

    ctx.ui.add_system_message(
        "用法: /model [current|list|use <provider>/<model>]\n"
        "尚未实现: /model test、fallback。"
    )


MODEL_COMMAND = Command(
    name="model",
    aliases=[],
    description="查看或切换当前会话模型",
    usage="/model [current|list|use <provider>/<model>]",
    type=CommandType.LOCAL,
    handler=handle_model,
)

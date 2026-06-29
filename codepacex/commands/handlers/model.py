"""提供 CodePaceX 的模型查看与会话内切换命令。"""

from __future__ import annotations

import inspect
from typing import Any

from codepacex.commands.registry import Command, CommandContext, CommandType
from codepacex.model_fallback import parse_model_ref
from codepacex.model_test import ModelTestResult


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


def _fallback_refs(ctx: CommandContext) -> list[str]:
    return list(ctx.config.get("fallback", []) if ctx.config else [])


def _fallback_index(ctx: CommandContext) -> dict[tuple[str, str], int]:
    index: dict[tuple[str, str], int] = {}
    for pos, raw in enumerate(_fallback_refs(ctx), start=1):
        try:
            ref = parse_model_ref(raw)
        except ValueError:
            continue
        index.setdefault((ref.provider, ref.model), pos)
    return index


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


async def _call_test(
    ctx: CommandContext,
    provider_name: str | None,
    model: str | None,
) -> ModelTestResult | tuple[bool, str]:
    test_model = ctx.config.get("test_model") if ctx.config else None
    if not callable(test_model):
        return False, "当前界面不支持模型连通性测试。"
    result = test_model(provider_name, model)
    if inspect.isawaitable(result):
        result = await result
    return result


def _format_test_result(result: ModelTestResult) -> str:
    lines = [
        "模型测试",
        "────────",
        f"Provider: {result.provider}",
        f"Protocol: {result.protocol}",
        f"Model: {result.model}",
        f"Base URL: {result.base_url}",
        f"Key: {result.key_status}",
        f"Result: {'ok' if result.ok else 'failed'}",
        f"Reason: {result.reason}",
    ]
    if result.latency_ms is not None:
        lines.append(f"Latency: {result.latency_ms} ms")
    if result.suggestion:
        lines.append(f"Suggestion: {result.suggestion}")
    return "\n".join(lines)


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
        fallback = _fallback_refs(ctx)
        if fallback:
            lines.append(f"Fallback: configured, {len(fallback)} candidate(s)")
            lines.append("Fallback chain:")
            for i, item in enumerate(fallback, start=1):
                lines.append(f"  {i}. {item}")
        else:
            lines.append("Fallback: not configured")
        ctx.ui.add_system_message("\n".join(lines))
        return

    if subcmd == "list":
        current = _current_provider(ctx)
        fallback_index = _fallback_index(ctx)
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
                markers: list[str] = []
                if active_provider and getattr(current, "model", "") == model:
                    markers.append("current")
                fallback_pos = fallback_index.get((provider.name, model))
                if fallback_pos is not None:
                    markers.append(f"fallback #{fallback_pos}")
                suffix = " [" + "] [".join(markers) + "]" if markers else ""
                lines.append(f"    - {model}{suffix}")
        ctx.ui.add_system_message("\n".join(lines))
        return

    if subcmd == "test":
        target = rest.strip()
        provider_name: str | None = None
        model: str | None = None
        if target:
            if "/" not in target:
                ctx.ui.add_system_message("用法: /model test <provider>/<model>")
                return
            provider_name, model = target.split("/", 1)
            provider_name = provider_name.strip()
            model = model.strip()
            if not provider_name or not model:
                ctx.ui.add_system_message("用法: /model test <provider>/<model>")
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

        result = await _call_test(ctx, provider_name, model)
        if isinstance(result, ModelTestResult):
            ctx.ui.add_system_message(_format_test_result(result))
        elif isinstance(result, tuple) and len(result) == 2:
            ctx.ui.add_system_message(str(result[1]))
        else:
            ctx.ui.add_system_message(str(result))
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
        "用法: /model [current|list|test [<provider>/<model>]|use <provider>/<model>]\n"
        "fallback 会在请求失败时按配置临时尝试备用模型。"
    )


MODEL_COMMAND = Command(
    name="model",
    aliases=[],
    description="查看或切换当前会话模型",
    usage="/model [current|list|test [<provider>/<model>]|use <provider>/<model>]",
    type=CommandType.LOCAL,
    handler=handle_model,
)

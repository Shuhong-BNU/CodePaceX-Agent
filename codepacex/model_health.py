"""Batch model health checks built on top of single-model tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, Sequence

from codepacex.config import ProviderConfig
from codepacex.model_fallback import parse_model_ref
from codepacex.model_test import ModelTestResult, ModelTestStatus, test_provider_model


class ModelHealthScope(str, Enum):
    ALL = "all"
    PROVIDER = "provider"
    FALLBACK = "fallback"


class ModelHealthError(Exception):
    pass


@dataclass(frozen=True)
class ModelHealthTarget:
    provider: ProviderConfig
    ref: str


@dataclass(frozen=True)
class ModelHealthItem:
    ref: str
    result: ModelTestResult

    @property
    def group(self) -> str:
        if self.result.status == ModelTestStatus.OK:
            return "ok"
        if self.result.status == ModelTestStatus.MISSING_KEY:
            return "skipped"
        return "failed"


@dataclass
class ModelHealthResult:
    scope: ModelHealthScope
    scope_label: str
    items: list[ModelHealthItem] = field(default_factory=list)
    note: str = ""

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def ok_items(self) -> list[ModelHealthItem]:
        return [item for item in self.items if item.group == "ok"]

    @property
    def failed_items(self) -> list[ModelHealthItem]:
        return [item for item in self.items if item.group == "failed"]

    @property
    def skipped_items(self) -> list[ModelHealthItem]:
        return [item for item in self.items if item.group == "skipped"]


ModelTester = Callable[[ProviderConfig], Awaitable[ModelTestResult]]


def build_model_health_targets(
    scope: ModelHealthScope,
    providers: Sequence[ProviderConfig],
    fallback: Sequence[str],
    *,
    provider_name: str | None = None,
) -> tuple[str, list[ModelHealthTarget], str]:
    if scope == ModelHealthScope.ALL:
        return "all configured models", _all_targets(providers), ""

    if scope == ModelHealthScope.PROVIDER:
        if not provider_name:
            raise ModelHealthError("用法: /model test --provider <provider>")
        provider = _find_provider(providers, provider_name)
        if provider is None:
            raise ModelHealthError(f"未知 provider: {provider_name}")
        return (
            f"provider {provider.name}",
            [_target(provider, model) for model in _models_for_provider(provider)],
            "",
        )

    if scope == ModelHealthScope.FALLBACK:
        if not fallback:
            return "fallback chain", [], "Fallback: not configured"
        return "fallback chain", _fallback_targets(providers, fallback), ""

    raise ModelHealthError(f"Unknown model health scope: {scope}")


async def run_model_health_check(
    scope: ModelHealthScope,
    providers: Sequence[ProviderConfig],
    fallback: Sequence[str],
    *,
    provider_name: str | None = None,
    tester: ModelTester = test_provider_model,
) -> ModelHealthResult:
    scope_label, targets, note = build_model_health_targets(
        scope,
        providers,
        fallback,
        provider_name=provider_name,
    )
    result = ModelHealthResult(scope=scope, scope_label=scope_label, note=note)
    for target in targets:
        item_result = await tester(target.provider)
        result.items.append(ModelHealthItem(ref=target.ref, result=item_result))
    return result


def _all_targets(providers: Sequence[ProviderConfig]) -> list[ModelHealthTarget]:
    targets: list[ModelHealthTarget] = []
    seen: set[tuple[str, str]] = set()
    for provider in providers:
        for model in _models_for_provider(provider):
            key = (provider.name, model)
            if key in seen:
                continue
            seen.add(key)
            targets.append(_target(provider, model))
    return targets


def _fallback_targets(
    providers: Sequence[ProviderConfig],
    fallback: Sequence[str],
) -> list[ModelHealthTarget]:
    targets: list[ModelHealthTarget] = []
    by_name = {provider.name: provider for provider in providers}
    for raw in fallback:
        ref = parse_model_ref(raw)
        provider = by_name.get(ref.provider)
        if provider is None:
            raise ModelHealthError(f"fallback 引用了未知 provider: {ref.provider}")
        if ref.model not in _models_for_provider(provider):
            raise ModelHealthError(f"fallback 引用了未知模型: {ref.label}")
        targets.append(_target(provider, ref.model))
    return targets


def _target(provider: ProviderConfig, model: str) -> ModelHealthTarget:
    provider_copy = ProviderConfig(
        name=provider.name,
        protocol=provider.protocol,
        base_url=provider.base_url,
        model=model,
        api_key=provider.api_key,
        thinking=False,
        context_window=provider.context_window,
        max_output_tokens=8,
        api_key_env=provider.api_key_env,
        default_model=model,
        models=_models_for_provider(provider),
    )
    return ModelHealthTarget(provider=provider_copy, ref=f"{provider.name}/{model}")


def _find_provider(
    providers: Sequence[ProviderConfig],
    provider_name: str,
) -> ProviderConfig | None:
    return next((provider for provider in providers if provider.name == provider_name), None)


def _models_for_provider(provider: ProviderConfig) -> list[str]:
    models = list(provider.models or [])
    if not models and provider.model:
        models = [provider.model]
    return models

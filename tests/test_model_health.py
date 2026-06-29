"""验证批量模型健康检查 helper。"""

from __future__ import annotations

import pytest

from codepacex.config import ProviderConfig
from codepacex.model_health import (
    ModelHealthError,
    ModelHealthScope,
    build_model_health_targets,
    run_model_health_check,
)
from codepacex.model_test import ModelTestResult, ModelTestStatus


def _provider(name: str, model: str, **overrides) -> ProviderConfig:
    base = {
        "name": name,
        "protocol": "openai-compat",
        "base_url": f"https://{name}.example/v1",
        "api_key": "placeholder",
        "model": model,
    }
    base.update(overrides)
    return ProviderConfig(**base)


def _result(provider: ProviderConfig, status: ModelTestStatus) -> ModelTestResult:
    return ModelTestResult(
        provider=provider.name,
        protocol=provider.protocol,
        model=provider.model,
        base_url=provider.base_url,
        key_status="missing" if status == ModelTestStatus.MISSING_KEY else "available",
        status=status,
        reason=status.value if status != ModelTestStatus.OK else "completed",
        latency_ms=12 if status == ModelTestStatus.OK else None,
        suggestion="suggestion" if status != ModelTestStatus.OK else "",
    )


def test_all_targets_cover_provider_models_and_legacy_model() -> None:
    providers = [
        _provider("aliyun", "qwen-plus", models=["qwen-plus", "qwen-turbo"]),
        _provider("legacy", "claude-sonnet"),
    ]

    scope_label, targets, note = build_model_health_targets(
        ModelHealthScope.ALL,
        providers,
        [],
    )

    assert scope_label == "all configured models"
    assert note == ""
    assert [target.ref for target in targets] == [
        "aliyun/qwen-plus",
        "aliyun/qwen-turbo",
        "legacy/claude-sonnet",
    ]
    assert [target.provider.model for target in targets] == [
        "qwen-plus",
        "qwen-turbo",
        "claude-sonnet",
    ]
    assert all(target.provider.max_output_tokens == 8 for target in targets)
    assert all(target.provider.thinking is False for target in targets)
    assert providers[0].models == ["qwen-plus", "qwen-turbo"]


def test_provider_targets_only_include_requested_provider() -> None:
    providers = [
        _provider("aliyun", "qwen-plus", models=["qwen-plus", "qwen-turbo"]),
        _provider("deepseek", "deepseek-chat", models=["deepseek-chat"]),
    ]

    scope_label, targets, _note = build_model_health_targets(
        ModelHealthScope.PROVIDER,
        providers,
        [],
        provider_name="deepseek",
    )

    assert scope_label == "provider deepseek"
    assert [target.ref for target in targets] == ["deepseek/deepseek-chat"]


def test_provider_targets_reject_unknown_provider() -> None:
    providers = [_provider("aliyun", "qwen-plus")]

    with pytest.raises(ModelHealthError, match="未知 provider"):
        build_model_health_targets(
            ModelHealthScope.PROVIDER,
            providers,
            [],
            provider_name="missing",
        )


def test_fallback_targets_follow_order_and_support_slash_model_names() -> None:
    providers = [
        _provider("aliyun", "qwen-plus", models=["qwen-plus"]),
        _provider(
            "openrouter",
            "openai/gpt-4o-mini",
            models=["openai/gpt-4o-mini"],
        ),
    ]

    scope_label, targets, note = build_model_health_targets(
        ModelHealthScope.FALLBACK,
        providers,
        ["openrouter/openai/gpt-4o-mini", "aliyun/qwen-plus"],
    )

    assert scope_label == "fallback chain"
    assert note == ""
    assert [target.ref for target in targets] == [
        "openrouter/openai/gpt-4o-mini",
        "aliyun/qwen-plus",
    ]


def test_empty_fallback_returns_not_configured_note() -> None:
    scope_label, targets, note = build_model_health_targets(
        ModelHealthScope.FALLBACK,
        [_provider("aliyun", "qwen-plus")],
        [],
    )

    assert scope_label == "fallback chain"
    assert targets == []
    assert note == "Fallback: not configured"


@pytest.mark.asyncio
async def test_health_check_runs_serially_and_groups_results() -> None:
    providers = [
        _provider(
            "aliyun",
            "qwen-plus",
            models=["qwen-plus", "qwen-turbo", "qwen-max"],
        )
    ]
    statuses = {
        "qwen-plus": ModelTestStatus.OK,
        "qwen-turbo": ModelTestStatus.AUTHENTICATION_FAILED,
        "qwen-max": ModelTestStatus.MISSING_KEY,
    }
    calls: list[str] = []

    async def fake_tester(provider: ProviderConfig) -> ModelTestResult:
        calls.append(provider.model)
        return _result(provider, statuses[provider.model])

    result = await run_model_health_check(
        ModelHealthScope.ALL,
        providers,
        [],
        tester=fake_tester,
    )

    assert calls == ["qwen-plus", "qwen-turbo", "qwen-max"]
    assert result.total == 3
    assert [item.ref for item in result.ok_items] == ["aliyun/qwen-plus"]
    assert [item.ref for item in result.failed_items] == ["aliyun/qwen-turbo"]
    assert [item.ref for item in result.skipped_items] == ["aliyun/qwen-max"]

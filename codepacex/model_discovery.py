"""Read-only provider model discovery helpers."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from openai import AsyncOpenAI

from codepacex.config import ProviderConfig
from codepacex.model_test import ModelTestStatus, classify_model_test_error


__test__ = False


class ModelDiscoveryStatus(str, Enum):
    OK = "ok"
    UNSUPPORTED_PROVIDER = "unsupported_provider"
    MISSING_KEY = "missing_key"
    AUTHENTICATION_FAILED = "authentication_failed"
    PERMISSION_DENIED = "permission_denied"
    MODEL_NOT_FOUND = "model_not_found"
    RATE_LIMITED = "rate_limited"
    NETWORK_ERROR = "network_error"
    TIMEOUT = "timeout"
    SERVER_ERROR = "server_error"
    OVERLOADED = "overloaded"
    UNKNOWN_ERROR = "unknown_error"


@dataclass
class ModelDiscoveryResult:
    provider: str
    protocol: str
    base_url: str
    key_status: str
    status: ModelDiscoveryStatus
    reason: str
    models: list[str] = field(default_factory=list)
    latency_ms: int | None = None
    suggestion: str = ""

    @property
    def ok(self) -> bool:
        return self.status == ModelDiscoveryStatus.OK


async def discover_provider_models(
    provider: ProviderConfig,
    timeout_s: float = 10.0,
) -> ModelDiscoveryResult:
    start = time.perf_counter()
    key = provider.resolve_api_key()
    key_status = "available" if key else "missing"

    if provider.protocol != "openai-compat":
        return _result(
            provider,
            key_status=key_status,
            status=ModelDiscoveryStatus.UNSUPPORTED_PROVIDER,
            reason="unsupported_provider",
            suggestion="当前 /model discover 仅支持 openai-compat provider 的 /models 发现。",
        )

    if not key:
        return _result(
            provider,
            key_status="missing",
            status=ModelDiscoveryStatus.MISSING_KEY,
            reason="API key is missing.",
            suggestion=_missing_key_suggestion(provider),
        )

    try:
        client = AsyncOpenAI(api_key=key, base_url=provider.base_url)
        response = await asyncio.wait_for(client.models.list(), timeout_s)
        models = _extract_model_ids(response)
        return _result(
            provider,
            key_status="available",
            status=ModelDiscoveryStatus.OK,
            reason="completed",
            models=models,
            latency_ms=_elapsed_ms(start),
            suggestion=(
                "发现结果仅表示 provider 列表接口可见，不代表账号一定有权限调用。"
                if models
                else "模型列表接口未返回模型。"
            ),
        )
    except Exception as e:
        status, reason, suggestion = classify_model_test_error(e, provider)
        return _result(
            provider,
            key_status="available",
            status=_from_model_test_status(status),
            reason=reason,
            latency_ms=_elapsed_ms(start),
            suggestion=suggestion,
        )


def _extract_model_ids(response: Any) -> list[str]:
    raw_items = getattr(response, "data", response)
    if isinstance(raw_items, dict):
        raw_items = raw_items.get("data", [])
    if raw_items is None:
        return []

    models: list[str] = []
    for item in raw_items:
        model_id = ""
        if isinstance(item, dict):
            value = item.get("id", "")
            model_id = value if isinstance(value, str) else ""
        else:
            value = getattr(item, "id", "")
            model_id = value if isinstance(value, str) else ""
        if model_id:
            models.append(model_id)
    return sorted(dict.fromkeys(models))


def _from_model_test_status(status: ModelTestStatus) -> ModelDiscoveryStatus:
    try:
        return ModelDiscoveryStatus(status.value)
    except ValueError:
        return ModelDiscoveryStatus.UNKNOWN_ERROR


def _result(
    provider: ProviderConfig,
    key_status: str,
    status: ModelDiscoveryStatus,
    reason: str,
    models: list[str] | None = None,
    latency_ms: int | None = None,
    suggestion: str = "",
) -> ModelDiscoveryResult:
    return ModelDiscoveryResult(
        provider=provider.name,
        protocol=provider.protocol,
        base_url=provider.base_url,
        key_status=key_status,
        status=status,
        reason=reason,
        models=models or [],
        latency_ms=latency_ms,
        suggestion=suggestion,
    )


def _elapsed_ms(start: float) -> int:
    return max(int((time.perf_counter() - start) * 1000), 0)


def _missing_key_suggestion(provider: ProviderConfig) -> str:
    if provider.api_key_env:
        return f"请检查 {provider.api_key_env} 是否已设置。"
    return "请配置 api_key、api_key_env 或协议默认 API Key 环境变量。"

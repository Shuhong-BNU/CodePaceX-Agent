"""提供 provider/model 连通性测试与错误分类能力。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from codepacex.client import (
    AuthenticationError,
    LLMError,
    NetworkError,
    RateLimitError,
    create_client,
)
from codepacex.config import ProviderConfig
from codepacex.conversation import ConversationManager
from codepacex.tools.base import StreamEnd, TextDelta


__test__ = False


class ModelTestStatus(str, Enum):
    OK = "ok"
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
class ModelTestResult:
    provider: str
    protocol: str
    model: str
    base_url: str
    key_status: str
    status: ModelTestStatus
    reason: str
    latency_ms: int | None = None
    suggestion: str = ""

    @property
    def ok(self) -> bool:
        return self.status == ModelTestStatus.OK


async def test_provider_model(
    provider: ProviderConfig,
    timeout_s: float = 15.0,
) -> ModelTestResult:
    start = time.perf_counter()
    if not provider.resolve_api_key():
        return _result(
            provider,
            key_status="missing",
            status=ModelTestStatus.MISSING_KEY,
            reason="API key is missing.",
            suggestion=_missing_key_suggestion(provider),
        )

    try:
        client = create_client(provider)
        conversation = ConversationManager()
        conversation.add_user_message("Reply with OK.")
        await asyncio.wait_for(_consume_minimal_stream(client, conversation), timeout_s)
        return _result(
            provider,
            key_status="available",
            status=ModelTestStatus.OK,
            reason="completed",
            latency_ms=_elapsed_ms(start),
        )
    except Exception as e:
        status, reason, suggestion = classify_model_test_error(e, provider)
        latency = _elapsed_ms(start) if status != ModelTestStatus.MISSING_KEY else None
        return _result(
            provider,
            key_status="available",
            status=status,
            reason=reason,
            latency_ms=latency,
            suggestion=suggestion,
        )


async def _consume_minimal_stream(client: Any, conversation: ConversationManager) -> None:
    async for event in client.stream(conversation, system="", tools=None):
        if isinstance(event, TextDelta):
            return
        if isinstance(event, StreamEnd):
            return


def classify_model_test_error(
    exc: Exception,
    provider: ProviderConfig | None = None,
) -> tuple[ModelTestStatus, str, str]:
    status_code = _status_code(exc)
    message = _error_message(exc)
    class_names = " ".join(type(e).__name__.lower() for e in _exception_chain(exc))
    lower = message.lower()

    if isinstance(exc, asyncio.TimeoutError) or "timeout" in class_names:
        return ModelTestStatus.TIMEOUT, message, "请求超时，请稍后重试或检查网络。"
    if isinstance(exc, AuthenticationError) or status_code == 401:
        return (
            ModelTestStatus.AUTHENTICATION_FAILED,
            message,
            _auth_suggestion(provider),
        )
    if status_code == 403 or "permissiondenied" in class_names:
        return (
            ModelTestStatus.PERMISSION_DENIED,
            message,
            "请确认账号是否已开通该模型，或检查 provider 权限配置。",
        )
    if status_code == 404 or "notfound" in class_names or _looks_model_not_found(lower):
        return (
            ModelTestStatus.MODEL_NOT_FOUND,
            message,
            "请确认模型名是否在 provider.models 中，并且服务端支持该模型。",
        )
    if isinstance(exc, RateLimitError) or status_code == 429:
        return (
            ModelTestStatus.RATE_LIMITED,
            message,
            "请求被限流，请稍后重试或检查额度限制。",
        )
    if status_code == 529 or "overloaded" in lower:
        return ModelTestStatus.OVERLOADED, message, "服务端繁忙，请稍后重试。"
    if isinstance(exc, NetworkError) or "connection" in class_names:
        return ModelTestStatus.NETWORK_ERROR, message, "请检查网络连接和 base_url。"
    if status_code is not None and 500 <= status_code <= 599:
        return ModelTestStatus.SERVER_ERROR, message, "服务端返回错误，请稍后重试。"
    if isinstance(exc, LLMError):
        return ModelTestStatus.UNKNOWN_ERROR, message, "请查看错误信息并检查 provider 配置。"
    return ModelTestStatus.UNKNOWN_ERROR, message, "请查看错误信息并检查 provider 配置。"


def _result(
    provider: ProviderConfig,
    key_status: str,
    status: ModelTestStatus,
    reason: str,
    latency_ms: int | None = None,
    suggestion: str = "",
) -> ModelTestResult:
    return ModelTestResult(
        provider=provider.name,
        protocol=provider.protocol,
        model=provider.model,
        base_url=provider.base_url,
        key_status=key_status,
        status=status,
        reason=reason,
        latency_ms=latency_ms,
        suggestion=suggestion,
    )


def _exception_chain(exc: Exception) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _status_code(exc: Exception) -> int | None:
    for item in _exception_chain(exc):
        value = getattr(item, "status_code", None)
        if isinstance(value, int):
            return value
    return None


def _error_message(exc: Exception) -> str:
    text = str(exc).strip()
    if text:
        return text
    return type(exc).__name__


def _looks_model_not_found(message: str) -> bool:
    return "model" in message and ("not found" in message or "does not exist" in message)


def _elapsed_ms(start: float) -> int:
    return max(int((time.perf_counter() - start) * 1000), 0)


def _missing_key_suggestion(provider: ProviderConfig) -> str:
    if provider.api_key_env:
        return f"请检查 {provider.api_key_env} 是否已设置。"
    return "请配置 api_key、api_key_env 或协议默认 API Key 环境变量。"


def _auth_suggestion(provider: ProviderConfig | None) -> str:
    if provider is not None and provider.api_key_env:
        return f"请检查 {provider.api_key_env} 是否有效。"
    return "请检查 API Key 是否有效。"

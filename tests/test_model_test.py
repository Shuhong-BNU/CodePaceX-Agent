"""验证 /model test 的连通性 helper 与错误分类。"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from codepacex.client import (
    AuthenticationError,
    LLMError,
    NetworkError,
    RateLimitError,
)
from codepacex.config import ProviderConfig
from codepacex.model_test import (
    ModelTestStatus,
    classify_model_test_error,
    test_provider_model as run_provider_model_test,
)
from codepacex.tools.base import TextDelta


def _provider(**overrides) -> ProviderConfig:
    base = {
        "name": "p",
        "protocol": "openai-compat",
        "base_url": "https://example.test/v1",
        "model": "qwen-plus",
        "api_key": "placeholder",
        "models": ["qwen-plus"],
    }
    base.update(overrides)
    return ProviderConfig(**base)


class _FakeClient:
    def __init__(self) -> None:
        self.seen_history: list[str] = []

    async def stream(self, conversation, system="", tools=None):
        self.seen_history = [m.content for m in conversation.history]
        yield TextDelta("OK")


class _StatusError(Exception):
    def __init__(self, status_code: int, message: str = "status error") -> None:
        super().__init__(message)
        self.status_code = status_code


@pytest.mark.asyncio
async def test_provider_model_success_uses_temporary_conversation() -> None:
    provider = _provider(max_output_tokens=8)
    client = _FakeClient()

    with patch("codepacex.model_test.create_client", return_value=client) as mk:
        result = await run_provider_model_test(provider)

    assert result.ok is True
    assert result.status == ModelTestStatus.OK
    assert result.key_status == "available"
    assert result.reason == "completed"
    assert result.latency_ms is not None
    mk.assert_called_once_with(provider)
    assert client.seen_history == ["Reply with OK."]


@pytest.mark.asyncio
async def test_provider_model_missing_key_does_not_create_client(monkeypatch) -> None:
    monkeypatch.delenv("CODEPACEX_TEST_MISSING_KEY", raising=False)
    provider = _provider(api_key="", api_key_env="CODEPACEX_TEST_MISSING_KEY")

    with patch("codepacex.model_test.create_client") as mk:
        result = await run_provider_model_test(provider)

    assert result.ok is False
    assert result.status == ModelTestStatus.MISSING_KEY
    assert result.key_status == "missing"
    assert "CODEPACEX_TEST_MISSING_KEY" in result.suggestion
    mk.assert_not_called()


@pytest.mark.parametrize(
    ("exc", "status"),
    [
        (AuthenticationError("bad key"), ModelTestStatus.AUTHENTICATION_FAILED),
        (_StatusError(401), ModelTestStatus.AUTHENTICATION_FAILED),
        (_StatusError(403), ModelTestStatus.PERMISSION_DENIED),
        (_StatusError(404), ModelTestStatus.MODEL_NOT_FOUND),
        (RateLimitError("limited"), ModelTestStatus.RATE_LIMITED),
        (_StatusError(429), ModelTestStatus.RATE_LIMITED),
        (NetworkError("offline"), ModelTestStatus.NETWORK_ERROR),
        (asyncio.TimeoutError(), ModelTestStatus.TIMEOUT),
        (_StatusError(500), ModelTestStatus.SERVER_ERROR),
        (_StatusError(529, "overloaded"), ModelTestStatus.OVERLOADED),
        (LLMError("the model was not found"), ModelTestStatus.MODEL_NOT_FOUND),
        (ValueError("surprise"), ModelTestStatus.UNKNOWN_ERROR),
    ],
)
def test_classify_model_test_error(exc: Exception, status: ModelTestStatus) -> None:
    actual, reason, suggestion = classify_model_test_error(exc, _provider())

    assert actual == status
    assert reason
    assert suggestion


def test_classify_uses_original_cause_status_code() -> None:
    exc = LLMError("API error")
    exc.__cause__ = _StatusError(403, "forbidden")

    status, _reason, suggestion = classify_model_test_error(exc, _provider())

    assert status == ModelTestStatus.PERMISSION_DENIED
    assert "开通" in suggestion or "权限" in suggestion

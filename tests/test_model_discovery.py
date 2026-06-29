"""验证 /model discover 的只读模型发现 helper。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from codepacex.client import NetworkError, RateLimitError
from codepacex.config import ProviderConfig
from codepacex.model_discovery import (
    ModelDiscoveryStatus,
    discover_provider_models,
)


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


class _StatusError(Exception):
    def __init__(self, status_code: int, message: str = "status error") -> None:
        super().__init__(message)
        self.status_code = status_code


@pytest.mark.asyncio
async def test_discover_openai_compat_models_from_objects_and_dicts() -> None:
    provider = _provider()
    fake_client = SimpleNamespace(
        models=SimpleNamespace(
            list=AsyncMock(
                return_value=SimpleNamespace(
                    data=[
                        SimpleNamespace(id="qwen-plus"),
                        {"id": "qwen-turbo"},
                        {"id": "qwen-plus"},
                    ]
                )
            )
        )
    )

    with patch("codepacex.model_discovery.AsyncOpenAI", return_value=fake_client) as mk:
        result = await discover_provider_models(provider)

    assert result.ok is True
    assert result.status == ModelDiscoveryStatus.OK
    assert result.key_status == "available"
    assert result.models == ["qwen-plus", "qwen-turbo"]
    assert result.latency_ms is not None
    assert "不代表账号一定有权限调用" in result.suggestion
    mk.assert_called_once_with(api_key="placeholder", base_url="https://example.test/v1")
    fake_client.models.list.assert_awaited_once()


@pytest.mark.asyncio
async def test_discover_accepts_dict_response() -> None:
    provider = _provider()
    fake_client = SimpleNamespace(
        models=SimpleNamespace(
            list=AsyncMock(return_value={"data": [{"id": "b"}, {"id": "a"}]})
        )
    )

    with patch("codepacex.model_discovery.AsyncOpenAI", return_value=fake_client):
        result = await discover_provider_models(provider)

    assert result.ok is True
    assert result.models == ["a", "b"]


@pytest.mark.asyncio
async def test_discover_empty_model_list_is_ok() -> None:
    provider = _provider()
    fake_client = SimpleNamespace(
        models=SimpleNamespace(list=AsyncMock(return_value=SimpleNamespace(data=[])))
    )

    with patch("codepacex.model_discovery.AsyncOpenAI", return_value=fake_client):
        result = await discover_provider_models(provider)

    assert result.ok is True
    assert result.models == []
    assert "未返回模型" in result.suggestion


@pytest.mark.asyncio
async def test_discover_missing_key_does_not_call_network(monkeypatch) -> None:
    monkeypatch.delenv("CODEPACEX_DISCOVERY_MISSING_KEY", raising=False)
    provider = _provider(api_key="", api_key_env="CODEPACEX_DISCOVERY_MISSING_KEY")

    with patch("codepacex.model_discovery.AsyncOpenAI") as mk:
        result = await discover_provider_models(provider)

    assert result.ok is False
    assert result.status == ModelDiscoveryStatus.MISSING_KEY
    assert result.key_status == "missing"
    assert "CODEPACEX_DISCOVERY_MISSING_KEY" in result.suggestion
    mk.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["anthropic", "openai"])
async def test_discover_unsupported_provider_does_not_call_network(protocol: str) -> None:
    provider = _provider(protocol=protocol)

    with patch("codepacex.model_discovery.AsyncOpenAI") as mk:
        result = await discover_provider_models(provider)

    assert result.ok is False
    assert result.status == ModelDiscoveryStatus.UNSUPPORTED_PROVIDER
    assert result.reason == "unsupported_provider"
    assert "仅支持 openai-compat" in result.suggestion
    mk.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "status"),
    [
        (_StatusError(401), ModelDiscoveryStatus.AUTHENTICATION_FAILED),
        (_StatusError(403), ModelDiscoveryStatus.PERMISSION_DENIED),
        (_StatusError(404), ModelDiscoveryStatus.MODEL_NOT_FOUND),
        (RateLimitError("limited"), ModelDiscoveryStatus.RATE_LIMITED),
        (_StatusError(429), ModelDiscoveryStatus.RATE_LIMITED),
        (NetworkError("offline"), ModelDiscoveryStatus.NETWORK_ERROR),
        (asyncio.TimeoutError(), ModelDiscoveryStatus.TIMEOUT),
        (_StatusError(500), ModelDiscoveryStatus.SERVER_ERROR),
        (_StatusError(529, "overloaded"), ModelDiscoveryStatus.OVERLOADED),
        (ValueError("surprise"), ModelDiscoveryStatus.UNKNOWN_ERROR),
    ],
)
async def test_discover_classifies_failures(exc: Exception, status: ModelDiscoveryStatus) -> None:
    provider = _provider()
    fake_client = SimpleNamespace(models=SimpleNamespace(list=AsyncMock(side_effect=exc)))

    with patch("codepacex.model_discovery.AsyncOpenAI", return_value=fake_client):
        result = await discover_provider_models(provider)

    assert result.ok is False
    assert result.status == status
    assert result.reason
    assert result.suggestion

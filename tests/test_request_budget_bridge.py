from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import httpx
import openai
import pytest

from codepacex.client import NetworkError, OpenAICompatClient
from codepacex.config import ProviderConfig
from codepacex.conversation import ConversationManager
from codepacex.tools.base import StreamEnd
from evals.paid_gate import ProviderRequestCeilingExceeded, ProviderUsageUnknown


class _Usage:
    prompt_tokens = 12
    completion_tokens = 3
    prompt_tokens_details = SimpleNamespace(cached_tokens=0)

    def model_dump(self, *, exclude_none: bool) -> dict[str, int]:
        assert exclude_none is True
        return {"prompt_tokens": 12, "completion_tokens": 3}


class _Response:
    def __init__(self, chunks: list[object]) -> None:
        self.chunks = chunks

    def __aiter__(self):
        async def iterator():
            for chunk in self.chunks:
                yield chunk
        return iterator()


class _FakeBudget:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.reservation = object()

    def reserve_before_request(self) -> object:
        self.calls.append("reserve")
        return self.reservation

    def settle_after_usage(self, reservation: object, usage: dict[str, int] | None) -> None:
        assert reservation is self.reservation
        assert usage == {"prompt_tokens": 12, "completion_tokens": 3}
        self.calls.append("settle")

    def record_request_failure(self, reservation: object, *, failure_type: str) -> None:
        assert reservation is self.reservation
        assert "openai.APITimeoutError" in failure_type
        assert "httpx.ConnectTimeout" in failure_type
        self.calls.append("failure")


def _client() -> OpenAICompatClient:
    return OpenAICompatClient(ProviderConfig(
        "test", "openai-compat", "https://example.invalid", "test-model",
        api_key="not-a-real-key", max_output_tokens=128,
    ), max_retries=0)


@pytest.mark.asyncio
async def test_compat_client_reserves_before_provider_and_settles_on_usage(monkeypatch) -> None:
    budget = _FakeBudget()
    client = _client()
    response = _Response([SimpleNamespace(choices=[], usage=_Usage())])

    async def create(**kwargs):
        assert budget.calls == ["reserve"]
        assert kwargs["stream_options"] == {"include_usage": True}
        return response

    client._client = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=create),
    ))
    conversation = ConversationManager()
    conversation.add_user_message("hello")
    monkeypatch.setenv("CODEPACEX_EXPERIMENT_REQUEST_BUDGET", "1")
    with patch("evals.paid_gate.ProviderRequestBudget.from_environment", return_value=budget):
        events = [event async for event in client.stream(conversation)]
    assert any(isinstance(event, StreamEnd) for event in events)
    assert budget.calls == ["reserve", "settle"]


@pytest.mark.asyncio
async def test_compat_client_sends_explicit_goal3_completion_contract() -> None:
    client = OpenAICompatClient(ProviderConfig(
        "bailian-qwen37-max", "openai-compat", "https://example.invalid",
        "qwen3.7-max-2026-06-08", api_key="not-a-real-key",
        max_completion_tokens=8192, enable_thinking=True, thinking_budget=6144,
    ), max_retries=0)
    response = _Response([SimpleNamespace(choices=[], usage=_Usage())])

    async def create(**kwargs):
        assert kwargs["max_completion_tokens"] == 8192
        assert kwargs["extra_body"] == {
            "enable_thinking": True, "thinking_budget": 6144,
        }
        assert "max_tokens" not in kwargs
        return response

    client._client = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=create),
    ))
    conversation = ConversationManager()
    conversation.add_user_message("hello")
    events = [event async for event in client.stream(conversation)]
    assert any(isinstance(event, StreamEnd) for event in events)


@pytest.mark.asyncio
async def test_compat_client_keeps_reservation_when_provider_returns_no_usage(monkeypatch) -> None:
    budget = _FakeBudget()
    client = _client()

    async def create(**kwargs):
        return _Response([])

    client._client = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=create),
    ))
    conversation = ConversationManager()
    conversation.add_user_message("hello")
    monkeypatch.setenv("CODEPACEX_EXPERIMENT_REQUEST_BUDGET", "1")
    with patch("evals.paid_gate.ProviderRequestBudget.from_environment", return_value=budget), pytest.raises(
        ProviderUsageUnknown, match="active reservation retained",
    ):
        async for _event in client.stream(conversation):
            pass
    assert budget.calls == ["reserve"]


@pytest.mark.asyncio
async def test_compat_client_does_not_retry_or_settle_after_connect_timeout(monkeypatch) -> None:
    budget = _FakeBudget()
    client = _client()
    calls = 0

    async def create(**kwargs):
        nonlocal calls
        calls += 1
        request = httpx.Request("POST", "https://example.invalid/chat/completions")
        try:
            raise httpx.ConnectTimeout("connect timed out", request=request)
        except httpx.ConnectTimeout as timeout:
            raise openai.APITimeoutError(request=request) from timeout

    client._client = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=create),
    ))
    conversation = ConversationManager()
    conversation.add_user_message("hello")
    monkeypatch.setenv("CODEPACEX_EXPERIMENT_REQUEST_BUDGET", "1")
    with patch("evals.paid_gate.ProviderRequestBudget.from_environment", return_value=budget), pytest.raises(
        NetworkError, match="Network error",
    ):
        async for _event in client.stream(conversation):
            pass
    assert calls == 1
    assert budget.calls == ["reserve", "failure"]


@pytest.mark.asyncio
async def test_compat_client_does_not_call_provider_after_request_ceiling(monkeypatch) -> None:
    client = _client()
    provider_calls = 0

    class CeilingBudget:
        def reserve_before_request(self) -> object:
            raise ProviderRequestCeilingExceeded(
                trial_id="swe/goal4/boundary", maximum_provider_requests=40,
                attempted_request_index=41,
            )

    async def create(**kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("Provider transport must not be reached")

    client._client = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=create),
    ))
    conversation = ConversationManager()
    conversation.add_user_message("hello")
    monkeypatch.setenv("CODEPACEX_EXPERIMENT_REQUEST_BUDGET", "1")
    with patch("evals.paid_gate.ProviderRequestBudget.from_environment", return_value=CeilingBudget()), pytest.raises(
        ProviderRequestCeilingExceeded, match="attempted_request_index=41",
    ):
        async for _event in client.stream(conversation):
            pass
    assert provider_calls == 0


@pytest.mark.asyncio
async def test_compat_client_stops_transport_exactly_at_forty_requests(monkeypatch) -> None:
    client = _client()
    provider_calls = 0

    class FortyRequestBudget:
        def __init__(self) -> None:
            self.requests = 0

        def reserve_before_request(self) -> object:
            self.requests += 1
            if self.requests == 41:
                raise ProviderRequestCeilingExceeded(
                    trial_id="swe/goal4/boundary", maximum_provider_requests=40,
                    attempted_request_index=41,
                )
            return self.requests

        def settle_after_usage(self, reservation: object, usage: dict[str, int] | None) -> None:
            assert isinstance(reservation, int) and reservation <= 40
            assert usage == {"prompt_tokens": 12, "completion_tokens": 3}

    budget = FortyRequestBudget()

    async def create(**kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _Response([SimpleNamespace(choices=[], usage=_Usage())])

    client._client = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=create),
    ))
    conversation = ConversationManager()
    conversation.add_user_message("hello")
    monkeypatch.setenv("CODEPACEX_EXPERIMENT_REQUEST_BUDGET", "1")
    with patch("evals.paid_gate.ProviderRequestBudget.from_environment", return_value=budget):
        for _ in range(40):
            events = [event async for event in client.stream(conversation)]
            assert any(isinstance(event, StreamEnd) for event in events)
        with pytest.raises(ProviderRequestCeilingExceeded, match="attempted_request_index=41"):
            async for _event in client.stream(conversation):
                pass
    assert provider_calls == 40

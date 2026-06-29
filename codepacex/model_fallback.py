"""Fallback chain helpers for configured provider/model targets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from codepacex.config import ProviderConfig
from codepacex.model_test import ModelTestStatus, classify_model_test_error


RECOVERABLE_STATUSES = {
    ModelTestStatus.RATE_LIMITED,
    ModelTestStatus.NETWORK_ERROR,
    ModelTestStatus.TIMEOUT,
    ModelTestStatus.SERVER_ERROR,
    ModelTestStatus.OVERLOADED,
}


@dataclass(frozen=True)
class ModelRef:
    provider: str
    model: str

    @property
    def label(self) -> str:
        return f"{self.provider}/{self.model}"


@dataclass(frozen=True)
class FallbackError:
    status: ModelTestStatus
    reason: str
    suggestion: str

    @property
    def recoverable(self) -> bool:
        return self.status in RECOVERABLE_STATUSES


@dataclass(frozen=True)
class FallbackCandidate:
    provider: ProviderConfig
    ref: ModelRef


def parse_model_ref(value: str) -> ModelRef:
    provider, model = value.split("/", 1)
    return ModelRef(provider=provider, model=model)


def model_ref_for_provider(provider: ProviderConfig) -> ModelRef:
    return ModelRef(provider=provider.name, model=provider.model)


def provider_for_model(provider: ProviderConfig, model: str) -> ProviderConfig:
    return ProviderConfig(
        name=provider.name,
        protocol=provider.protocol,
        base_url=provider.base_url,
        model=model,
        api_key=provider.api_key,
        thinking=provider.thinking,
        context_window=provider.context_window,
        max_output_tokens=provider.max_output_tokens,
        api_key_env=provider.api_key_env,
        default_model=model,
        models=list(provider.models or [provider.model]),
    )


def classify_fallback_error(
    exc: Exception,
    provider: ProviderConfig | None,
) -> FallbackError:
    status, reason, suggestion = classify_model_test_error(exc, provider)
    return FallbackError(status=status, reason=reason, suggestion=suggestion)


def iter_fallback_candidates(
    refs: Sequence[str],
    providers: Sequence[ProviderConfig],
    current_provider: ProviderConfig | None,
    *,
    has_history: bool,
    tried: set[str] | None = None,
) -> list[FallbackCandidate]:
    """Return configured fallback candidates safe for the current history.

    Cross-protocol candidates are skipped once history exists because existing
    thinking/tool blocks may not serialize safely under another protocol.
    """
    tried = tried or set()
    by_name = {p.name: p for p in providers}
    current_ref = (
        model_ref_for_provider(current_provider).label if current_provider else ""
    )
    current_protocol = current_provider.protocol if current_provider else ""

    candidates: list[FallbackCandidate] = []
    for raw in refs:
        ref = parse_model_ref(raw)
        if ref.label == current_ref or ref.label in tried:
            continue
        provider = by_name.get(ref.provider)
        if provider is None:
            continue
        if ref.model not in (provider.models or [provider.model]):
            continue
        if current_protocol and provider.protocol != current_protocol and has_history:
            continue
        candidates.append(
            FallbackCandidate(
                provider=provider_for_model(provider, ref.model),
                ref=ref,
            )
        )
    return candidates

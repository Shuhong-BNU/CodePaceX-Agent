from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ConfigError(Exception):
    pass


@dataclass
class ProviderConfig:
    name: str
    default_model: str = ""
    model: str = ""
    models: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.default_model:
            self.model = self.default_model
        if self.model and not self.models:
            self.models = [self.model]

    @property
    def effective_model(self) -> str:
        return self.model


def validate_provider(raw: dict[str, Any]) -> ProviderConfig:
    if "name" not in raw:
        raise ConfigError("provider requires name")
    models = raw.get("models", [])
    return ProviderConfig(
        name=str(raw["name"]),
        default_model=str(raw.get("default_model", "")),
        model=str(raw.get("model", "")),
        models=models,
    )


def load_provider(raw: dict[str, Any]) -> ProviderConfig:
    return validate_provider(raw)

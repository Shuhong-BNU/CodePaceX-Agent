"""Operating-system sandbox backends for command execution."""

from __future__ import annotations

import platform
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class SandboxConfig:
    allow_write: list[str] = field(default_factory=list)
    deny_write: list[str] = field(default_factory=list)
    network_enabled: bool = False


class Sandbox(ABC):
    @abstractmethod
    def wrap(self, command: str, config: SandboxConfig) -> str:
        """Wrap a shell command for execution inside the sandbox."""

    @abstractmethod
    def available(self) -> bool:
        """Return whether the backend is usable on this host."""


def create_sandbox() -> Sandbox | None:
    system = platform.system()
    if system == "Darwin":
        from codepacex.sandbox.seatbelt import SeatbeltSandbox

        return SeatbeltSandbox()
    if system == "Linux":
        from codepacex.sandbox.bwrap import BwrapSandbox

        return BwrapSandbox()
    return None


def build_sandbox_config(work_dir: str, *, network_enabled: bool = False) -> SandboxConfig:
    project_config_dir = f"{work_dir}/.codepacex"
    return SandboxConfig(
        allow_write=[work_dir, "/tmp"],
        deny_write=[
            f"{project_config_dir}/config.yaml",
            f"{project_config_dir}/config.local.yaml",
            f"{project_config_dir}/permissions.local.yaml",
        ],
        network_enabled=network_enabled,
    )


def configure_bash_sandbox(
    registry: object,
    *,
    enabled: bool,
    network_enabled: bool,
    work_dir: str,
) -> tuple[Sandbox | None, SandboxConfig | None, str]:
    """Attach the platform sandbox to the registry's Bash tool."""
    get_tool = getattr(registry, "get", None)
    bash = get_tool("Bash") if callable(get_tool) else None
    if bash is None:
        return None, None, "Bash tool is not registered"
    bash.work_dir = work_dir
    if not enabled:
        bash.sandbox = None
        bash.sandbox_config = None
        return None, None, "disabled"
    backend = create_sandbox()
    if backend is None:
        return None, None, "unsupported operating system"
    if not backend.available():
        return backend, None, f"{type(backend).__name__} is unavailable"
    config = build_sandbox_config(work_dir, network_enabled=network_enabled)
    bash.sandbox = backend
    bash.sandbox_config = config
    return backend, config, "available"

"""macOS Seatbelt sandbox backend."""

from __future__ import annotations

import shlex
from pathlib import Path

from codepacex.sandbox import Sandbox, SandboxConfig

_SANDBOX_EXEC = "/usr/bin/sandbox-exec"


def build_profile(config: SandboxConfig) -> str:
    rules = [
        "(version 1)",
        "(deny default)",
        "(allow process-exec)",
        "(allow process-fork)",
        "(allow sysctl-read)",
        '(allow file-read* (subpath "/"))',
    ]
    for path in config.allow_write:
        resolved = str(Path(path).resolve())
        rules.append(f'(allow file-write* (subpath "{resolved}"))')
    for path in config.deny_write:
        resolved = str(Path(path).resolve())
        matcher = "subpath" if Path(resolved).is_dir() else "literal"
        rules.append(f'(deny file-write* ({matcher} "{resolved}"))')
    rules.append("(allow network*)" if config.network_enabled else "(deny network*)")
    return "\n".join(rules)


class SeatbeltSandbox(Sandbox):
    def wrap(self, command: str, config: SandboxConfig) -> str:
        profile = build_profile(config)
        return (
            f"{_SANDBOX_EXEC} -p {shlex.quote(profile)} "
            f"bash -c {shlex.quote(command)}"
        )

    def available(self) -> bool:
        return Path(_SANDBOX_EXEC).is_file()

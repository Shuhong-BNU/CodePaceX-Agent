from __future__ import annotations

import platform
import shlex
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from codepacex.sandbox import SandboxConfig
from codepacex.sandbox.bwrap import BwrapSandbox
from codepacex.sandbox.seatbelt import SeatbeltSandbox


def test_bwrap_builder_orders_bind_and_network_flags(tmp_path: Path) -> None:
    protected = tmp_path / "protected"
    protected.mkdir()
    command = BwrapSandbox().wrap(
        "printf ok", SandboxConfig(allow_write=[str(tmp_path)], deny_write=[str(protected)])
    )
    assert command.index("--ro-bind / /") < command.index(f"--bind {tmp_path} {tmp_path}")
    assert command.index(f"--bind {tmp_path} {tmp_path}") < command.index(f"--ro-bind {protected} {protected}")
    assert "--unshare-net" in command


def _seatbelt_capability_or_skip() -> None:
    executable = Path("/usr/bin/sandbox-exec")
    if not executable.is_file():
        pytest.skip("system capability missing: /usr/bin/sandbox-exec is absent")
    probe = subprocess.run(
        [str(executable), "-p", "(version 1)\n(allow default)", "/usr/bin/true"],
        text=True, capture_output=True, check=False,
    )
    if probe.returncode == 0:
        return
    error = probe.stderr.lower()
    if "operation not permitted" in error or "not supported" in error:
        pytest.skip(f"system capability unavailable: {probe.stderr.strip()}")
    pytest.fail(f"sandbox-exec capability probe failed: {probe.stderr}")


@pytest.mark.skipif(platform.system() != "Darwin", reason="system capability missing: macOS Seatbelt")
def test_macos_seatbelt_real_smoke(tmp_path: Path) -> None:
    _seatbelt_capability_or_skip()
    work = tmp_path / "work"
    work.mkdir()
    outside = tmp_path / "outside.txt"
    sandbox = SeatbeltSandbox()
    config = SandboxConfig(allow_write=[str(work)], network_enabled=False)

    inside_command = f"printf inside > {shlex.quote(str(work / 'inside.txt'))}"
    inside = subprocess.run(sandbox.wrap(inside_command, config), shell=True, text=True, capture_output=True, check=False)
    assert inside.returncode == 0, inside.stderr
    assert (work / "inside.txt").read_text(encoding="utf-8") == "inside"

    outside_command = f"printf outside > {shlex.quote(str(outside))}"
    blocked = subprocess.run(sandbox.wrap(outside_command, config), shell=True, text=True, capture_output=True, check=False)
    assert blocked.returncode != 0
    assert not outside.exists()

    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(2)
        port = server.getsockname()[1]
        code = f"import socket; s=socket.create_connection(('127.0.0.1',{port}),1); s.close()"
        unsandboxed = subprocess.run([sys.executable, "-c", code], text=True, capture_output=True, check=False)
        assert unsandboxed.returncode == 0, unsandboxed.stderr
        python_command = f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"
        denied = subprocess.run(sandbox.wrap(python_command, config), shell=True, text=True, capture_output=True, check=False)
        assert denied.returncode != 0


def _bwrap_capability_or_skip() -> str:
    executable = shutil.which("bwrap")
    if executable is None:
        pytest.skip("system capability missing: bubblewrap is not installed")
    probe = subprocess.run(
        [executable, "--unshare-user", "--unshare-pid", "--unshare-net", "--ro-bind", "/", "/", "--proc", "/proc", "--dev", "/dev", "--", "/bin/true"],
        text=True, capture_output=True, check=False,
    )
    if probe.returncode == 0:
        return executable
    error = probe.stderr.lower()
    if "operation not permitted" in error or "no permissions to create new namespace" in error:
        pytest.skip(f"system capability unavailable: {probe.stderr.strip()}")
    pytest.fail(f"bubblewrap capability probe failed: {probe.stderr}")


@pytest.mark.skipif(platform.system() != "Linux", reason="system capability missing: Linux bubblewrap")
def test_linux_bwrap_real_smoke(tmp_path: Path) -> None:
    _bwrap_capability_or_skip()
    work = tmp_path / "work"
    work.mkdir()
    outside = tmp_path / "outside.txt"
    sandbox = BwrapSandbox()
    config = SandboxConfig(allow_write=[str(work)], network_enabled=False)

    inside = subprocess.run(
        sandbox.wrap(f"printf inside > {shlex.quote(str(work / 'inside.txt'))}", config),
        shell=True, text=True, capture_output=True, check=False,
    )
    assert inside.returncode == 0, inside.stderr
    blocked = subprocess.run(
        sandbox.wrap(f"printf outside > {shlex.quote(str(outside))}", config),
        shell=True, text=True, capture_output=True, check=False,
    )
    assert blocked.returncode != 0
    assert not outside.exists()

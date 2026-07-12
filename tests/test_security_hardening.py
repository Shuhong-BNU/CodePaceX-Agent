from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator

import pytest
import yaml
from pydantic import BaseModel

from codepacex.agent import Agent, PermissionRequest, StreamingExecutor
from codepacex.client import LLMClient
from codepacex.config import load_config
from codepacex.conversation import ConversationManager
from codepacex.hooks import Action, Hook, HookEngine
from codepacex.permissions import DangerousCommandDetector, PathSandbox, PermissionChecker, PermissionMode, RuleEngine
from codepacex.sandbox import build_sandbox_config
from codepacex.tools import create_default_registry
from codepacex.tools.base import StreamEnd, TextDelta, Tool, ToolCallComplete, ToolResult
from codepacex.tools.diff import Diff


def _checker(tmp_path: Path, *, mode: PermissionMode = PermissionMode.DEFAULT, rules: Path | None = None, sandbox_enabled: bool = False) -> PermissionChecker:
    return PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=PathSandbox(str(tmp_path)),
        rule_engine=RuleEngine(project_rules_path=rules),
        mode=mode,
        sandbox_enabled=sandbox_enabled,
    )


@pytest.mark.parametrize(
    ("command", "effect"),
    [
        ("rm temp.txt", "ask"),
        ("rm -rf build/", "ask"),
        ("rm -rf .", "deny"),
        ("rm -rf ..", "deny"),
        ("rm -rf ~", "deny"),
        (f"rm -rf {Path.home()}", "deny"),
        ("/bin/rm -rf .git/config", "deny"),
        ("command rm -rf .codepacex/state", "deny"),
        ("sudo rm -rf tests", "deny"),
        ("find . -delete", "deny"),
        ("find build -delete", "ask"),
        ("git clean -fdx", "ask"),
    ],
)
def test_mandatory_delete_decisions(tmp_path: Path, command: str, effect: str) -> None:
    from codepacex.tools.bash import Bash

    assert _checker(tmp_path, mode=PermissionMode.BYPASS).check(Bash(), {"command": command}).effect == effect


def test_single_test_file_delete_asks_but_bulk_delete_denies(tmp_path: Path) -> None:
    from codepacex.tools.bash import Bash

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    test_file = tests_dir / "test_one.py"
    test_file.write_text("pass\n", encoding="utf-8")
    checker = _checker(tmp_path, mode=PermissionMode.BYPASS)
    assert checker.check(Bash(), {"command": "rm tests/test_one.py"}).effect == "ask"
    assert checker.check(Bash(), {"command": "rm tests/test_one.py tests/test_two.py"}).effect == "deny"


def test_explicit_allow_cannot_override_mandatory_deny(tmp_path: Path) -> None:
    from codepacex.tools.bash import Bash

    rules = tmp_path / "rules.yaml"
    rules.write_text(yaml.safe_dump([{"rule": "Bash(rm *)", "effect": "allow"}]), encoding="utf-8")
    checker = _checker(tmp_path, mode=PermissionMode.BYPASS, rules=rules, sandbox_enabled=True)
    checker.add_session_allow("Bash", "rm -rf .")
    decision = checker.check(Bash(), {"command": "rm -rf ."})
    assert decision.effect == "deny"
    assert decision.persistable is False


def test_dangerous_check_exception_fails_closed(tmp_path: Path) -> None:
    from codepacex.tools.bash import Bash

    class BrokenDetector:
        def assess(self, command: str, work_dir: Path):
            raise RuntimeError("detector unavailable")

    checker = PermissionChecker(BrokenDetector(), PathSandbox(str(tmp_path)), RuleEngine(), PermissionMode.BYPASS)  # type: ignore[arg-type]
    decision = checker.check(Bash(), {"command": "echo ok"})
    assert decision.effect == "ask"
    assert "detector unavailable" in decision.reason
    assert decision.persistable is False


def test_rule_parse_error_fails_closed(tmp_path: Path) -> None:
    from codepacex.tools.bash import Bash

    rules = tmp_path / "rules.yaml"
    rules.write_text("not: a-list\n", encoding="utf-8")
    decision = _checker(tmp_path, mode=PermissionMode.BYPASS, rules=rules).check(Bash(), {"command": "echo ok"})
    assert decision.effect == "ask"
    assert "权限规则检查失败" in decision.reason


def test_diff_paths_resolve_inside_workspace(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    old = tmp_path / "old.txt"
    new = tmp_path / "new.txt"
    old.write_text("old", encoding="utf-8")
    new.write_text("new", encoding="utf-8")
    decision = _checker(tmp_path).check(Diff(), {"old_file": "src/../old.txt", "new_file": "new.txt"})
    assert decision.effect == "allow"


@pytest.mark.parametrize("outside", ["../outside.txt", "~/.ssh/id_rsa"])
def test_diff_rejects_outside_paths(tmp_path: Path, outside: str) -> None:
    inside = tmp_path / "inside.txt"
    inside.write_text("inside", encoding="utf-8")
    decision = _checker(tmp_path).check(Diff(), {"old_file": str(inside), "new_file": outside})
    assert decision.effect == "deny"


def test_diff_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = tmp_path / "escape.txt"
    link.symlink_to(outside)
    inside = tmp_path / "inside.txt"
    inside.write_text("inside", encoding="utf-8")
    assert _checker(tmp_path).check(Diff(), {"old_file": str(inside), "new_file": str(link)}).effect == "deny"


def test_write_rejects_symlinked_parent_escape(tmp_path: Path) -> None:
    from codepacex.tools.write_file import WriteFile

    outside = tmp_path.parent / f"{tmp_path.name}-outside-dir"
    outside.mkdir(exist_ok=True)
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
    decision = _checker(tmp_path).check(WriteFile(), {"file_path": "linked/new.txt", "content": "x"})
    assert decision.effect == "ask"
    assert decision.persistable is False


@pytest.mark.parametrize(
    ("command", "effect"),
    [
        ("pwd", "allow"),
        ("cat README.md", "allow"),
        ("cat ~/.ssh/id_rsa", "ask"),
        ("git diff ../../outside", "ask"),
        ("git diff --no-index a b", "ask"),
        ("env", "ask"),
        ("printenv", "ask"),
        ("grep TODO README.md", "ask"),
        ("cat README.md | wc", "ask"),
    ],
)
def test_sandbox_auto_allow_is_narrow(tmp_path: Path, command: str, effect: str) -> None:
    from codepacex.tools.bash import Bash

    (tmp_path / "README.md").write_text("text", encoding="utf-8")
    assert _checker(tmp_path, sandbox_enabled=True).check(Bash(), {"command": command}).effect == effect


def _config(provider_sandbox: dict[str, bool] | None) -> str:
    raw: dict[str, Any] = {
        "providers": [{"name": "test", "protocol": "openai", "base_url": "https://example.invalid", "model": "model"}],
    }
    if provider_sandbox is not None:
        raw["sandbox"] = provider_sandbox
    return yaml.safe_dump(raw)


@pytest.mark.parametrize(
    ("user", "project", "local", "expected"),
    [
        ({"enabled": True}, {"enabled": False}, None, False),
        ({"enabled": False}, {"enabled": True}, None, True),
        ({"enabled": False}, {"enabled": True}, {"enabled": False}, False),
        ({"enabled": True}, {}, None, True),
    ],
)
def test_sandbox_boolean_layering(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, user: dict[str, bool], project: dict[str, bool], local: dict[str, bool] | None, expected: bool) -> None:
    home = tmp_path / "home"
    work = tmp_path / "work"
    (home / ".codepacex").mkdir(parents=True)
    (work / ".codepacex").mkdir(parents=True)
    (home / ".codepacex/config.yaml").write_text(_config(user), encoding="utf-8")
    (work / ".codepacex/config.yaml").write_text(_config(project), encoding="utf-8")
    if local is not None:
        (work / ".codepacex/config.local.yaml").write_text(_config(local), encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(work)
    assert load_config().sandbox.enabled is expected


def test_shared_sandbox_config_protects_all_permission_files(tmp_path: Path) -> None:
    config = build_sandbox_config(str(tmp_path))
    assert {Path(path).name for path in config.deny_write} == {
        "config.yaml", "config.local.yaml", "permissions.local.yaml"
    }


class EmptyParams(BaseModel):
    pass


class CountingReadTool(Tool):
    name = "CountingRead"
    description = "Count executions"
    params_model = EmptyParams
    category = "read"
    is_concurrency_safe = True

    def __init__(self, sentinel: Path) -> None:
        self.count = 0
        self.sentinel = sentinel

    async def execute(self, params: EmptyParams) -> ToolResult:
        self.count += 1
        self.sentinel.write_text("executed", encoding="utf-8")
        return ToolResult("ok")


class HookAwareClient(LLMClient):
    def __init__(self, tool: CountingReadTool) -> None:
        self.tool = tool
        self.calls = 0

    async def stream(self, conversation: ConversationManager, system: str = "", tools=None) -> AsyncIterator[Any]:
        self.calls += 1
        if self.calls == 1:
            yield ToolCallComplete("t1", "CountingRead", {})
            await asyncio.sleep(0)
            assert self.tool.count == 0, "Hook-enabled tool executed before stream completion"
            yield StreamEnd("tool_use", input_tokens=1, output_tokens=1)
        else:
            yield TextDelta("done")
            yield StreamEnd("end_turn", input_tokens=1, output_tokens=1)


@pytest.mark.asyncio
async def test_hook_rejection_has_zero_target_side_effects(tmp_path: Path) -> None:
    sentinel = tmp_path / "sentinel"
    tool = CountingReadTool(sentinel)
    registry = create_default_registry()
    registry.register(tool)
    hook = Hook(id="reject", event="pre_tool_use", action=Action(type="command", command="printf blocked"), reject=True)
    agent = Agent(HookAwareClient(tool), registry, "anthropic", work_dir=str(tmp_path), permission_checker=_checker(tmp_path), hook_engine=HookEngine([hook]))
    conversation = ConversationManager()
    conversation.add_user_message("run")
    async for _ in agent.run(conversation):
        pass
    assert tool.count == 0
    assert not sentinel.exists()


@pytest.mark.asyncio
async def test_streaming_executor_cancels_and_reaps() -> None:
    executor = StreamingExecutor()
    finalized = asyncio.Event()

    async def pending() -> Any:
        try:
            await asyncio.Event().wait()
        finally:
            finalized.set()

    executor.submit(pending())
    await asyncio.sleep(0)
    await executor.cancel_and_reap()
    assert finalized.is_set()


class DeleteClient(LLMClient):
    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, conversation: ConversationManager, system: str = "", tools=None) -> AsyncIterator[Any]:
        self.calls += 1
        if self.calls == 1:
            yield ToolCallComplete("t1", "Bash", {"command": "rm temp.txt"})
            yield StreamEnd("tool_use", input_tokens=1, output_tokens=1)
        else:
            yield TextDelta("done")
            yield StreamEnd("end_turn", input_tokens=1, output_tokens=1)


@pytest.mark.asyncio
async def test_multiple_ask_layers_emit_one_hitl(tmp_path: Path) -> None:
    agent = Agent(DeleteClient(), create_default_registry(), "anthropic", work_dir=str(tmp_path), permission_checker=_checker(tmp_path))
    conversation = ConversationManager()
    conversation.add_user_message("delete")
    requests: list[PermissionRequest] = []
    async for event in agent.run(conversation):
        if isinstance(event, PermissionRequest):
            requests.append(event)
            event.future.set_result("deny")
    assert len(requests) == 1
    assert "危险命令检查" in requests[0].description

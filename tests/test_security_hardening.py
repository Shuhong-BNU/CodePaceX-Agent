from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator

import pytest
import yaml
from pydantic import BaseModel

from codepacex.__main__ import _deny_noninteractive_permission
from codepacex.agent import Agent, PermissionRequest, StreamingExecutor, StreamText
from codepacex.client import LLMClient
from codepacex.config import load_config
from codepacex.conversation import ConversationManager
from codepacex.hooks import Action, Hook, HookEngine
from codepacex.permissions import DangerousCommandDetector, PathSandbox, PermissionChecker, PermissionMode, RuleEngine
from codepacex.remote import RemoteServer
from codepacex.sandbox import build_sandbox_config
from codepacex.tools import create_default_registry
from codepacex.tools.base import StreamEnd, TextDelta, Tool, ToolCallComplete, ToolResult
from codepacex.tools.diff import Diff
from codepacex.tools.bash import Bash, Params as BashParams
from codepacex.tools.install_skill import InstallSkill, InstallSkillParams


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


@pytest.mark.parametrize("command", [
    "git -C .. clean -fdx",
    "git --work-tree=.. clean -fdx",
    "git --git-dir ../.git clean -fdx",
])
@pytest.mark.parametrize("authorization", ["bypass", "explicit", "session"])
def test_git_clean_globals_remain_mandatory_ask(
    tmp_path: Path, command: str, authorization: str
) -> None:
    rules = tmp_path / "rules.yaml"
    if authorization == "explicit":
        rules.write_text(yaml.safe_dump([{"rule": "Bash(git *)", "effect": "allow"}]), encoding="utf-8")
    checker = _checker(
        tmp_path,
        mode=PermissionMode.BYPASS,
        rules=rules if authorization == "explicit" else None,
        sandbox_enabled=True,
    )
    if authorization == "session":
        checker.add_session_allow("Bash", command)
    decision = checker.check(Bash(), {"command": command})
    assert decision.effect == "ask"
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


class CountingBash(Bash):
    def __init__(self) -> None:
        self.count = 0

    async def execute(self, params: BashParams) -> ToolResult:
        self.count += 1
        return ToolResult("unexpected")


class CountingInstallSkill(InstallSkill):
    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    async def execute(self, params: InstallSkillParams) -> ToolResult:
        self.count += 1
        return ToolResult("unexpected")


class NonInteractiveClient(LLMClient):
    def __init__(self, tool_name: str, arguments: dict[str, Any]) -> None:
        self.tool_name = tool_name
        self.arguments = arguments
        self.calls = 0

    async def stream(self, conversation: ConversationManager, system: str = "", tools=None) -> AsyncIterator[Any]:
        self.calls += 1
        if self.calls == 1:
            yield ToolCallComplete("t1", self.tool_name, self.arguments)
            yield StreamEnd("tool_use", input_tokens=1, output_tokens=1)
        else:
            yield TextDelta("done")
            yield StreamEnd("end_turn", input_tokens=1, output_tokens=1)


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool_name", "arguments"), [
    ("Bash", {"command": "rm temp.txt"}),
    ("Bash", {"command": "git -C .. clean -fdx"}),
    ("InstallSkill", {"url": "https://github.com/example/repo/tree/main/skills/review"}),
])
async def test_noninteractive_ask_denies_without_execution(
    tmp_path: Path, tool_name: str, arguments: dict[str, Any]
) -> None:
    tool = CountingBash() if tool_name == "Bash" else CountingInstallSkill()
    registry = create_default_registry()
    registry.register(tool)
    agent = Agent(
        NonInteractiveClient(tool_name, arguments),
        registry,
        "anthropic",
        work_dir=str(tmp_path),
        permission_checker=_checker(tmp_path, mode=PermissionMode.BYPASS),
    )
    conversation = ConversationManager()
    conversation.add_user_message("run unattended")
    permission_requests = 0
    denials: list[str] = []
    async for event in agent.run(conversation):
        if isinstance(event, PermissionRequest):
            permission_requests += 1
            denials.append(_deny_noninteractive_permission(event))
    assert permission_requests == 1
    assert len(denials) == 1
    assert "Non-interactive mode denied permission" in denials[0]
    assert tool.count == 0


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


class PendingReadTool(Tool):
    name = "PendingRead"
    description = "Read until cancelled"
    params_model = EmptyParams
    category = "read"
    is_concurrency_safe = True

    def __init__(self) -> None:
        self.count = 0
        self.started = asyncio.Event()
        self.finalized = asyncio.Event()

    async def execute(self, params: EmptyParams) -> ToolResult:
        self.count += 1
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.finalized.set()
        return ToolResult("unreachable")


class PendingReadClient(LLMClient):
    def __init__(self, first_stop: str = "tool_use") -> None:
        self.first_stop = first_stop
        self.calls = 0

    async def stream(self, conversation: ConversationManager, system: str = "", tools=None) -> AsyncIterator[Any]:
        self.calls += 1
        if self.calls == 1:
            yield ToolCallComplete("t1", "PendingRead", {})
            await asyncio.sleep(0)
            yield StreamEnd(self.first_stop, input_tokens=1, output_tokens=1)
        else:
            yield TextDelta("done")
            yield StreamEnd("end_turn", input_tokens=1, output_tokens=1)


def _pending_agent(tmp_path: Path, tool: PendingReadTool, *, first_stop: str = "tool_use") -> Agent:
    registry = create_default_registry()
    registry.register(tool)
    return Agent(
        PendingReadClient(first_stop), registry, "anthropic",
        work_dir=str(tmp_path), permission_checker=_checker(tmp_path),
    )


@pytest.mark.asyncio
async def test_agent_generator_close_reaps_streaming_tool(tmp_path: Path) -> None:
    tool = PendingReadTool()
    generator = _pending_agent(tmp_path, tool).run(ConversationManager())
    while True:
        event = await anext(generator)
        if getattr(event, "tool_name", None) == "PendingRead":
            break
    await asyncio.wait_for(tool.started.wait(), timeout=1)
    await generator.aclose()
    await asyncio.wait_for(tool.finalized.wait(), timeout=1)
    assert tool.count == 1


@pytest.mark.asyncio
async def test_max_tokens_reaps_streaming_tool_before_retry(tmp_path: Path) -> None:
    tool = PendingReadTool()
    agent = _pending_agent(tmp_path, tool, first_stop="max_tokens")
    conversation = ConversationManager()
    conversation.add_user_message("read")
    async for _ in agent.run(conversation):
        pass
    assert tool.count == 1
    assert tool.finalized.is_set()


class ClosableAgent:
    def __init__(self) -> None:
        self.closed = asyncio.Event()

    async def run(self, conversation: ConversationManager) -> AsyncIterator[Any]:
        try:
            yield StreamText("first")
            yield StreamText("second")
        finally:
            self.closed.set()


@pytest.mark.asyncio
async def test_remote_cancel_closes_agent_generator() -> None:
    server = RemoteServer([])
    server.agent = ClosableAgent()  # type: ignore[assignment]
    server.conversation = ConversationManager()

    async def cancel_after_first(message: dict[str, Any]) -> None:
        if message["type"] == "stream_text" and server._cancel_event is not None:
            server._cancel_event.set()

    server._broadcast = cancel_after_first  # type: ignore[method-assign]
    await server._handle_user_message("run")
    assert server.agent.closed.is_set()  # type: ignore[union-attr]
    assert server._streaming is False


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

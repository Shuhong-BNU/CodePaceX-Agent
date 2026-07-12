from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from codepacex.memory.consolidation import LOCK_FILE, MemoryConsolidator
from codepacex.permissions import DangerousCommandDetector, PathSandbox, PermissionChecker, PermissionMode, RuleEngine
from codepacex.skills.install import MAX_FILE_SIZE, InstallReport, install_skill, parse_skill_url
from codepacex.tools.install_skill import InstallSkill, InstallSkillParams
from codepacex.tools.team_delete import TeamDeleteParams, TeamDeleteTool


def _transport(*, oversized: bool = False, fail_download: bool = False) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.github.com":
            size = MAX_FILE_SIZE + 1 if oversized else 12
            return httpx.Response(200, json=[{
                "name": "SKILL.md", "path": "skills/review/SKILL.md", "type": "file",
                "size": size, "download_url": "https://raw.githubusercontent.com/example/repo/main/skills/review/SKILL.md",
            }])
        if fail_download:
            raise httpx.ReadError("download interrupted", request=request)
        return httpx.Response(200, content=b"# Review\n")

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_skill_install_records_source_and_digest(tmp_path: Path) -> None:
    source = parse_skill_url("https://github.com/example/repo/tree/main/skills/review")
    report = await install_skill(source, root=tmp_path, transport=_transport())
    metadata = json.loads((tmp_path / "review/.source.json").read_text(encoding="utf-8"))
    assert report.file_count == 1
    assert metadata["url"] == source.original_url
    assert metadata["ref"] == "main"
    assert metadata["installed_at"] > 0
    assert metadata["sha256"] == report.sha256


@pytest.mark.asyncio
async def test_skill_install_refuses_existing_without_overwrite(tmp_path: Path) -> None:
    (tmp_path / "review").mkdir()
    source = parse_skill_url("https://github.com/example/repo/tree/main/skills/review")
    with pytest.raises(FileExistsError):
        await install_skill(source, root=tmp_path, transport=_transport())


@pytest.mark.asyncio
async def test_skill_overwrite_success_removes_backup(tmp_path: Path) -> None:
    target = tmp_path / "review"
    target.mkdir()
    (target / "old.txt").write_text("old", encoding="utf-8")
    source = parse_skill_url("https://github.com/example/repo/tree/main/skills/review")
    await install_skill(source, root=tmp_path, overwrite=True, transport=_transport())
    assert (target / "SKILL.md").exists()
    assert not (tmp_path / ".review.backup").exists()


@pytest.mark.asyncio
async def test_skill_replacement_failure_restores_previous_version(tmp_path: Path) -> None:
    target = tmp_path / "review"
    target.mkdir()
    (target / "old.txt").write_text("old", encoding="utf-8")
    source = parse_skill_url("https://github.com/example/repo/tree/main/skills/review")
    original_replace = Path.replace

    def failing_replace(self: Path, destination: Path):
        if self.name.startswith(".install-review-") and Path(destination) == target:
            raise OSError("injected replacement failure")
        return original_replace(self, destination)

    with patch.object(Path, "replace", failing_replace):
        with pytest.raises(OSError, match="injected replacement failure"):
            await install_skill(source, root=tmp_path, overwrite=True, transport=_transport())
    assert (target / "old.txt").read_text(encoding="utf-8") == "old"
    assert not (tmp_path / ".review.backup").exists()
    assert not list(tmp_path.glob(".install-review-*"))


@pytest.mark.asyncio
async def test_skill_download_failure_cleans_staging(tmp_path: Path) -> None:
    source = parse_skill_url("https://github.com/example/repo/tree/main/skills/review")
    with pytest.raises(httpx.ReadError):
        await install_skill(source, root=tmp_path, transport=_transport(fail_download=True))
    assert not list(tmp_path.glob(".install-review-*"))


@pytest.mark.asyncio
async def test_skill_limits_are_enforced(tmp_path: Path) -> None:
    source = parse_skill_url("https://github.com/example/repo/tree/main/skills/review")
    with pytest.raises(ValueError, match="safety limits"):
        await install_skill(source, root=tmp_path, transport=_transport(oversized=True))


@pytest.mark.asyncio
async def test_skill_rejects_untrusted_download_host(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{
            "name": "SKILL.md", "path": "skills/review/SKILL.md", "type": "file",
            "size": 1, "download_url": "https://evil.example/SKILL.md",
        }])

    source = parse_skill_url("https://github.com/example/repo/tree/main/skills/review")
    with pytest.raises(ValueError, match="download URL"):
        await install_skill(source, root=tmp_path, transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("url", [
    "http://github.com/example/repo/tree/main/skills/review",
    "https://example.com/example/repo/tree/main/skills/review",
    "https://github.com/example/repo/tree/feature/topic/skills/review",
    "https://raw.githubusercontent.com/example/repo/feature/topic/skills/review/SKILL.md",
])
def test_skill_url_rejects_untrusted_or_ambiguous_sources(url: str) -> None:
    with pytest.raises(ValueError):
        parse_skill_url(url)


def test_install_skill_requires_non_persistable_authorization(tmp_path: Path) -> None:
    rules = tmp_path / "rules.yaml"
    rules.write_text('- rule: "InstallSkill(*)"\n  effect: allow\n', encoding="utf-8")
    checker = PermissionChecker(
        DangerousCommandDetector(), PathSandbox(str(tmp_path)),
        RuleEngine(project_rules_path=rules), PermissionMode.BYPASS,
    )
    decision = checker.check(InstallSkill(), {"url": "https://github.com/example/repo/tree/main/skills/review"})
    assert decision.effect == "ask"
    assert decision.persistable is False


@pytest.mark.asyncio
async def test_install_callback_runs_only_after_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def success(*args, **kwargs):
        return InstallReport("review", "/tmp/review", 1, 10, "abc")

    monkeypatch.setattr("codepacex.skills.install.install_skill", success)
    tool = InstallSkill()
    tool.set_on_installed(lambda: calls.append("done"))
    result = await tool.execute(InstallSkillParams(url="https://github.com/example/repo/tree/main/skills/review"))
    assert not result.is_error
    assert calls == ["done"]


def _memory_file(path: Path, name: str) -> None:
    path.write_text(f"---\nname: {name}\ndescription: item\ntype: project\n---\nbody", encoding="utf-8")


@pytest.mark.asyncio
async def test_active_memory_lock_blocks_run(tmp_path: Path) -> None:
    memory = tmp_path / ".codepacex/memory"
    memory.mkdir(parents=True)
    _memory_file(memory / "one.md", "One")
    lock = memory / LOCK_FILE
    lock.write_text(json.dumps({"pid": os.getpid(), "created_at": time.time()}), encoding="utf-8")
    assert await MemoryConsolidator(str(tmp_path), min_hours=0, min_sessions=0).maybe_run() is False
    assert lock.exists()


@pytest.mark.asyncio
async def test_expired_active_pid_lock_is_reclaimed(tmp_path: Path) -> None:
    memory = tmp_path / ".codepacex/memory"
    memory.mkdir(parents=True)
    _memory_file(memory / "one.md", "One")
    lock = memory / LOCK_FILE
    lock.write_text(
        json.dumps({"pid": os.getpid(), "created_at": time.time() - 7200}),
        encoding="utf-8",
    )
    assert await MemoryConsolidator(str(tmp_path), min_hours=0, min_sessions=0).maybe_run() is True
    assert "One" in (memory / "MEMORY.md").read_text(encoding="utf-8")
    assert not lock.exists()
    assert not (memory / ".MEMORY.md.tmp").exists()
    assert not (memory / ".consolidate-state.tmp").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("created_at", [0, float("nan"), float("inf")])
async def test_invalid_memory_lock_timestamp_is_reclaimed(
    tmp_path: Path, created_at: float,
) -> None:
    memory = tmp_path / ".codepacex/memory"
    memory.mkdir(parents=True)
    _memory_file(memory / "one.md", "One")
    lock = memory / LOCK_FILE
    lock.write_text(
        json.dumps({"pid": os.getpid(), "created_at": created_at}),
        encoding="utf-8",
    )
    assert await MemoryConsolidator(str(tmp_path), min_hours=0, min_sessions=0).maybe_run() is True
    assert not lock.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("pid", [0, -1, "invalid"])
async def test_invalid_memory_lock_pid_is_reclaimed(tmp_path: Path, pid: int | str) -> None:
    memory = tmp_path / ".codepacex/memory"
    memory.mkdir(parents=True)
    _memory_file(memory / "one.md", "One")
    lock = memory / LOCK_FILE
    lock.write_text(
        json.dumps({"pid": pid, "created_at": time.time()}),
        encoding="utf-8",
    )
    assert await MemoryConsolidator(str(tmp_path), min_hours=0, min_sessions=0).maybe_run() is True
    assert not lock.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", ["not-json", '{"pid": 99999999, "created_at": 0}'])
async def test_stale_or_broken_memory_lock_is_reclaimed(tmp_path: Path, payload: str) -> None:
    memory = tmp_path / ".codepacex/memory"
    memory.mkdir(parents=True)
    _memory_file(memory / "one.md", "One")
    (memory / LOCK_FILE).write_text(payload, encoding="utf-8")
    assert await MemoryConsolidator(str(tmp_path), min_hours=0, min_sessions=0).maybe_run() is True
    assert not (memory / LOCK_FILE).exists()


@pytest.mark.asyncio
async def test_memory_failure_preserves_index_and_state(tmp_path: Path) -> None:
    memory = tmp_path / ".codepacex/memory"
    memory.mkdir(parents=True)
    _memory_file(memory / "one.md", "One")
    index = memory / "MEMORY.md"
    index.write_text("original\n", encoding="utf-8")
    original_replace = Path.replace

    def fail_state(self: Path, destination: Path):
        if self.name == ".consolidate-state.tmp":
            raise OSError("injected state failure")
        return original_replace(self, destination)

    with patch.object(Path, "replace", fail_state):
        result = await MemoryConsolidator(str(tmp_path), min_hours=0, min_sessions=0).maybe_run()
    assert result is False
    assert index.read_text(encoding="utf-8") == "original\n"
    assert not (memory / ".consolidate-state").exists()


@pytest.mark.asyncio
async def test_memory_read_failure_releases_lock_and_allows_retry(tmp_path: Path) -> None:
    memory = tmp_path / ".codepacex/memory"
    memory.mkdir(parents=True)
    _memory_file(memory / "one.md", "One")
    index = memory / "MEMORY.md"
    index.write_text("original\n", encoding="utf-8")
    original_read_bytes = Path.read_bytes

    def fail_index_read(self: Path) -> bytes:
        if self == index:
            raise OSError("injected index read failure")
        return original_read_bytes(self)

    consolidator = MemoryConsolidator(str(tmp_path), min_hours=0, min_sessions=0)
    with patch.object(Path, "read_bytes", fail_index_read):
        assert await consolidator.maybe_run() is False
    assert index.read_text(encoding="utf-8") == "original\n"
    assert not (memory / LOCK_FILE).exists()
    assert not (memory / ".MEMORY.md.tmp").exists()
    assert not (memory / ".consolidate-state.tmp").exists()
    assert await consolidator.maybe_run() is True


@pytest.mark.asyncio
async def test_concurrent_memory_run_executes_once(tmp_path: Path) -> None:
    memory = tmp_path / ".codepacex/memory"
    memory.mkdir(parents=True)
    _memory_file(memory / "one.md", "One")
    consolidator = MemoryConsolidator(str(tmp_path), min_hours=0, min_sessions=0)
    results = await asyncio.gather(consolidator.maybe_run(), consolidator.maybe_run())
    assert sorted(results) == [False, True]


@pytest.mark.asyncio
async def test_project_consolidation_excludes_user_memory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project_memory = project / ".codepacex/memory"
    user_memory = home / ".codepacex/memory"
    project_memory.mkdir(parents=True)
    user_memory.mkdir(parents=True)
    _memory_file(project_memory / "project.md", "ProjectOnly")
    _memory_file(user_memory / "user.md", "UserOnly")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    assert await MemoryConsolidator(str(project), min_hours=0, min_sessions=0).maybe_run() is True
    index = (project_memory / "MEMORY.md").read_text(encoding="utf-8")
    assert "ProjectOnly" in index
    assert "UserOnly" not in index


def test_deleting_one_team_keeps_other_team_isolated(tmp_path: Path) -> None:
    from codepacex.teams.manager import TeamManager

    with patch("codepacex.teams.models.Path.home", return_value=tmp_path):
        manager = TeamManager()
        first = manager.create_team("first", "lead", teammate_mode="in-process", is_interactive=False)
        second = manager.create_team("second", "lead", teammate_mode="in-process", is_interactive=False)
        manager.delete_team(first.name)
        assert manager.get_team(second.name) is not None
        assert manager.get_mailbox(second.name) is not None
        assert manager.get_task_store(second.name) is not None


@pytest.mark.asyncio
async def test_team_cleanup_failure_returns_error_and_keeps_team_state(tmp_path: Path) -> None:
    from codepacex.teams.manager import TeamManager

    with patch("codepacex.teams.models.Path.home", return_value=tmp_path):
        manager = TeamManager()
        team = manager.create_team("first", "lead", teammate_mode="in-process", is_interactive=False)
        with patch.object(manager, "_remove_dir", side_effect=OSError("cleanup failed")):
            result = await TeamDeleteTool(manager).execute(TeamDeleteParams(team_name=team.name))
        assert result.is_error
        assert "cleanup failed" in result.output
        assert manager.get_team(team.name) is not None

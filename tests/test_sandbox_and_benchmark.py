from __future__ import annotations

import asyncio
import json
from pathlib import Path

from codepacex.sandbox import SandboxConfig
from codepacex.sandbox.bwrap import BwrapSandbox
from codepacex.sandbox.seatbelt import build_profile
from codepacex.tools.diff import build_diff
from codepacex.memory.consolidation import MemoryConsolidator
from codepacex.skills.install import parse_skill_url
from evals.benchmark import RunManifest, RunRecorder, reduction_percent, summarize
from evals.swe_bench_live import select_instances


def test_seatbelt_profile_denies_network_and_allows_project_write(tmp_path):
    profile = build_profile(SandboxConfig(allow_write=[str(tmp_path)]))
    assert '(allow file-write*' in profile
    assert '(deny network*)' in profile


def test_bwrap_command_is_network_isolated(tmp_path):
    command = BwrapSandbox().wrap("echo ok", SandboxConfig(allow_write=[str(tmp_path)]))
    assert "--unshare-net" in command
    assert "echo ok" in command


def test_diff_reports_unicode_and_truncation():
    result = build_diff("甲\nold\n", "甲\nnew\n")
    assert result.additions == 1
    assert result.removals == 1
    assert "+new" in result.text


def test_benchmark_artifacts_redact_and_summarize(tmp_path):
    recorder = RunRecorder(tmp_path, RunManifest(kind="pilot", model="model", provider="provider"))
    recorder.event("usage", {"api_key": "secret", "input_tokens": 12})
    recorder.finalize({"success": True})
    event = (recorder.path / "events.jsonl").read_text(encoding="utf-8")
    assert "secret" not in event
    assert summarize([1, 2, 3])["median"] == 2
    assert reduction_percent(100, 15) == 85


def test_run_recorder_reports_incomplete_attempts_and_rejects_duplicate_terminals(tmp_path):
    recorder = RunRecorder(tmp_path, RunManifest(kind="pilot", model="model", provider="provider"))
    recorder.event("trial_started", {
        "task_id": "completed", "repetition_id": "1", "attempt_id": 1,
    })
    recorder.event("trial_completed", {
        "task_id": "completed", "repetition_id": "1", "attempt_id": 1,
        "status": "task_failure",
    })
    recorder.event("trial_started", {
        "task_id": "interrupted", "repetition_id": "1", "attempt_id": 1,
    })
    assert recorder.incomplete_trial_attempts() == {("interrupted", "1", 1)}
    assert recorder.terminal_trial_statuses() == {("completed", "1"): "task_failure"}
    recorder.event("trial_completed", {
        "task_id": "completed", "repetition_id": "1", "attempt_id": 2,
        "status": "task_failure",
    })
    try:
        recorder.terminal_trial_statuses()
    except ValueError as exc:
        assert "duplicate terminal Trial event" in str(exc)
    else:
        raise AssertionError("duplicate terminal trial must be rejected")


def test_swe_selection_limits_repositories():
    items = [
        {"instance_id": f"a-{index}", "repo": "repo-a", "platform": "linux"}
        for index in range(3)
    ] + [
        {"instance_id": "b-1", "repo": "repo-b", "platform": "linux"},
        {"instance_id": "windows", "repo": "repo-c", "platform": "windows"},
    ]
    selected = select_instances(items, limit=20)
    assert len([item for item in selected if item["repo"] == "repo-a"]) == 2
    assert all(item["platform"] == "linux" for item in selected)


def test_consolidation_rebuilds_deduplicated_memory_index(tmp_path):
    memory_dir = tmp_path / ".codepacex" / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "one.md").write_text("---\nname: Design\ndescription: first\ntype: project\n---\nbody", encoding="utf-8")
    (memory_dir / "two.md").write_text("---\nname: design\ndescription: duplicate\ntype: project\n---\nbody", encoding="utf-8")
    consolidator = MemoryConsolidator(str(tmp_path), min_hours=0, min_sessions=0)
    asyncio.run(consolidator.maybe_run())
    index = (memory_dir / "MEMORY.md").read_text(encoding="utf-8")
    assert index.count("Design") == 1


def test_skill_url_parser_rejects_untrusted_or_invalid_urls():
    source = parse_skill_url("https://github.com/example/repo/tree/main/skills/review")
    assert source.name == "review"
    try:
        parse_skill_url("http://example.com/skill")
    except ValueError:
        pass
    else:
        raise AssertionError("non-HTTPS URL must be rejected")

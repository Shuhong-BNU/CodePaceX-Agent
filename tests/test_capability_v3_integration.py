from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any, AsyncIterator

from codepacex.agent import Agent, PermissionDecisionEvent
from codepacex.capability_v3 import CapabilityV3Config, CapabilityV3Controller, CapabilityV3Flag
from codepacex.client import LLMClient
from codepacex.permissions import DangerousCommandDetector, PathSandbox, PermissionChecker, PermissionMode, RuleEngine
from codepacex.tools import create_default_registry
from codepacex.tools.base import StreamEnd, StreamEvent, TextDelta, ToolCallComplete
from codepacex.tools.run_test import RunTest
from codepacex.conversation import ConversationManager
from evals.evaluation_v2 import control_canary


class _SyntheticClient(LLMClient):
    def __init__(self, workspace: Path, source: Path) -> None:
        self.workspace = workspace
        self.source = source
        self.calls = 0

    async def stream(
        self, _conversation: Any, system: str = "", tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.calls += 1
        if self.calls == 1:
            yield ToolCallComplete("read", "ReadFile", {"file_path": str(self.source), "offset": 0, "limit": 10})
        elif self.calls == 2:
            yield ToolCallComplete("edit", "EditFile", {
                "file_path": str(self.source), "old_string": "VALUE = 0", "new_string": "VALUE = 1",
            })
        elif self.calls == 3:
            yield ToolCallComplete("test", "RunTest", {
                "cwd": str(self.workspace), "argv": ["test_source.py"], "timeout_seconds": 30,
            })
        else:
            yield TextDelta("synthetic run complete")
        yield StreamEnd("end_turn")


class _DoneClient(LLMClient):
    async def stream(
        self, _conversation: Any, system: str = "", tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        yield TextDelta("done")
        yield StreamEnd("end_turn")


def _git_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "source.py").write_text("VALUE = 0\n", encoding="utf-8")
    (workspace / "test_source.py").write_text(
        "import source\n\ndef test_value():\n    assert source.VALUE == 1\n", encoding="utf-8",
    )
    for command in (
        ["git", "init"],
        ["git", "config", "user.email", "v3@example.test"],
        ["git", "config", "user.name", "Capability V3"],
        ["git", "add", "source.py", "test_source.py"],
        ["git", "commit", "-m", "base"],
    ):
        subprocess.run(command, cwd=workspace, check=True, capture_output=True, text=True)
    return workspace


def _agent(client: LLMClient, workspace: Path, artifact_root: Path, *, controller: CapabilityV3Controller | None = None) -> Agent:
    registry = create_default_registry()
    registry.register(RunTest())
    checker = PermissionChecker(
        DangerousCommandDetector(), PathSandbox(str(workspace)), RuleEngine(),
        PermissionMode.DEFAULT, session_allow_all=True,
    )
    return Agent(
        client, registry, "openai-compat", work_dir=str(workspace), permission_checker=checker,
        capability_v3_config=CapabilityV3Config.from_flag(CapabilityV3Flag.V3_CORE),
        capability_v3_controller=controller,
        capability_v3_flag=CapabilityV3Flag.V3_CORE.value,
        capability_v3_artifact_root=artifact_root,
        capability_v3_task_id="synthetic-task",
        capability_v3_base_commit="base",
    )


def test_v3_flag_drives_agent_lifecycle_and_artifact_without_provider(tmp_path: Path) -> None:
    workspace = _git_workspace(tmp_path)
    artifact_root = tmp_path / "artifact"
    agent = _agent(_SyntheticClient(workspace, workspace / "source.py"), workspace, artifact_root)

    asyncio.run(agent.run_to_completion("Update source VALUE and validate it."))

    summary = json.loads((artifact_root / "summary.json").read_text(encoding="utf-8"))
    event_types = [event["event_type"] for event in summary["events"]]
    assert summary["config"]["enabled"] is True
    assert "V3RunConfigured" in event_types
    assert "EvidenceCollected" in event_types
    assert "ImpactSliceBuilt" in event_types
    assert "CandidateSnapshotCreated" in event_types
    assert "CandidatePromoted" in event_types
    assert "V3Completed" in event_types
    assert (artifact_root / "events.jsonl").is_file()
    assert (artifact_root / "final.patch").read_text(encoding="utf-8") == subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff"], cwd=workspace,
        check=True, capture_output=True, text=True,
    ).stdout


def test_streaming_lifecycle_snapshots_edit_and_promotes_after_test(tmp_path: Path) -> None:
    workspace = _git_workspace(tmp_path)
    task_root = tmp_path / "task"
    artifact_root = task_root / "capability-v3"
    agent = _agent(_SyntheticClient(workspace, workspace / "source.py"), workspace, artifact_root)
    conversation = ConversationManager()
    conversation.add_user_message("Update source VALUE and validate it.")

    decisions: list[PermissionDecisionEvent] = []

    async def consume() -> None:
        async for event in agent.run(conversation):
            if isinstance(event, PermissionDecisionEvent):
                decisions.append(event)

    asyncio.run(consume())

    summary = json.loads((artifact_root / "summary.json").read_text(encoding="utf-8"))
    event_types = [event["event_type"] for event in summary["events"]]
    candidates = summary["derived_state"]["candidate_snapshots"]
    assert "CandidateSnapshotCreated" in event_types
    assert "CandidatePromoted" in event_types
    assert [candidate["level"] for candidate in candidates] == ["C1", "C2"]
    execution_paths = {event.tool_name: event.execution_path for event in decisions}
    assert execution_paths["EditFile"] == "sequential"
    assert execution_paths["RunTest"] == "sequential"
    assert (artifact_root / "final.patch").read_text(encoding="utf-8").strip()
    assert control_canary._validate_capability_v3_artifact(
        task_root=task_root,
        instance_id="synthetic-task",
        treatment=CapabilityV3Flag.V3_CORE,
    )["valid"] is True


def test_streaming_finalization_retains_auditable_workspace_diff_when_bookkeeping_is_missing(
    tmp_path: Path,
) -> None:
    workspace = _git_workspace(tmp_path)
    (workspace / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
    task_root = tmp_path / "task"
    artifact_root = task_root / "capability-v3"
    agent = _agent(_DoneClient(), workspace, artifact_root)
    conversation = ConversationManager()
    conversation.add_user_message("Finish the already-applied workspace change.")

    async def consume() -> None:
        async for _event in agent.run(conversation):
            pass

    asyncio.run(consume())

    summary = json.loads((artifact_root / "summary.json").read_text(encoding="utf-8"))
    events = summary["events"]
    fallback = next(event for event in events if event["event_type"] == "WorkspaceDiffFallbackRetained")
    completed = next(event for event in reversed(events) if event["event_type"] == "V3Completed")
    assert summary["derived_state"]["candidate_snapshots"] == []
    assert fallback["payload"]["audit_only"] is True
    assert fallback["payload"]["reason"] == "candidate_bookkeeping_missing"
    assert completed["payload"]["candidate_status"] == "audit_only_workspace_diff"
    assert (artifact_root / "workspace-diff-fallback.patch").read_text(encoding="utf-8").strip()
    assert (artifact_root / "final.patch").read_text(encoding="utf-8").strip()
    assert control_canary._validate_capability_v3_artifact(
        task_root=task_root,
        instance_id="synthetic-task",
        treatment=CapabilityV3Flag.V3_CORE,
    )["valid"] is True


def test_streaming_agent_lifecycle_writes_raw_v3_artifact_without_provider(tmp_path: Path) -> None:
    workspace = _git_workspace(tmp_path)
    artifact_root = tmp_path / "streaming-artifact"
    stable_patch = artifact_root / "stable.patch"
    stable_patch.parent.mkdir()
    stable_patch.write_text("diff --git a/source.py b/source.py\n+streaming\n", encoding="utf-8")
    controller = CapabilityV3Controller(
        CapabilityV3Config.from_flag(CapabilityV3Flag.V3_CORE), state_dir=artifact_root,
    )
    controller.observe_diff(diff_text="streaming", changed_files=["source.py"], patch_path=stable_patch)
    agent = _agent(_DoneClient(), workspace, artifact_root, controller=controller)
    conversation = ConversationManager()
    conversation.add_user_message("Update source VALUE and validate it.")

    async def consume() -> None:
        async for _event in agent.run(conversation):
            pass

    asyncio.run(consume())

    summary = json.loads((artifact_root / "summary.json").read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (artifact_root / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    configured = [item for item in events if item["event_type"] == "V3RunConfigured"]
    assert summary["events"] == events and events
    assert configured[0]["payload"]["task_id"] == "synthetic-task"
    assert configured[0]["payload"]["feature_flag"] == CapabilityV3Flag.V3_CORE.value
    assert (artifact_root / "final.patch").read_text(encoding="utf-8") == stable_patch.read_text(encoding="utf-8")


def test_final_export_prefers_stable_candidate_over_later_workspace_wip(tmp_path: Path) -> None:
    workspace = _git_workspace(tmp_path)
    artifact_root = tmp_path / "artifact"
    stable_patch = artifact_root / "stable.patch"
    stable_patch.parent.mkdir()
    stable_patch.write_text("diff --git a/source.py b/source.py\n+stable\n", encoding="utf-8")
    controller = CapabilityV3Controller(
        CapabilityV3Config.from_flag(CapabilityV3Flag.V3_CORE), state_dir=artifact_root,
    )
    controller.observe_diff(diff_text="stable", changed_files=["source.py"], patch_path=stable_patch)
    controller.observe_test_result(passed=True, test_evidence_id="stable-test")
    (workspace / "source.py").write_text("VALUE = 99\n", encoding="utf-8")

    asyncio.run(_agent(_DoneClient(), workspace, artifact_root, controller=controller).run_to_completion("finish"))

    assert (artifact_root / "final.patch").read_text(encoding="utf-8") == stable_patch.read_text(encoding="utf-8")
    assert "VALUE = 99" in subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff"], cwd=workspace,
        check=True, capture_output=True, text=True,
    ).stdout

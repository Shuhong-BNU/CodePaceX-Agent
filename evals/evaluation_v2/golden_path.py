"""Real zero-provider Coding Agent Golden Path for Evaluation V2.

The replay client is deterministic but it drives the normal Agent loop.  It is
not a shortcut around tool execution: every read, search, test, and edit is
dispatched by :class:`codepacex.agent.Agent` and recorded in the run artifact.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Sequence

from codepacex.agent import Agent
from codepacex.client import LLMClient
from codepacex.permissions import (
    DangerousCommandDetector,
    PathSandbox,
    PermissionChecker,
    PermissionMode,
    RuleEngine,
)
from codepacex.tools import create_default_registry
from codepacex.tools.base import StreamEnd, StreamEvent, TextDelta, ToolCallComplete, ToolResult
from codepacex.tools.run_test import RunTest, RunTestParams
from evals.goal3_swe import collect_goal3_official_outcome
from evals.swe_bench_live import official_evaluator_report_path, run_official_evaluator


SCHEMA_VERSION = 1
TASK = {
    "instance_id": "beetbox__beets-5495",
    "repo": "beetbox/beets",
    "base_commit": "fa10dcf11add0afd3b4b22af29f8d504e7ef8a0a",
    "dataset_name": "SWE-bench-Live/SWE-bench-Live",
    "split": "lite",
}
OFFICIAL_EVALUATOR_COMMIT = "ad79b850f15e33992e96f03f6e97f05ddf9aa0be"
MODEL_ID = "codepacex-evaluation-v2-replay"
MARKER = "# CodePaceX Evaluation V2 deterministic replay marker"
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def task_contract() -> dict[str, str]:
    """Return the non-gold task identity used by this Golden Path."""
    return dict(TASK)


def materialize_workspace(workspace: Path) -> dict[str, str]:
    """Clone the real task repository at its frozen base without reading gold data."""
    workspace = workspace.resolve()
    if workspace.exists():
        raise ValueError(f"workspace already exists: {workspace}")
    workspace.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--filter=blob:none", f"https://github.com/{TASK['repo']}.git", str(workspace)],
        text=True,
        capture_output=True,
        check=True,
    )
    subprocess.run(["git", "checkout", "--detach", TASK["base_commit"]], cwd=workspace, text=True, capture_output=True, check=True)
    actual = subprocess.run(["git", "rev-parse", "HEAD"], cwd=workspace, text=True, capture_output=True, check=True).stdout.strip()
    if actual != TASK["base_commit"]:
        raise ValueError("materialized workspace does not match the frozen task base commit")
    return {"workspace": str(workspace), "instance_id": TASK["instance_id"], "base_commit": actual}


def _first_read_line(messages: list[Any]) -> str:
    for message in messages:
        for block in getattr(message, "tool_results", []):
            content = str(getattr(block, "content", ""))
            first = content.splitlines()[0] if content else ""
            if "\t" in first:
                return first.split("\t", 1)[1]
    raise RuntimeError("replay did not receive the real ReadFile result")


class ReplayClient(LLMClient):
    """A local, fixed sequence of native Agent protocol events.

    It has no Provider configuration, transport, request accounting, or secret
    access.  The edit argument is derived from the preceding *tool result*, so
    the replay does not read or edit the workspace outside the Agent loop.
    """

    def __init__(self, workspace: Path, target_file: Path) -> None:
        self.workspace = workspace
        self.target_file = target_file
        self.calls = 0

    async def stream(self, conversation: Any, system: str = "", tools: list[dict[str, Any]] | None = None) -> AsyncIterator[StreamEvent]:
        self.calls += 1
        workspace = str(self.workspace)
        if self.calls == 1:
            yield TextDelta("Read the selected repository source file.")
            yield ToolCallComplete("read-source", "ReadFile", {"file_path": str(self.target_file), "offset": 0, "limit": 1})
        elif self.calls == 2:
            yield TextDelta("Search the materialized repository.")
            yield ToolCallComplete("search-source", "Grep", {"pattern": "def ", "path": workspace, "include": "*.py"})
        elif self.calls == 3:
            yield TextDelta("Run the deterministic pre-edit validation.")
            yield ToolCallComplete("test-pre", "RunTest", {"cwd": workspace, "argv": ["test/test_importer.py::ImportTest::test_set_fields"], "timeout_seconds": 120, "output_cap_chars": 12000})
        elif self.calls == 4:
            line = _first_read_line(conversation.get_messages())
            yield TextDelta("Apply the fixed non-gold marker through EditFile.")
            yield ToolCallComplete("edit-marker", "EditFile", {"file_path": str(self.target_file), "old_string": line, "new_string": f"{line}\n{MARKER}"})
        elif self.calls == 5:
            yield TextDelta("Run the deterministic post-edit validation.")
            yield ToolCallComplete("test-post", "RunTest", {"cwd": workspace, "argv": ["test/test_importer.py::ImportTest::test_set_fields"], "timeout_seconds": 120, "output_cap_chars": 12000})
        else:
            yield TextDelta("Replay completed its read, search, test, edit, and post-edit test sequence.")
        yield StreamEnd("end_turn", input_tokens=0, output_tokens=0)


class RecordingRunTest(RunTest):
    """Native RunTest with artifact-only observation of each executed result."""

    def __init__(self) -> None:
        self.results: list[ToolResult] = []

    async def execute(self, params: RunTestParams) -> ToolResult:
        result = await super().execute(params)
        self.results.append(result)
        return result


def _target_file(workspace: Path) -> Path:
    candidate = workspace / "beets" / "importer.py"
    if not candidate.is_file():
        raise ValueError(f"expected frozen task source file is missing: {candidate}")
    return candidate


def _git_diff(workspace: Path) -> str:
    process = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--binary", "--", "beets/importer.py"],
        cwd=workspace,
        text=True,
        capture_output=True,
        check=True,
    )
    return process.stdout


def _zero_provider_ledger(run_id: str) -> dict[str, Any]:
    reservation_id = f"evaluation-v2-replay-{run_id}"
    return {
        "schema_version": SCHEMA_VERSION,
        "provider_requests": 0,
        "usage": 0,
        "charge_cny": "0",
        "settlement_added": 0,
        "active_reservation": None,
        "reservations": [{
            "reservation_id": reservation_id,
            "reserved_cny": "0",
            "status": "closed_zero_provider",
            "reason": "deterministic_replay_has_no_provider_transport",
        }],
        "provider_secret_read": False,
    }


@dataclass
class GoldenPathResult:
    artifact_root: Path
    summary: dict[str, Any]


def run_golden_path(*, workspace: Path, artifact_root: Path, run_id: str, evaluator_commit: str = OFFICIAL_EVALUATOR_COMMIT) -> GoldenPathResult:
    """Execute the full zero-provider Golden Path, including the official evaluator."""
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("invalid Golden Path run ID")
    workspace, artifact_root = workspace.resolve(), artifact_root.resolve()
    if not (workspace / ".git").exists():
        raise ValueError("Golden Path requires a materialized Git workspace")
    if artifact_root.exists():
        raise ValueError(f"artifact root already exists: {artifact_root}")
    artifact_root.mkdir(parents=True, exist_ok=False)
    _write_json(artifact_root / "golden-path-contract.json", {
        "schema_version": SCHEMA_VERSION,
        "lane": "base",
        "task": task_contract(),
        "provider": "none",
        "replay": "deterministic-native-agent-tool-calls",
        "official_evaluator_commit": evaluator_commit,
        "gold_patch_used": False,
        "historical_evidence_modified": False,
    })
    _write_json(artifact_root / "manifest.json", {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "created_at": _utc_now(),
        "task": task_contract(),
        "workspace_base_commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=workspace, text=True, capture_output=True, check=True).stdout.strip(),
        "official_evaluator_commit": evaluator_commit,
        "provider": "none",
    })
    _write_json(artifact_root / "environment.json", {
        "python_version": platform.python_version(),
        "operating_system": platform.system(),
        "architecture": platform.machine(),
        "provider_secret_read": False,
        "provider_environment_accessed": False,
    })

    target = _target_file(workspace)
    replay = ReplayClient(workspace, target)
    registry = create_default_registry()
    recorded_tests = RecordingRunTest()
    registry.register(recorded_tests)
    checker = PermissionChecker(
        DangerousCommandDetector(), PathSandbox(str(workspace)), RuleEngine(),
        PermissionMode.DEFAULT, session_allow_all=True,
    )
    agent = Agent(replay, registry, "openai-compat", work_dir=str(workspace), permission_checker=checker, max_iterations=8)
    trace: list[dict[str, Any]] = []
    permissions: list[dict[str, Any]] = []

    def record(event: dict[str, Any]) -> None:
        if event.get("type") == "tool_use":
            trace.append(event)
        elif event.get("type") == "permission_decision":
            permissions.append(event)

    agent_result = asyncio.run(agent.run_to_completion("Execute the Evaluation V2 deterministic Golden Path.", event_callback=record))
    _write_json(artifact_root / "deterministic-replay-input.json", {
        "tool_sequence": ["ReadFile", "Grep", "RunTest", "EditFile", "RunTest"],
        "marker": MARKER,
        "provider_requests": 0,
    })
    _write_json(artifact_root / "agent-tool-trace.json", trace)
    _write_json(artifact_root / "permission-decisions.json", permissions)
    if [event.get("toolName") for event in trace] != ["ReadFile", "Grep", "RunTest", "EditFile", "RunTest"]:
        raise RuntimeError("Agent tool trace differs from the deterministic Golden Path contract")
    if len(recorded_tests.results) != 2:
        raise RuntimeError("Golden Path did not execute both validation tests")
    for name, result in zip(("pre-edit-test.txt", "post-edit-test.txt"), recorded_tests.results):
        (artifact_root / name).write_text(result.output, encoding="utf-8")

    diff = _git_diff(workspace)
    if not diff.strip() or MARKER not in diff:
        raise RuntimeError("Agent did not produce the required non-gold workspace diff")
    candidate = [{"instance_id": TASK["instance_id"], "model_name_or_path": MODEL_ID, "model_patch": diff}]
    candidate_path = artifact_root / "candidate.json"
    _write_json(candidate_path, candidate)
    diff_path = artifact_root / "workspace.diff"
    diff_path.write_text(diff, encoding="utf-8")
    candidate_sha = _sha256_bytes(diff.encode("utf-8"))
    diff_sha = _sha256_file(diff_path)
    if candidate_sha != diff_sha:
        raise RuntimeError("Candidate SHA does not bind the exact workspace diff")

    ledger = _zero_provider_ledger(run_id)
    _write_json(artifact_root / "zero-provider-ledger.json", ledger)
    evaluator_root = artifact_root / "official-evaluator"
    evaluator_root.mkdir(parents=True, exist_ok=True)
    completed = run_official_evaluator(
        cwd=evaluator_root,
        dataset_name=TASK["dataset_name"], split=TASK["split"], predictions_path=candidate_path,
        instance_ids=[TASK["instance_id"]], max_workers=1, run_id=run_id,
        namespace="starryzhang",
    )
    (artifact_root / "official-evaluator.stdout.txt").write_text(completed.stdout or "", encoding="utf-8")
    (artifact_root / "official-evaluator.stderr.txt").write_text(completed.stderr or "", encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(f"official evaluator exited {completed.returncode}")
    report_path = official_evaluator_report_path(
        cwd=evaluator_root, run_id=run_id, model_id=MODEL_ID, instance_id=TASK["instance_id"],
    )
    shutil.copyfile(report_path, artifact_root / "official-report.json")
    resolved = collect_goal3_official_outcome(report_path, TASK["instance_id"])
    report_sha = _sha256_file(artifact_root / "official-report.json")
    summary = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "task": TASK["instance_id"],
        "agent_status": "completed",
        "candidate_status": "exported_nonempty",
        "validation_status": "pre_and_post_executed",
        "evaluator_status": "resolved" if resolved else "unresolved",
        "runner_status": "completed",
        "provider_status": "zero_provider",
        "real_agent_loop": True,
        "real_edit_or_write": 1,
        "pre_edit_test_executed": True,
        "post_edit_test_executed": True,
        "pre_edit_test_exit_code": recorded_tests.results[0].exit_code,
        "post_edit_test_exit_code": recorded_tests.results[1].exit_code,
        "candidate_nonempty": True,
        "candidate_sha256": candidate_sha,
        "workspace_diff_sha256": diff_sha,
        "candidate_matches_workspace_diff": True,
        "official_evaluator_executed": True,
        "official_evaluator_commit": evaluator_commit,
        "official_report_generated": True,
        "official_outcome": "resolved" if resolved else "unresolved",
        "official_report_sha256": report_sha,
        "provider_requests": 0,
        "usage": 0,
        "charge_cny": "0",
        "settlement_added": 0,
        "settlements": 0,
        "active_reservation": None,
        "provider_secret_read": False,
        "historical_evidence_modified": False,
        "failure_classification": None,
        "agent_final_text": agent_result,
    }
    _write_json(artifact_root / "structured-summary.json", summary)
    return GoldenPathResult(artifact_root, summary)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Evaluation V2 zero-provider Golden Path")
    sub = parser.add_subparsers(dest="command", required=True)
    materialize = sub.add_parser("materialize")
    materialize.add_argument("--workspace", type=Path, required=True)
    run = sub.add_parser("run")
    run.add_argument("--workspace", type=Path, required=True)
    run.add_argument("--artifact-root", type=Path, required=True)
    run.add_argument("--run-id", required=True)
    run.add_argument("--evaluator-commit", default=OFFICIAL_EVALUATOR_COMMIT)
    args = parser.parse_args(argv)
    if args.command == "materialize":
        result: Any = materialize_workspace(args.workspace)
    else:
        try:
            result = run_golden_path(
                workspace=args.workspace,
                artifact_root=args.artifact_root,
                run_id=args.run_id,
                evaluator_commit=args.evaluator_commit,
            ).summary
        except Exception as exc:
            if args.artifact_root.exists():
                _write_json(args.artifact_root / "failure-classification.json", {
                    "runner_status": "error",
                    "failure_classification": "runner_error",
                    "message": str(exc),
                    "provider_requests": 0,
                    "usage": 0,
                    "charge_cny": "0",
                    "active_reservation": None,
                })
            raise
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

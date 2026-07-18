"""Run the lightweight CodePaceX agent eval suite."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

try:
    from evals.graders import (
        GraderResult,
        diff_snapshots,
        extract_metrics,
        load_trace,
        run_graders,
        snapshot_files,
    )
except ModuleNotFoundError:
    from graders import (
        GraderResult,
        diff_snapshots,
        extract_metrics,
        load_trace,
        run_graders,
        snapshot_files,
    )


EVALS_DIR = Path(__file__).resolve().parent
DEFAULT_TASKS_DIR = EVALS_DIR / "tasks"
DEFAULT_REPORT_DIR = EVALS_DIR / ".runs"


@contextlib.contextmanager
def chdir(path: Path):
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def run_git(repo_root: Path, args: list[str]) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def find_repo_root() -> Path:
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=EVALS_DIR,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return Path(proc.stdout.strip()).resolve()
    cur = EVALS_DIR
    while cur != cur.parent:
        if (cur / "pyproject.toml").exists() and (cur / "codepacex").is_dir():
            return cur
        cur = cur.parent
    raise RuntimeError("Could not determine CodePaceX repo root")


def source_preflight(repo_root: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root) + os.pathsep + env.get("PYTHONPATH", "")
    code = (
        "import codepacex, pathlib, sys; "
        "print(sys.executable); "
        "print(pathlib.Path(codepacex.__file__).resolve())"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    lines = proc.stdout.strip().splitlines()
    source_file = Path(lines[1]).resolve() if len(lines) >= 2 else Path()
    if proc.returncode != 0 or not source_file.is_relative_to(repo_root):
        raise RuntimeError(
            "Subprocess would not import codepacex from current checkout: "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
    return {
        "python_executable": lines[0] if lines else sys.executable,
        "codepacex_source_file": str(source_file),
    }


def sha256_file(path: Path) -> str:
    return __import__("hashlib").sha256(path.read_bytes()).hexdigest()


def config_metadata(workspace: Path) -> dict[str, Any]:
    home = Path.home()
    candidates = [
        home / ".codepacex" / "config.yaml",
        workspace / ".codepacex" / "config.yaml",
        workspace / ".codepacex" / "config.local.yaml",
    ]
    sources = [
        {"path": str(path), "sha256": sha256_file(path)}
        for path in candidates
        if path.exists()
    ]
    permission_candidates = [
        home / ".codepacex" / "permissions.yaml",
        workspace / ".codepacex" / "permissions.yaml",
        workspace / ".codepacex" / "permissions.local.yaml",
    ]
    permission_sources = [
        {"path": str(path), "sha256": sha256_file(path)}
        for path in permission_candidates
        if path.exists()
    ]
    provider: dict[str, Any] = {}
    try:
        from codepacex.config import load_config

        with chdir(workspace):
            cfg = load_config()
        if cfg.providers:
            primary = cfg.providers[0]
            provider = {
                "name": primary.name,
                "protocol": primary.protocol,
                "model": primary.model,
                "fallback": list(cfg.fallback),
                "permission_mode": cfg.permission_mode,
                "hooks_configured": bool(cfg.raw_hooks),
            }
    except Exception as exc:
        provider = {"error": str(exc)}
    return {
        "config_sources": sources,
        "permission_sources": permission_sources,
        "provider": provider,
    }


def load_task(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Task file must contain a mapping: {path}")
    data["_path"] = str(path)
    return data


def discover_tasks(tasks_dir: Path, selected: list[str]) -> list[dict[str, Any]]:
    task_paths = sorted(tasks_dir.glob("*.yaml"))
    tasks = [load_task(path) for path in task_paths]
    if selected:
        selected_set = set(selected)
        tasks = [task for task in tasks if task.get("id") in selected_set]
    return tasks


def copy_fixture(repo_root: Path, task: dict[str, Any], workspace: Path) -> None:
    fixture_name = str(task["fixture"])
    src = EVALS_DIR / "fixtures" / fixture_name
    if not src.is_dir():
        raise FileNotFoundError(f"Fixture not found: {src}")
    shutil.copytree(src, workspace, dirs_exist_ok=True)


def run_agent(
    repo_root: Path,
    workspace: Path,
    task: dict[str, Any],
    trace_path: Path,
    stderr_path: Path,
    experiment_profile: Path | None = None,
) -> tuple[int | None, int, bool]:
    execution = task.get("execution") if isinstance(task.get("execution"), dict) else {}
    mode = str(execution.get("permission_mode", "default"))
    timeout = int(execution.get("timeout_seconds", 420))
    prompt = str(task.get("prompt", ""))
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root) + os.pathsep + env.get("PYTHONPATH", "")
    command = [
        sys.executable,
        "-m",
        "codepacex",
        "--mode",
        mode,
        "-p",
        prompt,
        "--output-format",
        "stream-json",
    ]
    if experiment_profile is not None:
        command.extend(["--experiment-profile", str(experiment_profile)])
    start = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            cwd=workspace,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        trace_path.write_text(proc.stdout, encoding="utf-8")
        stderr_path.write_text(proc.stderr, encoding="utf-8")
        return proc.returncode, duration_ms, False
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode(errors="replace")
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode(errors="replace")
        trace_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")
        return None, duration_ms, True


def grader_to_dict(result: GraderResult) -> dict[str, Any]:
    return {
        "name": result.name,
        "passed": result.passed,
        "summary": result.summary,
        "details": result.details,
    }


EXECUTION_EVENT_TYPES = {
    "assistant",
    "thinking",
    "tool_use",
    "tool_result",
    "usage",
    "turn_complete",
    "result",
}


def trial_started(events: list[dict[str, Any]]) -> bool:
    return any(event.get("type") in EXECUTION_EVENT_TYPES for event in events)


def _combined_error_text(events: list[dict[str, Any]], stderr: str) -> str:
    parts = [stderr]
    for event in events:
        if event.get("type") == "error":
            parts.append(str(event.get("message", "")))
    return "\n".join(parts)


def classify_startup_error(events: list[dict[str, Any]], stderr: str) -> str:
    text = _combined_error_text(events, stderr).lower()
    if is_provider_network_error(events, stderr):
        return "provider_network_error"
    if (
        "authenticationerror" in text
        or "api key not found" in text
        or "openai_api_key not found" in text
        or "api key is missing" in text
        or "missing key" in text
        or "missing_key" in text
    ):
        return "auth_error"
    if (
        "configerror" in text
        or "config file" in text
        or "config must contain" in text
        or "hook config error" in text
    ):
        return "config_error"
    return "agent_startup_error"


def is_provider_network_error(events: list[dict[str, Any]], stderr: str) -> bool:
    text = _combined_error_text(events, stderr).lower()
    patterns = (
        "codepacex.client.networkerror",
        "networkerror:",
        "network error:",
        "apiconnectionerror",
        "api connection error",
        "httpx.remoteprotocolerror",
        "remoteprotocolerror:",
        "incomplete chunked read",
        "peer closed connection without sending complete message body",
    )
    return any(pattern in text for pattern in patterns)


def classify_task_status(
    *,
    events: list[dict[str, Any]],
    stderr: str,
    returncode: int | None,
    timed_out: bool,
    graders: list[GraderResult],
    expected_grader_count: int | None = None,
) -> tuple[str, str, str, str, str]:
    started = trial_started(events)
    warning_type = ""
    warning_message = ""
    infra_error = bool(returncode != 0 and is_provider_network_error(events, stderr))
    expected_count = len(graders) if expected_grader_count is None else expected_grader_count
    all_required_graders_passed = (
        expected_count > 0
        and len(graders) == expected_count
        and all(result.passed for result in graders)
    )

    if infra_error:
        if all_required_graders_passed:
            warning_type = "infra_error_after_success"
            warning_message = "Provider/network/transport error occurred after outcome graders passed."
            return "PASS", "", "", warning_type, warning_message
        return "ERROR", "", "provider_network_error", "", ""

    if not started:
        if timed_out:
            return "ERROR", "", "startup_timeout", "", ""
        if returncode != 0:
            return "ERROR", "", classify_startup_error(events, stderr), "", ""
        return "ERROR", "", "agent_startup_error", "", ""

    if timed_out:
        return "FAIL", "agent_timeout", "", "", ""
    if returncode != 0:
        return "FAIL", "agent_runtime_error", "", "", ""
    if not all_required_graders_passed:
        return "FAIL", "grader_failed", "", "", ""
    return "PASS", "", "", "", ""


def summarize_suite_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    passed = sum(1 for result in results if result["status"] == "PASS")
    failed = sum(1 for result in results if result["status"] == "FAIL")
    errors = sum(1 for result in results if result["status"] == "ERROR")
    warnings = sum(1 for result in results if result.get("warning_type"))
    scored = passed + failed
    if errors:
        suite_status = "INCOMPLETE"
    elif failed:
        suite_status = "FAIL"
    else:
        suite_status = "PASS"
    return {
        "total_tasks": len(results),
        "passed_tasks": passed,
        "failed_tasks": failed,
        "error_tasks": errors,
        "warnings": warnings,
        "scored_trials": scored,
        "success_rate": (passed / scored) if scored else None,
        "suite_status": suite_status,
    }


def run_task(
    repo_root: Path,
    task: dict[str, Any],
    run_dir: Path,
    *,
    keep_failed: bool,
    experiment_profile: Path | None = None,
) -> dict[str, Any]:
    task_id = str(task["id"])
    task_dir = run_dir / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix=f"{task_id}-"))
    workspace_kept = False
    workspace_artifact = ""
    try:
        copy_fixture(repo_root, task, workspace)
        metadata = config_metadata(workspace)
        initial = snapshot_files(workspace)
        trace_path = task_dir / "trace.ndjson"
        stderr_path = task_dir / "stderr.txt"
        returncode, duration_ms, timed_out = run_agent(
            repo_root, workspace, task, trace_path, stderr_path,
            experiment_profile=experiment_profile,
        )
        post_agent = snapshot_files(workspace)
        diff = diff_snapshots(initial, post_agent)
        events = load_trace(trace_path)
        metrics = extract_metrics(events, duration_ms)
        stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
        graders: list[GraderResult] = []
        if trial_started(events):
            graders = run_graders(
                workspace,
                task.get("graders", []),
                diff=diff,
                events=events,
                python_executable=sys.executable,
            )
        status, failure_reason, error_type, warning_type, warning_message = classify_task_status(
            events=events,
            stderr=stderr,
            returncode=returncode,
            timed_out=timed_out,
            graders=graders,
            expected_grader_count=len(task.get("graders", [])),
        )
        passed = status == "PASS"
        if keep_failed and not passed:
            workspace_artifact = str(task_dir / "workspace")
            if Path(workspace_artifact).exists():
                shutil.rmtree(workspace_artifact)
            shutil.move(str(workspace), workspace_artifact)
            workspace_kept = True
        result = {
            "id": task_id,
            "name": task.get("name", task_id),
            "status": status,
            "passed": passed,
            "failure_reason": failure_reason,
            "error_type": error_type,
            "warning_type": warning_type,
            "warning_message": warning_message,
            "returncode": returncode,
            "timed_out": timed_out,
            "metrics": metrics,
            "agent_diff": {
                "added": diff.added,
                "modified": diff.modified,
                "deleted": diff.deleted,
            },
            "graders": [grader_to_dict(g) for g in graders],
            "metadata": metadata,
            "artifacts": {
                "trace": str(trace_path),
                "stderr": str(stderr_path),
                "workspace": workspace_artifact,
                "workspace_kept": workspace_kept,
            },
        }
        (task_dir / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return result
    finally:
        if workspace.exists() and not workspace_kept:
            shutil.rmtree(workspace)


def write_report(run_dir: Path, suite_result: dict[str, Any]) -> None:
    rate = suite_result["success_rate"]
    rate_text = "N/A" if rate is None else f"{rate:.1%}"
    lines = [
        "# CodePaceX Eval Report",
        "",
        f"- Run id: `{suite_result['run_id']}`",
        f"- Git commit: `{suite_result['metadata']['git_commit']}`",
        f"- Dirty: `{suite_result['metadata']['git_dirty']}`",
        f"- Python: `{suite_result['metadata']['python_executable']}`",
        f"- Source root: `{suite_result['metadata']['source_root']}`",
        "",
        "## Summary",
        "",
        f"- Passed: {suite_result['passed_tasks']}",
        f"- Failed: {suite_result['failed_tasks']}",
        f"- Errors: {suite_result['error_tasks']}",
        f"- Warnings: {suite_result['warnings']}",
        f"- Scored Trials: {suite_result['scored_trials']}",
        f"- Task Success Rate: {rate_text}",
        f"- Suite Status: {suite_result['suite_status']}",
        "",
        "## Tasks",
        "",
    ]
    for task in suite_result["tasks"]:
        lines.append(f"### {task['id']} - {task['status']}")
        lines.append("")
        if task.get("failure_reason"):
            lines.append(f"- Failure reason: {task['failure_reason']}")
        if task.get("error_type"):
            lines.append(f"- Error type: {task['error_type']}")
        if task.get("warning_type"):
            lines.append(f"- Warning type: {task['warning_type']}")
        if task.get("warning_message"):
            lines.append(f"- Warning: {task['warning_message']}")
        lines.append(f"- Duration: {task['metrics'].get('duration_ms')} ms")
        lines.append(f"- Turns: {task['metrics'].get('num_turns')}")
        lines.append(f"- Tool calls: {task['metrics'].get('tool_calls')}")
        lines.append(f"- Tool result errors: {task['metrics'].get('tool_result_errors')}")
        lines.append(f"- Changed files: {', '.join(task['agent_diff']['modified'] + task['agent_diff']['added'] + task['agent_diff']['deleted']) or '(none)'}")
        for grader in task["graders"]:
            gstatus = "PASS" if grader["passed"] else "FAIL"
            lines.append(f"- Grader `{grader['name']}`: {gstatus} - {grader['summary']}")
        if task["artifacts"].get("workspace"):
            lines.append(f"- Kept workspace: `{task['artifacts']['workspace']}`")
        lines.append("")
    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run CodePaceX lightweight evals")
    parser.add_argument("--tasks-dir", default=str(DEFAULT_TASKS_DIR))
    parser.add_argument("--task", action="append", default=[])
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    parser.add_argument("--keep-failed", action="store_true")
    parser.add_argument("--experiment-profile", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = find_repo_root()
    sys.path.insert(0, str(repo_root))
    preflight = source_preflight(repo_root)
    tasks = discover_tasks(Path(args.tasks_dir), args.task)
    if not tasks:
        print("No tasks selected", file=sys.stderr)
        return 2
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    commit = run_git(repo_root, ["rev-parse", "HEAD"])
    short = commit[:8] if commit else "nogit"
    run_id = f"{run_id}-{short}"
    run_dir = Path(args.report_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    results = [
        run_task(
            repo_root, task, run_dir, keep_failed=args.keep_failed,
            experiment_profile=args.experiment_profile,
        )
        for task in tasks
    ]
    summary = summarize_suite_results(results)
    suite_result = {
        "run_id": run_id,
        **summary,
        "metadata": {
            "timestamp": datetime.now().isoformat(),
            "git_commit": commit,
            "git_dirty": bool(run_git(repo_root, ["status", "--short"])),
            "source_root": str(repo_root),
            **preflight,
        },
        "tasks": results,
    }
    (run_dir / "suite_result.json").write_text(
        json.dumps(suite_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_report(run_dir, suite_result)
    print(f"Report: {run_dir / 'report.md'}")
    return 0 if summary["suite_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

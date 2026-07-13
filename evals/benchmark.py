"""Versioned, auditable benchmark artifacts and metric helpers."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable
from urllib.parse import urlsplit

SCHEMA_VERSION = 1
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RESULT_STATUSES = {
    "success", "task_failure", "timeout", "provider_error",
    "configuration_error", "infrastructure_error", "cancelled", "dry_run",
}
SCORABLE_STATUSES = {"success", "task_failure"}
RESUMABLE_STATUSES = {"timeout", "provider_error", "infrastructure_error", "cancelled"}
OPTIONAL_JSON = {"usage.json"}
OPTIONAL_STREAMS = {
    "permission-events.jsonl", "compression-events.jsonl", "runtime-events.jsonl",
}
ALLOWED_ARTIFACTS = {"patch.diff", "test-output.txt", "stdout.txt", "stderr.txt"}
SECRET_KEYS = {
    "api_key", "apikey", "x_api_key", "authorization", "bearer", "password",
    "secret", "access_token", "bailian_api_key", "agentrouter_api_key",
}
SECRET_VALUE_PATTERNS = [
    re.compile(r"(?i)\b(api[_-]?key|x-api-key|authorization|password|secret|access_token)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
]


def _normalized_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")


class SecretRedactor:
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        self._secrets = sorted({item for item in secrets if item}, key=len, reverse=True)

    def redact(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): "[REDACTED]" if _normalized_key(key) in SECRET_KEYS else self.redact(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, tuple):
            return [self.redact(item) for item in value]
        if isinstance(value, str):
            text = value
            for secret in self._secrets:
                text = text.replace(secret, "[REDACTED]")
            for pattern in SECRET_VALUE_PATTERNS:
                text = pattern.sub("[REDACTED]", text)
            return text
        return value


def sanitize_origin(url: str) -> str | None:
    if not url:
        return None
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base URL must be an HTTP(S) URL with a host")
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname}{port}"


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def file_hash(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class RunManifest:
    experiment_kind: str = "unknown"
    provider: str = "unknown"
    model_id: str = "unknown"
    protocol: str = "unknown"
    base_url_origin: str | None = None
    api_key_env: str | None = None
    run_id: str = ""
    repetition_id: str | None = None
    git_commit: str = "unknown"
    dirty_worktree: bool | None = None
    prompt_version: str = "unknown"
    system_prompt_hash: str | None = None
    tool_schema_hash: str | None = None
    feature_flags: dict[str, Any] = field(default_factory=dict)
    task_ids: list[str] = field(default_factory=list)
    model_parameters: dict[str, Any] = field(default_factory=dict)
    context_window: int | None = None
    max_output_tokens: int | None = None
    timeout_seconds: int | None = None
    retry_budget: int | None = None
    fallback_enabled: bool = False
    operating_system: str = field(default_factory=platform.system)
    python_version: str = field(default_factory=platform.python_version)
    dependency_snapshot_hash: str | None = None
    experiment_config_hash: str | None = None
    created_at: str = field(default_factory=utc_now)
    schema_version: int = SCHEMA_VERSION
    # Compatibility aliases used by the pre-Pilot helper scripts.
    kind: str | None = None
    model: str | None = None

    def __post_init__(self) -> None:
        if self.kind and self.experiment_kind == "unknown":
            self.experiment_kind = self.kind
        if self.model and self.model_id == "unknown":
            self.model_id = self.model
        if self.base_url_origin:
            self.base_url_origin = sanitize_origin(self.base_url_origin)

    def to_dict(self) -> dict[str, Any]:
        return {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
            if name not in {"kind", "model"}
        }


def _command_version(command: list[str]) -> str | None:
    try:
        proc = subprocess.run(command, text=True, capture_output=True, timeout=2, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    output = (proc.stdout or proc.stderr).strip().splitlines()
    return output[0] if proc.returncode == 0 and output else None


def environment_snapshot(manifest: RunManifest, repo_root: Path | None = None) -> dict[str, Any]:
    dependency = manifest.dependency_snapshot_hash
    if dependency is None and repo_root is not None:
        dependency = file_hash(repo_root / "uv.lock") or file_hash(repo_root / "pyproject.toml")
    return {
        "schema_version": SCHEMA_VERSION,
        "python_version": platform.python_version(),
        "operating_system": platform.system(),
        "operating_system_release": platform.release(),
        "architecture": platform.machine(),
        "docker_version": _command_version(["docker", "--version"]),
        "git_version": _command_version(["git", "--version"]),
        "dependency_snapshot_hash": dependency,
        "api_key_env": manifest.api_key_env,
        "api_key_present": bool(manifest.api_key_env and os.environ.get(manifest.api_key_env)),
    }


class RunRecorder:
    def __init__(
        self, root: Path, manifest: RunManifest, *, run_id: str | None = None,
        secrets: Iterable[str] = (), repo_root: Path | None = None,
    ) -> None:
        selected = run_id or manifest.run_id or f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
        if not RUN_ID_RE.fullmatch(selected):
            raise ValueError("invalid run id")
        self.run_id = selected
        self.path = self._run_path(root, selected)
        self.path.mkdir(parents=True, exist_ok=False)
        self.redactor = SecretRedactor(secrets)
        manifest.run_id = selected
        self.manifest = manifest
        self.write_json("manifest.json", manifest.to_dict())
        self.write_json("environment.json", environment_snapshot(manifest, repo_root))
        self._atomic_write(self.path / "events.jsonl", b"")

    @classmethod
    def resume(cls, root: Path, run_id: str, expected: RunManifest, *, secrets: Iterable[str] = ()) -> RunRecorder:
        if not RUN_ID_RE.fullmatch(run_id):
            raise ValueError("invalid run id")
        path = cls._run_path(root, run_id)
        payload = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        identity = (
            "experiment_config_hash", "git_commit", "provider", "model_id",
            "system_prompt_hash", "tool_schema_hash",
        )
        expected_payload = expected.to_dict()
        mismatches = [key for key in identity if payload.get(key) != expected_payload.get(key)]
        if mismatches:
            raise ValueError(f"resume manifest mismatch: {', '.join(mismatches)}")
        result_path = path / "result.json"
        if result_path.exists():
            status = json.loads(result_path.read_text(encoding="utf-8")).get("status")
            if status not in RESUMABLE_STATUSES:
                raise ValueError(f"run status is not resumable: {status}")
        recorder = cls.__new__(cls)
        recorder.run_id = run_id
        recorder.path = path
        recorder.redactor = SecretRedactor(secrets)
        recorder.manifest = expected
        recorder.event("run_resumed", {"previous_status": status if result_path.exists() else None})
        return recorder

    @staticmethod
    def _run_path(root: Path, run_id: str) -> Path:
        resolved_root = root.resolve()
        resolved = (resolved_root / run_id).resolve(strict=False)
        try:
            resolved.relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError("run path escapes runs root") from exc
        return resolved

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_bytes(content)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def write_json(self, name: str, value: Any) -> None:
        if Path(name).name != name or not name.endswith(".json"):
            raise ValueError("JSON output name must be a plain .json filename")
        payload = self.redactor.redact(value)
        if isinstance(payload, dict):
            payload.setdefault("schema_version", SCHEMA_VERSION)
        content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        self._atomic_write(self.path / name, content.encode())

    def _append_jsonl(self, path: Path, value: dict[str, Any]) -> None:
        record = self.redactor.redact(value)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()

    def event(self, name: str, value: dict[str, Any]) -> None:
        self._append_jsonl(self.path / "events.jsonl", {
            "schema_version": SCHEMA_VERSION, "timestamp": time.time(), "type": name, **value,
        })

    def write_optional_json(self, name: str, value: Any) -> None:
        if name not in OPTIONAL_JSON:
            raise ValueError(f"unsupported optional JSON artifact: {name}")
        self.write_json(name, value)

    def optional_event(self, name: str, value: dict[str, Any]) -> None:
        if name not in OPTIONAL_STREAMS:
            raise ValueError(f"unsupported optional event stream: {name}")
        self._append_jsonl(self.path / name, {
            "schema_version": SCHEMA_VERSION, "timestamp": time.time(), **value,
        })

    def _jsonl_records(self, name: str) -> list[dict[str, Any]]:
        path = self.path / name
        if not path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                decoded = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, dict):
                records.append(decoded)
        return records

    def _json_object(self, name: str) -> dict[str, Any]:
        path = self.path / name
        if not path.exists():
            return {}
        try:
            decoded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return decoded if isinstance(decoded, dict) else {}

    def capture_event(self, event: dict[str, Any]) -> None:
        """Persist one real runner event and derive optional files when applicable.

        ``provider_usage`` is deliberately retained as supplied by the provider:
        this recorder does not manufacture cache, reasoning, or completion fields.
        """
        event_type = event.get("type")
        if not isinstance(event_type, str):
            raise ValueError("captured event requires a string type")
        payload = {key: value for key, value in event.items() if key != "type"}
        if event_type == "permission_decision":
            tool_id = payload.get("tool_id")
            if not isinstance(tool_id, str) or not tool_id:
                raise ValueError("permission decision requires tool_id")
            previous = self._jsonl_records("permission-events.jsonl")
            if any(item.get("tool_id") == tool_id for item in previous):
                raise ValueError(f"duplicate permission decision for tool ID: {tool_id}")
        elif event_type == "runtime_manifest":
            request_index = payload.get("request_index")
            if not isinstance(request_index, int) or request_index < 1:
                raise ValueError("runtime manifest requires a positive request_index")
            previous = self._jsonl_records("runtime-events.jsonl")
            if any(item.get("request_index") == request_index for item in previous):
                raise ValueError(f"duplicate runtime request index: {request_index}")
        self.event(event_type, payload)
        if event_type == "usage":
            existing = self._json_object("usage.json")
            requests = existing.get("requests", [])
            if not isinstance(requests, list):
                requests = []
            requests.append(payload)
            self.write_optional_json("usage.json", {"requests": requests})
        elif event_type == "permission_decision":
            self.optional_event("permission-events.jsonl", payload)
        elif event_type == "compression":
            self.optional_event("compression-events.jsonl", payload)
        elif event_type == "runtime_manifest":
            self.optional_event("runtime-events.jsonl", payload)

    def write_artifact(self, name: str, content: str | bytes) -> Path:
        if name not in ALLOWED_ARTIFACTS or Path(name).name != name:
            raise ValueError("unsupported artifact name")
        artifact_dir = self.path / "artifacts"
        artifact_dir.mkdir(exist_ok=True)
        target = artifact_dir / name
        safe = content if isinstance(content, str) else content.decode(errors="replace")
        self._atomic_write(target, self.redactor.redact(safe).encode())
        return target

    def completed_trials(self) -> set[tuple[str, str]]:
        completed: set[tuple[str, str]] = set()
        for line in (self.path / "events.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "trial_completed":
                completed.add((str(event.get("task_id")), str(event.get("repetition_id"))))
        return completed

    def finalize(self, result: dict[str, Any]) -> None:
        status = result.get("status")
        if status is None and isinstance(result.get("success"), bool):
            status = "success" if result["success"] else "task_failure"
            result = {**result, "status": status}
        if status not in RESULT_STATUSES:
            raise ValueError(f"unsupported result status: {status}")
        attempts: set[tuple[str, str]] = set()
        completed: list[dict[str, Any]] = []
        for event in self._jsonl_records("events.jsonl"):
            trial = (str(event.get("task_id")), str(event.get("repetition_id")))
            if event.get("type") == "trial_started":
                attempts.add(trial)
            elif event.get("type") == "trial_completed":
                attempts.add(trial)
                completed.append(event)
        errors: dict[str, int] = {}
        for event in completed:
            trial_status = str(event.get("status", "infrastructure_error"))
            if trial_status != "success":
                errors[trial_status] = errors.get(trial_status, 0) + 1
        if not attempts and status not in {"success", "dry_run"}:
            errors[status] = errors.get(status, 0) + 1
        unscorable = sum(
            1 for event in completed if event.get("status") not in SCORABLE_STATUSES
        )
        payload = {
            "schema_version": SCHEMA_VERSION,
            **result,
            "status": status,
            "scorable": status in SCORABLE_STATUSES,
            "attempted_trial_count": len(attempts),
            "completed_trial_count": len(completed),
            "unscorable_trial_count": unscorable,
            "error_category_summary": errors,
        }
        self.write_json("result.json", payload)
        redacted = self.redactor.redact(payload)
        lines = ["# Benchmark Run", "", f"- Run ID: `{self.run_id}`", f"- Status: `{status}`"]
        for key, value in sorted(redacted.items()):
            if key not in {"schema_version", "status"}:
                lines.append(f"- {key}: {value}")
        self._atomic_write(self.path / "report.md", ("\n".join(lines) + "\n").encode())


def percentile(values: Iterable[float], fraction: float) -> float | None:
    data = sorted(values)
    if not data:
        return None
    return data[round((len(data) - 1) * fraction)]


def summarize(values: Iterable[float]) -> dict[str, float | int | None]:
    data = list(values)
    return {
        "n": len(data), "mean": sum(data) / len(data) if data else None,
        "median": median(data) if data else None, "p95": percentile(data, 0.95),
    }


def reduction_percent(baseline: float, improved: float) -> float | None:
    return None if baseline <= 0 else (1 - improved / baseline) * 100


def current_git_commit(repo_root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"], text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"

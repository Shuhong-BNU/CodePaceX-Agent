"""Best-effort, crash-safe consolidation of durable memory indexes."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from codepacex.memory.auto_memory import ENTRYPOINT_NAME, MAX_ENTRYPOINT_LINES, MemoryManager
from codepacex.memory.session import SessionManager

DEFAULT_MIN_HOURS = 24
DEFAULT_MIN_SESSIONS = 5
LOCK_FILE = ".consolidate-lock"
STATE_FILE = ".consolidate-state"
LOCK_STALE_SECONDS = 3600


class MemoryConsolidator:
    """Rebuild a deduplicated project-memory index in the background."""

    def __init__(self, work_dir: str, *, min_hours: int = DEFAULT_MIN_HOURS, min_sessions: int = DEFAULT_MIN_SESSIONS) -> None:
        self.work_dir = work_dir
        self.min_hours = min_hours
        self.min_sessions = min_sessions
        self._running = False
        self._guard = asyncio.Lock()

    async def maybe_run(self) -> bool:
        if self._running:
            return False
        async with self._guard:
            try:
                eligible = self._eligible()
            except Exception:
                return False
            if self._running or not eligible:
                return False
            self._running = True
            try:
                return bool(await asyncio.to_thread(self._consolidate))
            except Exception:
                return False
            finally:
                self._running = False

    def _memory_dir(self) -> Path:
        return Path(self.work_dir) / ".codepacex" / "memory"

    def _eligible(self) -> bool:
        memory_dir = self._memory_dir()
        if not memory_dir.is_dir():
            return False
        state = memory_dir / STATE_FILE
        last = state.stat().st_mtime if state.exists() else 0.0
        if time.time() - last < self.min_hours * 3600:
            return False
        sessions = [
            session for session in SessionManager(self.work_dir).list()
            if session.last_active.timestamp() > last
        ]
        return len(sessions) >= self.min_sessions

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _acquire_lock(self, lock: Path) -> bool:
        for _ in range(2):
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                active = False
                try:
                    raw_lock = lock.read_text(encoding="utf-8")
                except OSError:
                    return False
                try:
                    payload = json.loads(raw_lock)
                    pid, created = int(payload["pid"]), float(payload["created_at"])
                    active = self._pid_alive(pid)
                    stale = not active or time.time() - created > LOCK_STALE_SECONDS
                except Exception:
                    stale = True
                if not stale or active:
                    return False
                try:
                    lock.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    return False
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"pid": os.getpid(), "created_at": time.time()}, handle)
            return True
        return False

    def _consolidate(self) -> bool:
        memory_dir = self._memory_dir()
        memory_dir.mkdir(parents=True, exist_ok=True)
        lock = memory_dir / LOCK_FILE
        if not self._acquire_lock(lock):
            return False
        target = memory_dir / ENTRYPOINT_NAME
        state = memory_dir / STATE_FILE
        staging = memory_dir / f".{ENTRYPOINT_NAME}.tmp"
        state_staging = memory_dir / f"{STATE_FILE}.tmp"
        original = target.read_bytes() if target.exists() else None
        try:
            entries: dict[str, str] = {}
            for memory in MemoryManager(self.work_dir).load_project():
                path = Path(memory.path)
                if not path.exists():
                    continue
                title = memory.name.strip() or path.stem
                description = memory.description.strip() or path.stem
                entries.setdefault(title.casefold(), f"- [{title}]({path.name}) — {description}")
            lines = sorted(entries.values(), key=str.casefold)[:MAX_ENTRYPOINT_LINES]
            staging.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            state_staging.write_text(str(int(time.time())), encoding="utf-8")
            staging.replace(target)
            state_staging.replace(state)
            return True
        except Exception:
            try:
                if original is None:
                    target.unlink(missing_ok=True)
                else:
                    restore = memory_dir / f".{ENTRYPOINT_NAME}.restore"
                    restore.write_bytes(original)
                    restore.replace(target)
            except OSError:
                pass
            return False
        finally:
            for temporary in (staging, state_staging, lock):
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

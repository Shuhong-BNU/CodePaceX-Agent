"""Best-effort, crash-safe consolidation of durable memory indexes."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from codepacex.memory.auto_memory import ENTRYPOINT_NAME, MAX_ENTRYPOINT_LINES, MemoryManager
from codepacex.memory.session import SessionManager

DEFAULT_MIN_HOURS = 24
DEFAULT_MIN_SESSIONS = 5
LOCK_FILE = ".consolidate-lock"
STATE_FILE = ".consolidate-state"


class MemoryConsolidator:
    """Rebuild a deduplicated project-memory index in the background."""

    def __init__(self, work_dir: str, *, min_hours: int = DEFAULT_MIN_HOURS, min_sessions: int = DEFAULT_MIN_SESSIONS) -> None:
        self.work_dir = work_dir
        self.min_hours = min_hours
        self.min_sessions = min_sessions
        self._running = False

    async def maybe_run(self) -> bool:
        if self._running or not self._eligible():
            return False
        self._running = True
        try:
            await asyncio.to_thread(self._consolidate)
            return True
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

    def _consolidate(self) -> None:
        memory_dir = self._memory_dir()
        memory_dir.mkdir(parents=True, exist_ok=True)
        lock = memory_dir / LOCK_FILE
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(str(os.getpid()))
            entries: dict[str, str] = {}
            for memory in MemoryManager(self.work_dir).load_all():
                path = Path(memory.path)
                if not path.exists():
                    continue
                title = memory.name.strip() or path.stem
                description = memory.description.strip() or path.stem
                entries.setdefault(title.casefold(), f"- [{title}]({path.name}) — {description}")
            target = memory_dir / ENTRYPOINT_NAME
            staging = target.with_suffix(".tmp")
            lines = sorted(entries.values(), key=str.casefold)[:MAX_ENTRYPOINT_LINES]
            staging.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            staging.replace(target)
            (memory_dir / STATE_FILE).write_text(str(int(time.time())), encoding="utf-8")
        finally:
            try:
                lock.unlink()
            except FileNotFoundError:
                pass

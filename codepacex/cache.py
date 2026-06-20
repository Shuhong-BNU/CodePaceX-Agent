"""提供 CodePaceX 的文件内容缓存与一致性维护能力。

主要包含核心数据结构与执行流程。该模块由 CodePaceX 运行时调用，并维护状态一致性和异常传播。
"""

from __future__ import annotations

import threading


# 核心实现
class FileCache:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._lock = threading.Lock()

    def get(self, path: str) -> str | None:
        with self._lock:
            return self._store.get(path)


    def put(self, path: str, content: str) -> None:
        with self._lock:
            self._store[path] = content


    def invalidate(self, path: str) -> None:
        with self._lock:
            self._store.pop(path, None)


    def clear(self) -> None:
        with self._lock:
            self._store.clear()


    def __len__(self) -> int:
        with self._lock:
            return len(self._store)

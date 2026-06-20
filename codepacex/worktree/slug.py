"""提供 CodePaceX 的 worktree 名称校验与分支名规范化能力。

主要包含 Git worktree 创建、进入、清理和变更检测。该模块由子 Agent 隔离执行与团队协作调用，并维护分支状态、未提交改动和清理安全。
"""

from __future__ import annotations

import re

MAX_SLUG_LENGTH = 64
_SEGMENT_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


# 核心实现
def validate_slug(name: str) -> str | None:
    if not name:
        return "name cannot be empty"
    if len(name) > MAX_SLUG_LENGTH:
        return f"name too long (max {MAX_SLUG_LENGTH} characters)"


    segments = name.split("/")
    for seg in segments:
        if not seg:
            return "name contains empty segment"
        if seg in (".", ".."):
            return "name must not contain '.' or '..' as a segment"
        if not _SEGMENT_RE.match(seg):
            return f"invalid segment: {seg!r} (allowed: letters, digits, '.', '-', '_')"


    return None


def flatten_slug(name: str) -> str:
    return name.replace("/", "+")

"""组织 codepacex.worktree 包的公开接口与子模块。"""

from codepacex.worktree.changes import (
    Changes,
    CleanupResult,
    count_worktree_changes,
    has_worktree_changes,
)
from codepacex.worktree.cleanup import cleanup_stale_worktrees, start_stale_cleanup_task
from codepacex.worktree.manager import WorktreeError, WorktreeManager
from codepacex.worktree.models import Worktree, WorktreeSession
from codepacex.worktree.session import load_worktree_session, save_worktree_session
from codepacex.worktree.slug import flatten_slug, validate_slug


__all__ = [
    "Changes",
    "CleanupResult",
    "Worktree",
    "WorktreeError",
    "WorktreeManager",
    "WorktreeSession",
    "cleanup_stale_worktrees",
    "count_worktree_changes",
    "flatten_slug",
    "has_worktree_changes",
    "load_worktree_session",
    "save_worktree_session",
    "start_stale_cleanup_task",
    "validate_slug",
]


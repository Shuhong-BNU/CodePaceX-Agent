"""提供 CodePaceX 的文件工具的工作区路径边界检查能力。

主要包含权限模式、危险命令检测、路径沙箱和分级规则。该模块由所有工具执行前的权限检查调用，并维护默认拒绝、人工确认和工作区边界。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Literal


# 核心实现
class PathSandbox:


    def __init__(
        self,
        project_root: str,
        extra_allowed: list[str] | None = None,
    ) -> None:
        root = Path(project_root).resolve()
        self._allowed_roots: list[Path] = [root, Path(tempfile.gettempdir()).resolve()]
        if extra_allowed:
            for p in extra_allowed:
                self._allowed_roots.append(Path(p).resolve())


    @property
    def project_root(self) -> Path:
        return self._allowed_roots[0]


    def check(
        self,
        path: str,
        *,
        access: Literal["read", "write"] = "read",
        workspace_only: bool = False,
    ) -> tuple[bool, str]:
        """Resolve a path and verify its real location against allowed roots.

        Non-existent write targets are resolved through their nearest existing
        ancestor so a symlinked parent cannot escape the workspace.
        """
        try:
            candidate = Path(path).expanduser()
            if not candidate.is_absolute():
                candidate = self.project_root / candidate
            absolute = candidate.absolute()
            if absolute.exists():
                real_path = absolute.resolve(strict=True)
            else:
                ancestor = absolute
                while not ancestor.exists():
                    parent = ancestor.parent
                    if parent == ancestor:
                        return False, f"无法解析路径: {path}"
                    ancestor = parent
                resolved_ancestor = ancestor.resolve(strict=True)
                real_path = resolved_ancestor / absolute.relative_to(ancestor)
            roots = self._allowed_roots[:1] if workspace_only else self._allowed_roots
        except (OSError, RuntimeError, ValueError) as exc:
            return False, f"路径解析失败 {path}: {exc}"

        for root in roots:
            try:
                real_path.relative_to(root)
                return True, ""
            except ValueError:
                continue

        return False, f"路径 {path} 超出沙箱范围"

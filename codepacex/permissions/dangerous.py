"""提供 CodePaceX 的危险 Shell 命令识别能力。

主要包含权限模式、危险命令检测、路径沙箱和分级规则。该模块由所有工具执行前的权限检查调用，并维护默认拒绝、人工确认和工作区边界。
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Literal

SafetyEffect = Literal["deny", "ask"]

_DANGEROUS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"rm\s+-[a-z]*r[a-z]*f[a-z]*\s+/\s*$"), "递归强制删除根目录"),
    (re.compile(r"mkfs\."), "格式化磁盘"),
    (re.compile(r"dd\s+if=.*of=/dev/"), "直接写磁盘设备"),
    (re.compile(r"chmod\s+-R\s+777\s+/"), "递归修改根目录权限"),
    (re.compile(r":\(\)\{\s*:\|:&\s*\};:"), "fork bomb"),
    (re.compile(r"curl\s+.*\|\s*(ba)?sh"), "管道执行远程脚本"),
    (re.compile(r"wget\s+.*\|\s*(ba)?sh"), "管道执行远程脚本"),
    (re.compile(r">\s*/dev/sd"), "覆盖磁盘设备"),
]


_SHELL_META = ("|", ";", "&&", ">", "<", "$(", "`", "\n", "${")
_FILE_COMMANDS = {"cat", "head", "tail", "wc", "ls"}
_GIT_COMMANDS = {"status", "diff", "log"}
_GIT_DENIED_ARGS = {"-C", "--git-dir", "--work-tree", "--no-index"}
_GIT_SAFE_OPTIONS = {
    "status": {"-s", "-b", "--short", "--branch", "--porcelain"},
    "diff": {"--stat", "--cached", "--staged", "--name-only", "--name-status"},
    "log": {"--oneline", "--decorate", "--graph", "--stat", "--name-only", "--all", "--no-merges"},
}


def is_safe_command(command: str, project_root: Path | None = None) -> bool:
    """Recognize only a deliberately small set of read-only commands."""
    if not command.strip() or any(meta in command for meta in _SHELL_META):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if not tokens or any("*" in token or "?" in token or "$" in token for token in tokens):
        return False
    executable = Path(tokens[0]).name
    if executable == "pwd":
        return len(tokens) == 1
    if executable == "git":
        if len(tokens) < 2 or tokens[1] not in _GIT_COMMANDS:
            return False
        subcommand = tokens[1]
        args = tokens[2:]
        if any(arg in _GIT_DENIED_ARGS or arg.startswith("--git-dir=") or arg.startswith("--work-tree=") for arg in args):
            return False
        separator = args.index("--") if "--" in args else len(args)
        options, paths = args[:separator], args[separator + 1:] if separator < len(args) else []
        safe_options = _GIT_SAFE_OPTIONS[subcommand]
        for option in options:
            if option in safe_options:
                continue
            if subcommand == "status" and option.startswith(("--porcelain=", "--untracked-files=")):
                continue
            if subcommand == "diff" and (re.fullmatch(r"-U\d+", option) or option.startswith("--color=")):
                continue
            if subcommand == "log" and (
                re.fullmatch(r"-\d+", option)
                or option.startswith(("--max-count=", "--pretty=", "--format=", "--since=", "--until="))
            ):
                continue
            return False
    elif executable in _FILE_COMMANDS:
        paths = [token for token in tokens[1:] if not token.startswith("-")]
        if executable != "ls" and not paths:
            return False
    else:
        return False
    if not paths:
        return True
    if project_root is None:
        return False
    root = project_root.resolve()
    try:
        for raw in paths:
            candidate = Path(raw).expanduser()
            if not candidate.is_absolute():
                candidate = root / candidate
            candidate.resolve(strict=False).relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return False
    return True


class DangerousCommandDetector:


    def __init__(self, extra_patterns: list[tuple[str, str]] | None = None) -> None:
        self._patterns = list(_DANGEROUS_PATTERNS)
        if extra_patterns:
            for regex_str, reason in extra_patterns:
                self._patterns.append((re.compile(regex_str), reason))


    def detect(self, command: str) -> tuple[bool, str]:
        for pattern, reason in self._patterns:
            if pattern.search(command):
                return True, reason
        return False, ""

    def assess(self, command: str, work_dir: Path) -> tuple[SafetyEffect | None, str]:
        """Return a mandatory decision for destructive commands."""
        hit, reason = self.detect(command)
        if hit:
            return "deny", reason
        decisions = [self._assess_segment(part, work_dir) for part in re.split(r"&&|\|\||[;|]", command)]
        decisions = [item for item in decisions if item[0] is not None]
        if not decisions:
            return None, ""
        for effect, item_reason in decisions:
            if effect == "deny":
                return effect, item_reason
        return decisions[0]

    def _assess_segment(self, command: str, work_dir: Path) -> tuple[SafetyEffect | None, str]:
        tokens = shlex.split(command.strip())
        if not tokens:
            return None, ""
        index = 0
        if Path(tokens[index]).name == "command":
            index += 1
        if index < len(tokens) and Path(tokens[index]).name == "sudo":
            index += 1
            value_options = {"-u", "-g", "-h", "-p", "-C", "-T", "-r"}
            while index < len(tokens) and tokens[index].startswith("-"):
                option = tokens[index]
                index += 2 if option in value_options else 1
        if index >= len(tokens):
            return None, ""
        executable = Path(tokens[index]).name
        args = tokens[index + 1:]
        if executable == "rm":
            return self._assess_rm(args, work_dir)
        if executable == "find" and "-delete" in args:
            target = next((arg for arg in args if not arg.startswith("-")), ".")
            reason = self._critical_delete_reason(target, work_dir, bulk=True)
            return ("deny", reason) if reason else ("ask", "find -delete 会批量删除文件")
        if executable == "git" and args and args[0] == "clean":
            return "ask", "git clean 会删除未跟踪文件"
        if re.search(r"(?:^|\s)(?:rm|find|git\s+clean)(?:\s|$)", command):
            return "ask", "检测到无法完全解析的删除操作"
        return None, ""

    def _assess_rm(self, args: list[str], work_dir: Path) -> tuple[SafetyEffect, str]:
        recursive = any(arg.startswith("-") and "r" in arg.lower() for arg in args if arg != "--")
        targets = [arg for arg in args if not arg.startswith("-")]
        if not targets:
            return "ask", "rm 删除目标无法确认"
        bulk = len(targets) > 1 or any("*" in target or "?" in target for target in targets)
        for target in targets:
            reason = self._critical_delete_reason(target, work_dir, bulk=bulk or recursive)
            if reason:
                return "deny", reason
        return "ask", "rm 会删除文件或目录"

    @staticmethod
    def _critical_delete_reason(raw: str, work_dir: Path, *, bulk: bool) -> str:
        root = work_dir.resolve()
        home = Path.home().resolve()
        target = Path(raw).expanduser()
        if not target.is_absolute():
            target = root / target
        resolved = target.resolve(strict=False)
        if resolved == Path("/") or resolved == home or resolved == root:
            return f"禁止删除关键路径 {raw}"
        try:
            root.relative_to(resolved)
            return f"禁止删除项目或用户目录的祖先 {raw}"
        except ValueError:
            pass
        for name in (".git", ".codepacex"):
            protected = root / name
            try:
                resolved.relative_to(protected)
                return f"禁止删除受保护目录 {name} 的内容"
            except ValueError:
                pass
        for name in ("tests", "evals"):
            protected = root / name
            try:
                resolved.relative_to(protected)
            except ValueError:
                continue
            if resolved == protected or bulk or not (resolved.exists() and resolved.is_file()):
                return f"禁止批量或递归删除 {name}"
        return ""

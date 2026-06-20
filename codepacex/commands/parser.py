"""提供 CodePaceX 的命令行文本与参数解析能力。

主要包含斜杠命令的解析、注册、补全与处理器。该模块由终端应用的命令分发层调用，并维护命令参数和 UI 状态一致性。
"""

from __future__ import annotations

from codepacex.commands.registry import CommandRegistry


# 核心实现
def parse_command(text: str) -> tuple[str, str, bool]:
    text = text.strip()
    if not text.startswith("/"):
        return "", "", False
    text = text[1:]
    if not text:
        return "", "", True
    parts = text.split(None, 1)
    name = parts[0].lower()
    args = parts[1].strip() if len(parts) > 1 else ""
    return name, args, True


def complete(registry: CommandRegistry, prefix: str) -> list[tuple[str, str]]:
    """返回匹配命令的 (display_text, command_value) 列表。"""
    prefix = prefix.lstrip("/")
    seen: set[str] = set()
    matches: list[tuple[str, str]] = []
    for cmd in registry.list_commands():
        if cmd.name in seen:
            continue
        if cmd.name.startswith(prefix) or any(a.startswith(prefix) for a in cmd.aliases):
            seen.add(cmd.name)
            desc = cmd.description
            if len(desc) > 30:
                desc = desc[:28] + "…"
            desc = desc.replace("[", "\\[")
            display = f"/{cmd.name:<16} — {desc}"
            matches.append((display, "/" + cmd.name))
    matches.sort(key=lambda x: x[1])
    return matches[:8]


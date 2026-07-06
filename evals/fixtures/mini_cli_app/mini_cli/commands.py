from __future__ import annotations


COMMANDS = {
    "list": "List configured items",
    "show": "Show one item",
}

ALIASES = {
    "ls": "list",
    "cat": "show",
}


def resolve_command(name: str) -> str:
    if name in COMMANDS:
        return name
    if name in ALIASES:
        return name
    raise KeyError(name)


def command_description(name: str) -> str:
    resolved = resolve_command(name)
    return COMMANDS[resolved]

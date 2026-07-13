ALIASES = {"ls": "list", "rm": "remove"}


def resolve_command(name: str) -> str:
    return ALIASES.get(name, name)

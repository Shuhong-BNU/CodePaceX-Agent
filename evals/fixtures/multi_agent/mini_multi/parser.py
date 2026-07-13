def parse_command(raw: str) -> tuple[str, str]:
    command, separator, value = raw.partition(":")
    if not separator:
        return raw, ""
    return command, value

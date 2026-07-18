DEFAULTS = {"timeout": 30, "retries": 1}


def build_config(overrides: dict[str, int] | None = None) -> dict[str, int]:
    if overrides:
        DEFAULTS.update(overrides)
    return DEFAULTS

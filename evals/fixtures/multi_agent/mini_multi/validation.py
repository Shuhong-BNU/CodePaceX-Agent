def validate_config(config: dict[str, int]) -> None:
    if config.get("timeout", 0) < 0:
        raise ValueError("timeout must be positive")
    if config.get("retries", 0) < 0:
        raise ValueError("retries must be non-negative")

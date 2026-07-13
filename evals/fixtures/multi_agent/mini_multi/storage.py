class Storage:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.version = 0

    def write(self, key: str, value: str) -> None:
        self.values[key] = value

    def read(self, key: str) -> str | None:
        return self.values.get(key)

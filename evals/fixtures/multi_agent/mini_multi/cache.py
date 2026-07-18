from mini_multi.storage import Storage


class Cache:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        self.values: dict[str, str | None] = {}

    def get(self, key: str) -> str | None:
        if key not in self.values:
            self.values[key] = self.storage.read(key)
        return self.values[key]

    def put(self, key: str, value: str) -> None:
        self.storage.write(key, value)

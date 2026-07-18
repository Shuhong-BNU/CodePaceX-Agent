from mini_multi.cache import Cache
from mini_multi.storage import Storage


def test_put_updates_cache_and_storage_version() -> None:
    storage = Storage()
    cache = Cache(storage)
    assert cache.get("key") is None
    cache.put("key", "v1")
    assert cache.get("key") == "v1"
    assert storage.version == 1
    cache.put("key", "v2")
    assert cache.get("key") == "v2"
    assert storage.version == 2

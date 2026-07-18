import pytest

from mini_multi.config import DEFAULTS, build_config
from mini_multi.validation import validate_config


def test_configs_do_not_mutate_shared_defaults() -> None:
    custom = build_config({"timeout": 5})
    fresh = build_config()
    assert custom == {"timeout": 5, "retries": 1}
    assert fresh == {"timeout": 30, "retries": 1}
    assert DEFAULTS == {"timeout": 30, "retries": 1}


@pytest.mark.parametrize("config", [{"timeout": 0, "retries": 1}, {"timeout": 1, "retries": -1}])
def test_invalid_values_are_rejected(config: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        validate_config(config)

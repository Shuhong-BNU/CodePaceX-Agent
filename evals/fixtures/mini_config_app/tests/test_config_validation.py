import pytest

from mini_config.config import ConfigError, load_provider


def test_models_must_be_a_list():
    with pytest.raises(ConfigError, match="models"):
        load_provider({"name": "bad", "models": "not-a-list"})


def test_models_must_not_contain_empty_names():
    with pytest.raises(ConfigError, match="empty"):
        load_provider({"name": "bad", "models": ["ok", ""]})

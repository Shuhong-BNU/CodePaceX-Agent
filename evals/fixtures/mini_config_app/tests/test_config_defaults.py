from mini_config.config import load_provider


def test_models_first_entry_is_used_when_default_model_is_empty():
    provider = load_provider({
        "name": "local",
        "models": ["fast-model", "slow-model"],
    })

    assert provider.effective_model == "fast-model"
    assert provider.default_model == "fast-model"
    assert provider.models == ["fast-model", "slow-model"]

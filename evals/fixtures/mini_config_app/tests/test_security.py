from mini_config.security import redact_secret


def test_redact_secret_masks_middle_characters():
    assert redact_secret("sk-1234567890") == "sk-********90"

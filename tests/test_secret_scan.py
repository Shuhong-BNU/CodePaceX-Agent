from evals.secret_scan import line_has_credential


def test_tracked_secret_scanner_covers_assignments_bearer_and_encoded_credentials() -> None:
    assert line_has_credential("BAILIAN_API_KEY=live_credential_value")
    assert line_has_credential("Authorization: Bearer credential-value-123")
    assert line_has_credential("https://user:encoded%2Fpassword@proxy.internal")
    assert not line_has_credential("BAILIAN_API_KEY=test-only-bailian-key")
    assert not line_has_credential("https://user:password@example.test")

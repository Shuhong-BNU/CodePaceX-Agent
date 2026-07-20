import json
from pathlib import Path

from evals.secret_scan import line_has_credential, scan_artifact_roots


def test_tracked_secret_scanner_covers_assignments_bearer_and_encoded_credentials() -> None:
    assert line_has_credential("BAILIAN_" + "API_KEY=live_credential_value")
    assert line_has_credential("Authorization: " + "Bear" + "er credential-value-123")
    assert line_has_credential("https://user:" + "encoded%2Fpassword@proxy.internal")
    assert not line_has_credential("BAILIAN_API_KEY=test-only-bailian-key")
    assert not line_has_credential("https://user:password@example.test")


def test_artifact_secret_scanner_reports_paths_without_secret_values(tmp_path: Path) -> None:
    (tmp_path / "safe.json").write_text('{"api_key_env":"BAILIAN_API_KEY"}')
    secret = tmp_path / "unsafe.txt"
    secret.write_text("Authorization: " + "Bear" + "er credential-value-123")
    assert scan_artifact_roots([tmp_path]) == [f"{secret}:1"]


def test_artifact_scanner_can_exclude_explicit_untrusted_jsonl_field(tmp_path: Path) -> None:
    corpus = tmp_path / "formal-dataset.jsonl"
    untrusted_prompt = "AWS" + "::LanguageExtensions"
    artifact_secret = "Authorization: " + "Bear" + "er " + "credential" + "-value-123"
    corpus.write_text(json.dumps({
        "problem_statement": untrusted_prompt,
        "evaluator_output": artifact_secret,
    }) + "\n")
    assert scan_artifact_roots([tmp_path]) == [f"{corpus}:1"]
    assert scan_artifact_roots(
        [tmp_path], untrusted_json_fields=frozenset({"problem_statement"}),
    ) == [f"{corpus}:1"]

    corpus.write_text(json.dumps({"problem_statement": untrusted_prompt}) + "\n")
    assert scan_artifact_roots(
        [tmp_path], untrusted_json_fields=frozenset({"problem_statement"}),
    ) == []

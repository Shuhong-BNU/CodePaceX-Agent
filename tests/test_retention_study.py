import json
from pathlib import Path

from evals.goal2_studies import load_studies
from evals.retention_study import (
    dry_run, filler_messages, profiles, retention_rate_fields, scoped_session_indices,
    strict_canary_grade,
)


STUDIES = Path("evals/goal2/studies.yaml")


def test_retention_profiles_map_real_recovery_attachment_behavior() -> None:
    summary, recovery = profiles(load_studies(STUDIES))
    assert summary.effective_runtime()["recovery_attachments_enabled"] is False
    assert recovery.effective_runtime()["recovery_attachments_enabled"] is True


def test_retention_pilot_scope_pairs_both_profiles_on_one_seed() -> None:
    studies = load_studies(STUDIES)
    assert scoped_session_indices(studies, scope="pilot") == [0]
    assert scoped_session_indices(studies, scope="formal") == list(range(10))


def test_strict_canary_grader_requires_exact_json_shape_order_and_values() -> None:
    canaries = ["CNY-a", "CNY-b"]
    assert strict_canary_grade(json.dumps({"canaries": canaries}), canaries)[0]
    assert not strict_canary_grade(json.dumps({"canaries": list(reversed(canaries))}), canaries)[0]
    assert not strict_canary_grade(json.dumps({"canaries": canaries, "note": "extra"}), canaries)[0]
    assert not strict_canary_grade("```json\n{}\n```", canaries)[0]


def test_controlled_filler_is_deterministic_and_does_not_contain_canaries() -> None:
    first = filler_messages("session", 1)
    assert first == filler_messages("session", 1)
    assert first != filler_messages("session", 2)
    assert len(first) == 8
    assert "CNY-" not in repr(first)


def test_retention_rate_fields_are_conservative_exact_match_counts() -> None:
    assert retention_rate_fields("success", {
        "ordered_exact_match": True, "expected_count": 12,
        "successful_compactions": 3, "minimum_compactions": 3,
    }) == {"numerator": 12, "denominator": 12}
    assert retention_rate_fields("task_failure", {
        "ordered_exact_match": False, "expected_count": 12,
        "successful_compactions": 3, "minimum_compactions": 3,
    }) == {"numerator": 0, "denominator": 12}


def test_retention_dry_run_creates_two_unscorable_arms(tmp_path: Path) -> None:
    recorders = dry_run(
        root=Path.cwd(), studies_path=STUDIES,
        runs_dir=tmp_path, run_prefix="retention",
    )
    assert [item.run_id for item in recorders] == [
        "retention-summary_only", "retention-recovery_v1",
    ]
    for recorder in recorders:
        result = json.loads((recorder.path / "result.json").read_text())
        assert result["status"] == "dry_run" and result["scorable"] is False

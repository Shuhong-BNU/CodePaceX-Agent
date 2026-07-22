from __future__ import annotations

from pathlib import Path


WORKFLOWS = (
    Path(".github/workflows/stage-c-smoke.yml"),
    Path(".github/workflows/stage-c-continuation.yml"),
)


def test_stage_c_workflows_are_dispatch_only_and_default_to_zero_provider() -> None:
    for path in WORKFLOWS:
        source = path.read_text(encoding="utf-8")
        assert "workflow_dispatch:" in source
        assert "pull_request:" not in source
        assert "push:" not in source
        assert "default: false" in source
        assert "BAILIAN_API_KEY" not in source
        assert "paid_execution" in source
        assert "reject-paid" in source
        assert "formal_stage_c_trial" in source


def test_stage_c_workflows_require_immutable_commit_and_continuation_identity() -> None:
    smoke = WORKFLOWS[0].read_text(encoding="utf-8")
    continuation = WORKFLOWS[1].read_text(encoding="utf-8")
    assert "Manually approved immutable" in smoke
    assert "ref: ${{ inputs.freeze_commit }}" in smoke
    for value in (
        "phase_1_artifact_id", "phase_1_archive_sha256", "phase_1_report_sha256",
        "phase_1_ledger_sha256", "phase_2_authorization_identity",
    ):
        assert value in continuation

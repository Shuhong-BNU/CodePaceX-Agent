from __future__ import annotations

from pathlib import Path


PAID_WORKFLOWS = (
    Path(".github/workflows/stage-c-smoke-paid.yml"),
    Path(".github/workflows/stage-c-continuation-paid.yml"),
)


def test_paid_workflows_are_dispatch_only_and_default_inert() -> None:
    for path in PAID_WORKFLOWS:
        source = path.read_text(encoding="utf-8")
        assert "workflow_dispatch:" in source
        assert "push:" not in source
        assert "pull_request:" not in source
        assert "schedule:" not in source
        assert "paid_execution:" in source
        assert "default: false" in source
        assert "zero-provider-validation" in source
        assert "inputs.paid_execution" in source


def test_paid_phase_one_binds_commit_hashes_and_never_autostarts_phase_two() -> None:
    source = PAID_WORKFLOWS[0].read_text(encoding="utf-8")
    for value in (
        "approved_commit", "freeze_sha256", "pricing_sha256", "authorization_identity",
        "task_bundle_artifact_id", "task_bundle_sha256", "--confirm-paid-run",
        "stage-c-phase-1", "evals.stage_c_paid execute-phase",
    ):
        assert value in source
    assert "stage-c-continuation-paid.yml" not in source


def test_paid_continuation_binds_phase_one_artifact_before_phase_two_transport() -> None:
    source = PAID_WORKFLOWS[1].read_text(encoding="utf-8")
    for value in (
        "phase_1_artifact_id", "phase_1_archive_sha256", "validate-phase-1",
        "actions/artifacts/${{ inputs.phase_1_artifact_id }}", "--phase-1-artifact",
        "--phase-1-consumption-cny", "--phase-1-archive-sha256", "--confirm-paid-run",
    ):
        assert value in source

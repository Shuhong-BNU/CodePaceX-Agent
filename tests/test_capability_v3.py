from __future__ import annotations

from pathlib import Path

import pytest

from codepacex.capability_v3 import CapabilityV3Config, CapabilityV3Controller, CapabilityV3Flag, flag_from_feature_flags
from codepacex.capability_v3.models import (
    CandidateLevel, ComparableRunIdentity, ContractDimension, FailureRecord,
    HypothesisRecord, ReproducerEvidence, ReproducerStatus,
)


def _controller(**kwargs) -> CapabilityV3Controller:
    return CapabilityV3Controller(CapabilityV3Config(enabled=True, **kwargs), base_commit="base")


def test_flags_defaults_and_invalid_configuration() -> None:
    assert not CapabilityV3Config.from_flag(CapabilityV3Flag.V2_CONTROL).enabled
    assert flag_from_feature_flags({"capability_v3_flag": "V3_CORE"}) is CapabilityV3Flag.V3_CORE
    assert CapabilityV3Config.from_flag(CapabilityV3Flag.V3_A_ONLY).contract_recovery_enabled
    assert not CapabilityV3Config.from_flag(CapabilityV3Flag.V3_A_ONLY).impact_slice_enabled
    with pytest.raises(ValueError, match="max_hypotheses"):
        CapabilityV3Config(max_hypotheses=4)
    with pytest.raises(ValueError, match="restore_floor"):
        CapabilityV3Controller(CapabilityV3Config(enabled=True, restore_floor_requests=8), request_limit=40)


def test_evidence_hypotheses_and_oracle_are_advisory(tmp_path: Path) -> None:
    package = tmp_path / "pkg"; package.mkdir()
    (package / "api.py").write_text("DEFAULT = 'x'\ndef target(value=DEFAULT):\n    return value\n")
    tests = tmp_path / "tests"; tests.mkdir()
    (tests / "test_api.py").write_text("from pkg.api import target\ndef test_target(): assert target() == 'x'\n")
    controller = _controller()
    packet = controller.collect_evidence("target default config", tmp_path, ["target"])
    assert packet and packet.target_symbols and packet.tests_and_fixtures
    records = [HypothesisRecord(f"h{n}", f"claim {n}", (), (), f"prediction {n}", "pytest") for n in range(4)]
    assert len(controller.record_hypotheses(records)) == 3
    assert not controller.reject_hypothesis("h0", tool_evidence_id="")
    assert controller.reject_hypothesis("h0", tool_evidence_id="tool-result")
    risks = controller.inspect_oracle(changed_files=["tests/test_api.py"], diff_text="+def new_api():\n+    except Exception: pass")
    assert risks
    assert controller.before_tool_call("EditFile").level in {"warning", "strong_warning"}


def test_impact_matrix_reproducer_and_candidate_finalization(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text("def target():\n    return 1\n")
    (tmp_path / "test_mod.py").write_text("from mod import target\ndef test_target(): assert target() == 1\n")
    patch = tmp_path / "candidate.patch"; patch.write_text("diff")
    controller = _controller()
    impact = controller.build_impact(diff_sha="d", changed_files=["mod.py"], repository=tmp_path,
                                     prior_failing_tests=["tests/prior.py"], f2p_tests=["tests/f2p.py"])
    assert impact and [item.test for item in impact.impacted_tests[:2]] == ["tests/f2p.py", "tests/prior.py"]
    matrix = controller.build_matrix([
        ContractDimension("default", ("implicit", "explicit"), ("ev",)),
        ContractDimension("case", ("lower", "mixed"), ("ev",)),
        ContractDimension("invalid", ("valid", "error"), ()),
    ])
    assert matrix and len(matrix.dimensions) == 2 and matrix.uncovered_dimensions == ("invalid",)
    controller.register_reproducer(ReproducerEvidence("r", "command", ("pytest",), (), "fail", "pass", ("ev",), ReproducerStatus.PRE_FAIL_POST_PASS))
    c1 = controller.observe_diff(diff_text="diff", changed_files=["mod.py"], patch_path=patch)
    assert c1 and c1.level is CandidateLevel.C1
    c2 = controller.observe_test_result(passed=True, test_evidence_id="target", reproducer_status=ReproducerStatus.PRE_FAIL_POST_PASS)
    assert c2 and c2.level is CandidateLevel.C2
    c3 = controller.observe_test_result(passed=True, test_evidence_id="impact", regression_bounded=True)
    assert c3 and c3.level is CandidateLevel.C3
    assert controller.update_budget(32).phase == "finalize"
    assert controller.update_budget(37).phase == "finalize"
    assert controller.finalize("request_ceiling").level is CandidateLevel.C3


def test_finalization_prefers_later_tested_candidate_over_early_narrow_risk_free_candidate(tmp_path: Path) -> None:
    """Finalization is evidence-weighted, not an accidental restoration of C1."""
    early_patch = tmp_path / "early.patch"; early_patch.write_text("early")
    later_patch = tmp_path / "later.patch"; later_patch.write_text("later")
    controller = _controller()
    controller.update_budget(3)
    early = controller.snapshot_candidate(
        CandidateLevel.C1, diff_sha="early", patch_path=early_patch,
        changed_files=("src/narrow.py",),
        oracle_risks=(),
    )
    assert early is not None
    controller.update_budget(18)
    later = controller.snapshot_candidate(
        CandidateLevel.C1, diff_sha="later", patch_path=later_patch,
        changed_files=("src/api.py", "src/adapter.py", "tests/test_api.py"),
        test_evidence_ids=("RunTest-later",),
        oracle_risks=(
            controller.inspect_oracle(
                changed_files=["tests/test_api.py"], diff_text="",
            )[0],
        ),
    )
    assert later is not None
    selected = controller.finalize("request_ceiling")
    assert selected is not None and selected.candidate_id == later.candidate_id
    event = next(event for event in controller.events if event["event_type"] == "CandidateSelectionEvaluated")
    by_id = {item["candidate_id"]: item for item in event["payload"]["candidates"]}
    assert by_id[later.candidate_id]["validation_evidence_count"] == 1
    assert by_id[later.candidate_id]["oracle_risk_penalty"] == 1
    assert by_id[early.candidate_id]["changed_file_coverage"] == 1


def test_oracle_risk_grading_distinguishes_project_tests_from_evaluator_gold() -> None:
    controller = _controller()
    project_risk = controller.inspect_oracle(changed_files=["tests/test_feature.py"], diff_text="")
    evaluator_risk = controller.inspect_oracle(changed_files=["evals/gold/expected.json"], diff_text="")
    assert [(risk.risk_type, risk.level) for risk in project_risk] == [("expected_or_fixture_changed", "warning")]
    assert [(risk.risk_type, risk.level) for risk in evaluator_risk] == [("evaluator_or_gold_modified", "high")]


def test_failed_test_evidence_supersedes_the_same_patch_for_final_selection(tmp_path: Path) -> None:
    early_patch = tmp_path / "early.patch"; early_patch.write_text("early")
    later_patch = tmp_path / "later.patch"; later_patch.write_text("later")
    controller = _controller()
    controller.update_budget(2)
    early = controller.snapshot_candidate(
        CandidateLevel.C1, diff_sha="early", patch_path=early_patch,
        changed_files=("src/early.py",), test_evidence_ids=("RunTest-early",),
    )
    assert early is not None
    controller.update_budget(16)
    later = controller.snapshot_candidate(
        CandidateLevel.C1, diff_sha="later", patch_path=later_patch,
        changed_files=("src/later.py", "src/more.py"),
    )
    assert later is not None
    failed = controller.observe_test_result(passed=False, test_evidence_id="RunTest-regression")
    assert failed is not None and failed.known_failures == ("RunTest-regression",)
    assert controller.finalize("request_ceiling").candidate_id == early.candidate_id
    event = next(event for event in controller.events if event["event_type"] == "CandidateSelectionEvaluated")
    by_id = {item["candidate_id"]: item for item in event["payload"]["candidates"]}
    assert by_id[later.candidate_id]["selection_eligible"] is False
    assert by_id[later.candidate_id]["superseded_by"] == failed.candidate_id
    assert by_id[failed.candidate_id]["regression_count"] == 1


def test_no_candidate_never_exports_wip_and_differential_preserves_unknown() -> None:
    controller = _controller()
    assert controller.finalize("request_ceiling") is None
    identity = ComparableRunIdentity("c", "env", "deps", "py", ("pytest",), "slice", "offline", 30)
    post = [FailureRecord("failure", "test_a", "assertion", "sig")]
    assert controller.attribute_failures(None, post, baseline_identity=None, post_identity=identity).unknown
    other = ComparableRunIdentity("other", "env", "deps", "py", ("pytest",), "slice", "offline", 30)
    assert controller.attribute_failures(post, post, baseline_identity=other, post_identity=identity).incomparable
    result = controller.attribute_failures([], post, baseline_identity=identity, post_identity=identity)
    assert result.new_regression_candidates == ("failure",)


def test_events_and_artifact_are_append_only_and_replayable(tmp_path: Path) -> None:
    controller = _controller()
    controller.update_budget(1)
    artifact = controller.write_artifact(tmp_path / "artifact")
    assert (artifact / "events.jsonl").exists() and (artifact / "summary.json").exists()
    replay = CapabilityV3Controller.replay(controller.events)
    assert replay.events == controller.events

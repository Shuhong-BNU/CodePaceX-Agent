import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from evals.goal2_studies import load_studies
from evals.paid_gate import ProviderUsageUnknown
from evals.retention_study import (
    RetentionUsageIncomplete, dry_run, execute, filler_messages, profiles,
    retention_rate_fields, scoped_session_indices, strict_canary_grade,
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


def test_retention_usage_incomplete_preserves_runtime_and_usage_counts() -> None:
    error = RetentionUsageIncomplete(request_count=12, usage_count=10)
    assert str(error) == "retention provider request usage is incomplete"
    assert error.request_count == 12
    assert error.usage_count == 10


def test_execute_conservatively_settles_only_the_known_missing_usage_trial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeGate:
        def __init__(self) -> None:
            self.reconciled_trial_ids: list[str] = []

        def conservatively_settle_active_trial_unknown_usage(
            self, *, trial_id: str, evidence_gap: str,
        ) -> SimpleNamespace:
            assert "no durable Provider Usage" in evidence_gap
            self.reconciled_trial_ids.append(trial_id)
            return SimpleNamespace(reservation_id="reservation-unknown")

        def trial_accounting(self, trial_id: str) -> dict[str, object]:
            assert trial_id == self.reconciled_trial_ids[0]
            return {
                "actual_cny": "0.688128", "request_count": 10,
                "usage_unknown": True,
                "claim_exclusion_reason": "unknown_provider_usage_conservative_reservation",
            }

    gate = FakeGate()

    async def missing_usage(**_: object) -> tuple[str, dict[str, object], list[tuple[int, int]]]:
        raise RetentionUsageIncomplete(request_count=12, usage_count=10)

    monkeypatch.setenv("BAILIAN_API_KEY", "test-only-not-a-real-key")
    monkeypatch.setattr("evals.retention_study.PaidRunGate", lambda **_: gate)
    monkeypatch.setattr("evals.retention_study.provider_request_budget_scope", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr("evals.retention_study._run_session", missing_usage)
    recorders = execute(
        root=Path.cwd(), studies_path=STUDIES, runs_dir=tmp_path,
        run_prefix="retention-missing-usage", pricing_snapshot=Path(
            "evals/goal2/pricing_bailian_qwen37_max_2026-07-13.json"
        ),
        budget_authorization=tmp_path / "authorization.json",
        budget_ledger=tmp_path / "ledger.json", budget_allocation=tmp_path / "allocation.json",
        confirmed=True,
    )
    assert gate.reconciled_trial_ids == [
        "retention/retention-missing-usage-summary_only/summary_only/retention-session-01"
    ]
    events = [json.loads(line) for line in (
        recorders[0].path / "events.jsonl"
    ).read_text().splitlines()]
    completed = [event for event in events if event["type"] == "trial_completed"][-1]
    assert completed["budget_reconciled_conservatively"] is True
    assert completed["usage_unknown"] is True
    assert completed["actual_cny"] == "0.688128"
    assert completed["runtime_request_count"] == 12
    assert completed["usage_event_count"] == 10
    assert completed["provider_usage_unknown"] is True


def test_execute_conservatively_settles_provider_usage_unknown_once_and_seals_trial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeGate:
        def __init__(self) -> None:
            self.active_trial_id: str | None = None
            self.reconciled_trial_ids: list[str] = []

        def conservatively_settle_active_trial_unknown_usage(
            self, *, trial_id: str, evidence_gap: str,
        ) -> SimpleNamespace:
            assert self.active_trial_id == trial_id
            assert "no durable Provider Usage" in evidence_gap
            self.active_trial_id = None
            self.reconciled_trial_ids.append(trial_id)
            return SimpleNamespace(reservation_id="reservation-unknown")

        def trial_accounting(self, trial_id: str) -> dict[str, object]:
            assert self.active_trial_id is None
            assert trial_id == self.reconciled_trial_ids[0]
            return {
                "actual_cny": "0.688128", "request_count": 0,
                "usage_unknown": True,
                "claim_exclusion_reason": "unknown_provider_usage_conservative_reservation",
            }

    gate = FakeGate()

    async def unknown_usage(**kwargs: object) -> tuple[str, dict[str, object], list[tuple[int, int]]]:
        gate.active_trial_id = (
            "retention/retention-provider-usage-unknown-summary_only/summary_only/"
            "retention-session-01"
        )
        raise ProviderUsageUnknown("active reservation retained")

    monkeypatch.setenv("BAILIAN_API_KEY", "test-only-not-a-real-key")
    monkeypatch.setattr("evals.retention_study.PaidRunGate", lambda **_: gate)
    monkeypatch.setattr("evals.retention_study.provider_request_budget_scope", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr("evals.retention_study._run_session", unknown_usage)
    arguments = {
        "root": Path.cwd(), "studies_path": STUDIES, "runs_dir": tmp_path,
        "run_prefix": "retention-provider-usage-unknown", "pricing_snapshot": Path(
            "evals/goal2/pricing_bailian_qwen37_max_2026-07-13.json"
        ),
        "budget_authorization": tmp_path / "authorization.json",
        "budget_ledger": tmp_path / "ledger.json",
        "budget_allocation": tmp_path / "allocation.json", "confirmed": True,
        "scope": "pilot",
    }
    recorders = execute(**arguments)
    assert gate.active_trial_id is None
    assert gate.reconciled_trial_ids == [
        "retention/retention-provider-usage-unknown-summary_only/summary_only/retention-session-01"
    ]
    events = [json.loads(line) for line in (recorders[0].path / "events.jsonl").read_text().splitlines()]
    completed = [event for event in events if event["type"] == "trial_completed"]
    assert len(completed) == 1
    assert completed[0]["status"] == "infrastructure_error"
    assert completed[0]["error_category"] == "ProviderUsageUnknown"
    assert completed[0]["provider_usage_unknown"] is True
    assert completed[0]["budget_reconciled_conservatively"] is True
    assert completed[0]["usage_unknown"] is True
    assert "input_tokens" not in completed[0] and "output_tokens" not in completed[0]
    assert not (recorders[0].path / "usage.json").exists()

    resumed = execute(**arguments, resume=True)
    assert gate.reconciled_trial_ids == [
        "retention/retention-provider-usage-unknown-summary_only/summary_only/retention-session-01"
    ]
    resumed_events = [json.loads(line) for line in (resumed[0].path / "events.jsonl").read_text().splitlines()]
    assert sum(event["type"] == "trial_completed" for event in resumed_events) == 1


def test_execute_provider_usage_unknown_fails_closed_when_reconciliation_cannot_settle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingGate:
        def __init__(self) -> None:
            self.calls = 0

        def conservatively_settle_active_trial_unknown_usage(self, **_: object) -> SimpleNamespace:
            self.calls += 1
            raise ValueError("active paid reservation does not belong to the Trial")

    gate = FailingGate()

    async def unknown_usage(**_: object) -> tuple[str, dict[str, object], list[tuple[int, int]]]:
        raise ProviderUsageUnknown("active reservation retained")

    monkeypatch.setenv("BAILIAN_API_KEY", "test-only-not-a-real-key")
    monkeypatch.setattr("evals.retention_study.PaidRunGate", lambda **_: gate)
    monkeypatch.setattr("evals.retention_study.provider_request_budget_scope", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr("evals.retention_study._run_session", unknown_usage)
    recorders = execute(
        root=Path.cwd(), studies_path=STUDIES, runs_dir=tmp_path,
        run_prefix="retention-unknown-usage-reconciliation-failure", pricing_snapshot=Path(
            "evals/goal2/pricing_bailian_qwen37_max_2026-07-13.json"
        ), budget_authorization=tmp_path / "authorization.json",
        budget_ledger=tmp_path / "ledger.json", budget_allocation=tmp_path / "allocation.json",
        confirmed=True,
    )
    assert gate.calls == 1
    completed = [json.loads(line) for line in (
        recorders[0].path / "events.jsonl"
    ).read_text().splitlines() if json.loads(line)["type"] == "trial_completed"][-1]
    assert completed["status"] == "infrastructure_error"
    assert completed["provider_usage_unknown"] is True
    assert completed["budget_reconciliation_required"] is True
    assert completed["reconciliation_error_category"] == "ValueError"
    assert completed.get("budget_reconciled_conservatively") is None


@pytest.mark.parametrize(
    ("scenario", "expected_status"),
    [("success", "success"), ("budget_blocked", "budget_blocked"), ("timeout", "infrastructure_error")],
)
def test_execute_non_unknown_usage_paths_do_not_conservatively_settle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str, expected_status: str,
) -> None:
    class FakeGate:
        def __init__(self) -> None:
            self.conservative_settlement_calls = 0

        def conservatively_settle_active_trial_unknown_usage(self, **_: object) -> SimpleNamespace:
            self.conservative_settlement_calls += 1
            raise AssertionError("non-unknown-usage paths must not settle conservatively")

        def trial_accounting(self, _: str) -> dict[str, object]:
            return {
                "actual_cny": "0", "request_count": 0,
                "budget_blocked": scenario == "budget_blocked",
                "budget_block_reasons": ["stage budget exhausted"] if scenario == "budget_blocked" else [],
                "active_reservation": None,
            }

    async def session(**_: object) -> tuple[str, dict[str, object], list[tuple[int, int]]]:
        if scenario == "timeout":
            raise TimeoutError("offline timeout")
        return "success", {
            "ordered_exact_match": True, "expected_count": 2,
            "successful_compactions": 1, "minimum_compactions": 1,
        }, []

    gate = FakeGate()
    monkeypatch.setenv("BAILIAN_API_KEY", "test-only-not-a-real-key")
    monkeypatch.setattr("evals.retention_study.PaidRunGate", lambda **_: gate)
    monkeypatch.setattr("evals.retention_study.provider_request_budget_scope", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr("evals.retention_study._run_session", session)
    recorders = execute(
        root=Path.cwd(), studies_path=STUDIES, runs_dir=tmp_path,
        run_prefix=f"retention-{scenario}", pricing_snapshot=Path(
            "evals/goal2/pricing_bailian_qwen37_max_2026-07-13.json"
        ), budget_authorization=tmp_path / "authorization.json",
        budget_ledger=tmp_path / "ledger.json", budget_allocation=tmp_path / "allocation.json",
        confirmed=True, scope="pilot",
    )
    assert gate.conservative_settlement_calls == 0
    completed = [json.loads(line) for line in (
        recorders[0].path / "events.jsonl"
    ).read_text().splitlines() if json.loads(line)["type"] == "trial_completed"][-1]
    assert completed["status"] == expected_status


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

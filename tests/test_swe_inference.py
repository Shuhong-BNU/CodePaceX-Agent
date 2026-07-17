import json
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from evals.swe_bench_live import select_formal_instances, select_pilot_instances, select_repeated_subset
from evals.benchmark import RunManifest, RunRecorder
from evals.swe_inference import (
    budget_trial_id,
    collect_official_outcomes,
    freeze_matrix,
    load_validated_matrix,
    load_official_environment,
    official_evaluator_preflight,
    stage_instance_ids,
)
import evals.swe_inference as swe_inference


OFFICIAL_ENVIRONMENT = Path("evals/goal2/swe_official_environment.json")


def test_budget_trial_id_is_run_scoped() -> None:
    first = budget_trial_id(
        run_id="run-a", stage="formal", repeat_index=None, instance_id="instance-1",
    )
    assert first == "swe/run-a/formal/1/instance-1"
    assert first != budget_trial_id(
        run_id="run-b", stage="formal", repeat_index=None, instance_id="instance-1",
    )
    assert first != budget_trial_id(
        run_id="run-a", stage="formal", repeat_index=2, instance_id="instance-1",
    )


def _patch(count: int) -> str:
    return "\n".join(
        f"--- a/f{i}.py\n+++ b/f{i}.py\n@@ -1 +1 @@\n-a\n+b"
        for i in range(count)
    )


def _dataset() -> list[dict[str, object]]:
    items = []
    counter = 0
    for label, files, count in (("one", 1, 12), ("medium", 3, 12), ("large", 5, 8)):
        for index in range(count):
            counter += 1
            items.append({
                "instance_id": f"{label}-{index:02d}", "repo": f"org/repo-{counter:02d}",
                "base_commit": f"{counter:040x}", "problem_statement": f"fix {label} {index}",
                "patch": _patch(files), "test_patch": "", "platform": "linux",
            })
    return items


def _write_jsonl(path: Path, items: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(item) + "\n" for item in items), encoding="utf-8")


def test_freeze_and_validate_exact_official_payload_hashes(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    matrix = tmp_path / "matrix.json"
    items = _dataset()
    _write_jsonl(dataset, items)
    payload = freeze_matrix(
        dataset_jsonl=dataset, output=matrix, dataset_revision="official-sha",
        codepacex_commit="code-sha", model="qwen", provider="bailian",
    )
    assert len(stage_instance_ids(payload, stage="pilot")) == 3
    assert len(stage_instance_ids(payload, stage="formal")) == 20
    assert len(stage_instance_ids(payload, stage="repeat")) == 5
    load_validated_matrix(matrix_path=matrix, dataset_jsonl=dataset)

    items[0]["problem_statement"] = "tampered"
    _write_jsonl(dataset, items)
    with pytest.raises(ValueError, match="JSONL hash"):
        load_validated_matrix(matrix_path=matrix, dataset_jsonl=dataset)


def test_official_outcome_collection_requires_every_instance(tmp_path: Path) -> None:
    (tmp_path / "one.json").write_text(json.dumps({"one": {"resolved": True}}))
    (tmp_path / "summary.json").write_text(json.dumps({"unresolved_ids": ["two"]}))
    assert collect_official_outcomes(tmp_path, {"one", "two"}) == {
        "one": True, "two": False,
    }
    with pytest.raises(ValueError, match="incomplete"):
        collect_official_outcomes(tmp_path, {"one", "two", "three"})


def test_selection_helpers_used_by_freezer_remain_disjoint() -> None:
    items = _dataset()
    pilot = select_pilot_instances(items)
    formal = select_formal_instances(
        items, pilot_instance_ids={str(item["instance_id"]) for item in pilot},
    )
    repeated = select_repeated_subset(formal)
    assert not {item["instance_id"] for item in pilot} & {item["instance_id"] for item in formal}
    assert {item["instance_id"] for item in repeated} <= {item["instance_id"] for item in formal}


def test_official_environment_freezes_exact_python_only_revision() -> None:
    environment = load_official_environment(OFFICIAL_ENVIRONMENT)
    assert environment["repository"] == "https://github.com/microsoft/SWE-bench-Live"
    assert environment["branch"] == "python-only"
    assert environment["commit"] == "ad79b850f15e33992e96f03f6e97f05ddf9aa0be"
    assert environment["evaluator_namespace"] == "starryzhang"
    assert environment["arm64_evaluator_architecture"] == "x86_64"
    assert environment["split"] == "lite"


def test_preflight_rejects_wrong_installed_official_revision(tmp_path: Path) -> None:
    package = tmp_path / "checkout" / "swebench" / "__init__.py"
    package.parent.mkdir(parents=True)
    package.write_text("", encoding="utf-8")
    (tmp_path / "checkout" / ".git").mkdir()
    docker = SimpleNamespace(returncode=0, stdout="29.6.1\n")
    git = SimpleNamespace(returncode=0, stdout="wrong-revision\n")
    with patch(
        "evals.swe_inference.importlib.util.find_spec",
        return_value=SimpleNamespace(origin=str(package)),
    ), patch("evals.swe_inference.subprocess.run", side_effect=[git, docker]):
        result = official_evaluator_preflight(OFFICIAL_ENVIRONMENT)
    assert result["official_evaluator_module_available"] is True
    assert result["evaluator_revision_matches"] is False
    assert result["official_evaluator_available"] is False


def test_swe_request_ceiling_uses_child_request_bridge_not_trial_reservation() -> None:
    source = inspect.getsource(swe_inference.execute)
    assert "maximum_requests=MAXIMUM_REQUESTS_PER_INSTANCE" not in source
    assert "provider_request_budget_environment(" in source
    assert "with gate.locked()" not in source


@pytest.mark.parametrize(
    ("trace_count", "accounting", "reason"), [
        (0, {"request_count": 0, "active_reservation": None, "usage_unknown": False, "actual_cny": "0.000000"}, "missing_trace_usage"),
        (1, {"request_count": 1, "active_reservation": {"reservation_id": "reserved"}, "usage_unknown": False, "actual_cny": "0.000000"}, "active_reservation"),
        (1, {"request_count": 2, "active_reservation": None, "usage_unknown": False, "actual_cny": "0.000001"}, "request_count_mismatch"),
    ],
)
def test_swe_reconciliation_failure_records_one_terminal_trial(
    tmp_path: Path, trace_count: int, accounting: dict[str, object], reason: str,
) -> None:
    recorder = RunRecorder(
        tmp_path, RunManifest(experiment_kind="swe_bench_live"), run_id=f"reconcile-{reason}",
    )
    recorder.event("trial_started", {"task_id": "instance", "repetition_id": "1", "attempt_id": 1})
    swe_inference._record_reconciliation_failure(
        recorder, instance_id="instance", repeat_index=0, trial_id="swe/run/pilot/1/instance",
        duration_seconds=1.0, trace_request_count=trace_count,
        accounting=accounting, reason=reason,
    )
    recorder.finalize({"status": "infrastructure_error", "execution_mode": "live"})
    events = [json.loads(line) for line in (recorder.path / "events.jsonl").read_text().splitlines()]
    completed = [event for event in events if event["type"] == "trial_completed"]
    assert len(completed) == 1
    assert completed[0]["status"] == "infrastructure_error"
    assert completed[0]["budget_reconciliation_required"] is True
    assert completed[0]["reconciliation_reason"] == reason
    result = json.loads((recorder.path / "result.json").read_text())
    assert result["completed_trial_count"] == 1
    assert not (recorder.path / "usage.json").exists()


def test_swe_evaluator_failure_closes_each_pending_trial(tmp_path: Path) -> None:
    recorder = RunRecorder(
        tmp_path, RunManifest(experiment_kind="swe_bench_live"), run_id="evaluator-failure",
    )
    pending = []
    for instance_id in ("one", "two"):
        recorder.event("trial_started", {"task_id": instance_id, "repetition_id": "1", "attempt_id": 1})
        pending.append({"instance_id": instance_id, "duration_seconds": 1.0, "actual_cny": "0.000001"})
    swe_inference._complete_pending_evaluator_failure(
        recorder, pending, repeat_index=0, reason="official_evaluator_failed",
    )
    recorder.finalize({"status": "infrastructure_error", "execution_mode": "live"})
    result = json.loads((recorder.path / "result.json").read_text())
    assert result["completed_trial_count"] == 2

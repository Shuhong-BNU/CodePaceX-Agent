from __future__ import annotations

import pytest

from evals.swe_bench_live import (
    instance_payload_hash,
    patch_file_count,
    select_formal_instances,
    select_pilot_instances,
    select_repeated_subset,
    size_bucket,
    validate_predictions,
    write_goal2_manifest,
)


def _patch(count: int) -> str:
    return "\n".join(
        f"--- a/file_{index}.py\n+++ b/file_{index}.py\n@@ -1 +1 @@\n-old\n+new"
        for index in range(count)
    )


def _instances() -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    sequence = [("one", 1, 8), ("medium", 3, 8), ("large", 5, 4)]
    counter = 0
    for label, files, count in sequence:
        for index in range(count):
            counter += 1
            items.append({
                "instance_id": f"formal-{label}-{index:02d}",
                "repo": f"repo-{counter:02d}", "platform": "linux",
                "patch": _patch(files),
            })
    items.append({
        "instance_id": "pilot-one", "repo": "pilot-repo",
        "platform": "linux", "patch": _patch(1),
    })
    return list(reversed(items))


def test_patch_size_buckets_are_derived_from_gold_diff() -> None:
    assert patch_file_count(_patch(4)) == 4
    assert size_bucket({"patch": _patch(1)}) == "one_file"
    assert size_bucket({"patch": _patch(4)}) == "two_to_four_files"
    assert size_bucket({"patch": _patch(5)}) == "five_plus_files"


def test_formal_selection_excludes_pilot_and_freezes_8_8_4_plus_repeat_2_2_1() -> None:
    formal = select_formal_instances(_instances(), pilot_instance_ids={"pilot-one"})
    assert len(formal) == 20
    assert [sum(size_bucket(item) == bucket for item in formal) for bucket in (
        "one_file", "two_to_four_files", "five_plus_files"
    )] == [8, 8, 4]
    repeated = select_repeated_subset(formal)
    assert len(repeated) == 5
    assert [sum(size_bucket(item) == bucket for item in repeated) for bucket in (
        "one_file", "two_to_four_files", "five_plus_files"
    )] == [2, 2, 1]


def test_pilot_selection_freezes_one_instance_per_size_bucket() -> None:
    pilot = select_pilot_instances(_instances())
    assert len(pilot) == 3
    assert {size_bucket(item) for item in pilot} == {
        "one_file", "two_to_four_files", "five_plus_files",
    }
    assert len({item["repo"] for item in pilot}) == 3


def test_empty_or_off_manifest_predictions_fail_closed() -> None:
    with pytest.raises(ValueError, match="empty model patch"):
        validate_predictions(
            [{"instance_id": "one", "model_patch": ""}],
            required_instance_ids={"one"},
        )


def test_goal2_manifest_records_revision_buckets_and_disjoint_pilot(tmp_path) -> None:
    instances = _instances()
    pilot = [
        {"instance_id": f"pilot-{index}", "repo": f"pilot-repo-{index}", "patch": _patch(1)}
        for index in range(3)
    ]
    formal = select_formal_instances(instances, pilot_instance_ids={"pilot-one"})
    repeated = select_repeated_subset(formal)
    path = tmp_path / "manifest.json"
    write_goal2_manifest(
        pilot_instances=pilot, formal_instances=formal,
        repeated_instances=repeated, path=path,
        dataset_name="SWE-bench-Live/SWE-bench-Live", revision="dataset-sha",
        codepacex_commit="code-sha", model="qwen", provider="bailian",
    )
    import json

    payload = json.loads(path.read_text())
    assert payload["dataset_branch"] == "python-only"
    assert payload["split"] == "lite"
    assert payload["dataset_revision"] == "dataset-sha"
    assert len(payload["formal_instances"]) == 20
    assert payload["source_repository"] == "https://github.com/microsoft/SWE-bench-Live"
    assert payload["evaluator_namespace"] == "starryzhang"
    assert len(payload["instance_payload_hashes"]) == 23
    assert payload["instance_payload_hashes"]["pilot-0"] == instance_payload_hash(pilot[0])
    with pytest.raises(ValueError, match="exactly match"):
        validate_predictions(
            [{"instance_id": "other", "model_patch": _patch(1)}],
            required_instance_ids={"one"},
        )

import json
from pathlib import Path

import pytest

from evals.swe_bench_live import select_formal_instances, select_pilot_instances, select_repeated_subset
from evals.swe_inference import (
    collect_official_outcomes,
    freeze_matrix,
    load_validated_matrix,
    stage_instance_ids,
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

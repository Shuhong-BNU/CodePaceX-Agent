import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from evals.swe_bench_live import select_formal_instances, select_pilot_instances, select_repeated_subset
from evals.swe_inference import (
    collect_official_outcomes,
    freeze_matrix,
    load_validated_matrix,
    load_official_environment,
    official_evaluator_preflight,
    stage_instance_ids,
)


OFFICIAL_ENVIRONMENT = Path("evals/goal2/swe_official_environment.json")


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

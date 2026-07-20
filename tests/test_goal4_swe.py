from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

import evals.goal4_swe as goal4


PRICING = Path("evals/goal2/pricing_bailian_qwen37_max_2026-07-13.json")


def _patch(count: int) -> str:
    return "\n".join(
        f"--- a/f{index}.py\n+++ b/f{index}.py\n@@ -1 +1 @@\n-old\n+new"
        for index in range(count)
    )


def _rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    index = 0
    for label, files, count in (("one", 1, 12), ("medium", 3, 12), ("large", 5, 8)):
        for number in range(count):
            index += 1
            rows.append({
                "instance_id": f"{label}-{number:02d}", "repo": f"org/repo-{index:02d}",
                "base_commit": f"{index:040x}", "problem_statement": f"fix {label} {number}",
                "patch": _patch(files), "test_patch": "test", "platform": "linux",
                "version": "1", "environment_setup_commit": f"{index:040x}",
            })
    return list(reversed(rows))


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_matrix_is_deterministic_stratified_and_excludes_goal3() -> None:
    rows = _rows()
    rows.append({
        "instance_id": "amoffat__sh-744", "repo": "excluded/repo", "platform": "linux",
        "patch": _patch(1), "base_commit": "a" * 40, "problem_statement": "excluded",
        "test_patch": "", "version": "1", "environment_setup_commit": "a" * 40,
    })
    selected = goal4.select_formal_matrix(rows)
    assert len(selected) == 20
    assert not {str(item["instance_id"]) for item in selected} & goal4.GOAL3_PILOT_IDS
    assert [sum(goal4.size_bucket(item) == bucket for item in selected) for bucket in goal4.FORMAL_SIZE_TARGETS] == [8, 8, 4]
    assert max(list({str(item["repo"]): sum(str(other["repo"]) == str(item["repo"]) for other in selected) for item in selected}.values())) <= 2
    assignments = goal4.assign_batches(selected)
    assert sum(batch == "A" for _item, batch in assignments) == 5
    assert sum(batch == "B" for _item, batch in assignments) == 15


def test_freeze_sanitizes_gold_data_and_validates_batches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, output = tmp_path / "source.jsonl", tmp_path / "goal4-freeze"
    _write_jsonl(source, _rows())
    monkeypatch.setattr(goal4, "current_git_commit", lambda _root: "c" * 40)
    monkeypatch.setattr(goal4, "_tree_hash", lambda _root: "d" * 40)
    frozen = goal4.freeze_formal_bundle(
        root=tmp_path, dataset_jsonl=source, pricing_snapshot=PRICING,
        output_dir=output, dataset_revision="a" * 40,
    )
    assert len(frozen["tasks"]) == 20
    assert {task["batch"] for task in frozen["tasks"]} == {"A", "B"}
    agent_rows = [json.loads(line) for line in (output / "formal-dataset.jsonl").read_text().splitlines()]
    assert len(agent_rows) == 20
    assert all("patch" not in row and "test_patch" not in row for row in agent_rows)
    assert goal4.load_formal_freeze(output / "formal-freeze.json")["matrix_sha256"] == frozen["matrix_sha256"]
    assert len(goal4.load_formal_instances(frozen=frozen, dataset_jsonl=output / "formal-dataset.jsonl")) == 20


def test_parent_and_child_budget_artifacts_are_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, output = tmp_path / "source.jsonl", tmp_path / "goal4-swe"
    _write_jsonl(source, _rows())
    monkeypatch.setattr(goal4, "current_git_commit", lambda _root: "c" * 40)
    monkeypatch.setattr(goal4, "_tree_hash", lambda _root: "d" * 40)
    goal4.freeze_formal_bundle(
        root=tmp_path, dataset_jsonl=source, pricing_snapshot=PRICING,
        output_dir=output, dataset_revision="a" * 40,
    )
    prepared = goal4.prepare_paid_artifacts(
        root=tmp_path, freeze_path=output / "formal-freeze.json",
        pricing_path=output / "pricing-snapshot.json", evidence_root=output,
    )
    assert prepared["parent_authorization_cny"] == "1684.439040"
    zero = goal4.zero_provider_check(
        root=tmp_path, freeze_path=output / "formal-freeze.json",
        pricing_path=output / "pricing-snapshot.json", evidence_root=output,
    )
    assert zero["provider_requests"] == 0
    parent = json.loads((output / "accounts" / "parent-ledger.json").read_text())
    assert parent["active_reservation"] is None
    assert parent["request_charge_count"] == 0


def test_budget_contract_is_exact() -> None:
    assert goal4.BATCH_AUTHORIZATION["A"] == goal4.Decimal("421.109760")
    assert goal4.BATCH_AUTHORIZATION["B"] == goal4.Decimal("1263.329280")
    assert goal4.PARENT_AUTHORIZATION == goal4.Decimal("1684.439040")


def test_pilot_schema_compatibility_does_not_change_formal_request_ceiling() -> None:
    config = goal4._pilot_config({
        "provider": "bailian-qwen37-max", "protocol": "openai-compat",
        "base_url": "https://llm-ipge9fy38w648m28.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "BAILIAN_API_KEY", "model_id": "qwen3.7-max-2026-06-08",
        "model_parameters": goal4.FORMAL_MODEL_PARAMETERS,
        "experiment_profile": goal4._goal4_profile().canonical_payload(),
    })
    assert config.max_iterations == 50
    assert goal4.MAXIMUM_REQUESTS_PER_INSTANCE == 40


def test_empty_control_accepts_official_empty_patch_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.jsonl"
    instance = _rows()[0]
    instance_id = str(instance["instance_id"])
    _write_jsonl(source, [instance])
    monkeypatch.setattr(goal4, "current_git_commit", lambda _root: "c" * 40)
    monkeypatch.setattr(goal4, "require_native_preflight", lambda *, root: {
        "installed_evaluator_commit": "e" * 40,
    })

    def fake_evaluator(**kwargs: object) -> subprocess.CompletedProcess[str]:
        cwd = Path(str(kwargs["cwd"]))
        evaluator_logs = cwd / "logs"
        evaluator_logs.mkdir()
        (evaluator_logs / "upstream.txt").write_text("AWS" + "::LanguageExtensions")
        (cwd / "official-empty-summary.json").write_text(json.dumps({
            "empty_patch_ids": [instance_id],
        }), encoding="utf-8")
        return subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="No instances to run.\nInstances with empty patches: 1\n",
            stderr="",
        )

    monkeypatch.setattr(goal4, "run_official_evaluator", fake_evaluator)
    recorder = goal4.run_control(
        root=tmp_path, source_dataset_jsonl=source, instance_id=instance_id,
        control="empty", runs_dir=tmp_path / "goal4-controls", run_id="empty-control",
    )
    result = json.loads((recorder.path / "result.json").read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (recorder.path / "events.jsonl").read_text().splitlines()]
    terminal = next(event for event in events if event["type"] == "control_completed")
    assert result["status"] == "success"
    assert result["resolved"] is False
    assert result["official_evaluator_completed"] is True
    assert result["empty_patch_rejected_by_evaluator"] is True
    assert not (recorder.path / "predictions.json").exists()
    assert not (recorder.path / "logs").exists()
    assert terminal["evaluator_completed"] is True
    assert terminal["empty_patch_rejected_by_evaluator"] is True

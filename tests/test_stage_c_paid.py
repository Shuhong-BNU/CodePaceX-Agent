from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from codepacex.agent import Agent
from codepacex.client import create_client
from codepacex.config import load_config
from codepacex.tools import create_default_registry
from evals import stage_c, stage_c_paid
from evals.paid_gate import BudgetLedger, ProviderRequestBudget


ROOT = Path(".").resolve()
FREEZE = ROOT / "evals/stage_c"


def _commit() -> str:
    return stage_c_paid.current_git_commit(ROOT)


def _identities() -> dict[str, str]:
    return stage_c_paid.frozen_identities(ROOT, FREEZE)


def _prepare(tmp_path: Path, *, phase: stage_c_paid.Phase = "phase_1") -> dict:
    identities = _identities()
    return stage_c_paid.prepare_phase(
        root=ROOT, freeze_dir=FREEZE, evidence_root=tmp_path, phase=phase,
        approved_commit=_commit(), authorization_identity="test-authorization",
        supplied_freeze_sha256=identities["freeze_sha256"],
        supplied_pricing_hash=identities["pricing_snapshot_hash"],
    )


def test_paid_phase_binds_the_frozen_stage_b_and_pricing_identities(tmp_path: Path) -> None:
    prepared = _prepare(tmp_path)
    assert prepared["phase"] == "phase_1"
    assert prepared["task_ids"] == list(stage_c.PHASE_1_IDS)
    assert prepared["authorization_cap_cny"] == "80"
    assert prepared["next_request_maximum_cny"] == "1.830912"
    assert prepared["provider_requests"] == 0
    binding = json.loads((tmp_path / "phase-authorization.json").read_text())
    assert binding["experiment_profile_hash"] == stage_c.stage_c_profile().profile_hash()
    assert binding["runtime_contract_hash"] == stage_c.stage_c_profile().runtime_contract_hash()


def test_paid_phase_rejects_an_authorization_hash_mismatch_before_any_ledger_exists(tmp_path: Path) -> None:
    identities = _identities()
    with pytest.raises(ValueError, match="freeze hash"):
        stage_c_paid.prepare_phase(
            root=ROOT, freeze_dir=FREEZE, evidence_root=tmp_path, phase="phase_1",
            approved_commit=_commit(), authorization_identity="test-authorization",
            supplied_freeze_sha256="0" * 64, supplied_pricing_hash=identities["pricing_snapshot_hash"],
        )
    assert not list(tmp_path.iterdir())


def test_stage_c_bootstraps_and_initializes_agent_from_a_clean_workspace_without_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover the real paid-run failure before any Provider transport is allowed.

    The workspace deliberately has no project config.  Stage C must generate a
    temporary HOME config, load it through CodePaceX's normal discovery path,
    and initialize a real Agent.  The dummy key is a test fixture only; this
    test never invokes ``Agent.run`` or a client stream method.
    """
    home = tmp_path / "isolated-home"
    workspace = tmp_path / "fresh-workspace"
    workspace.mkdir()
    assert not (workspace / ".codepacex" / "config.yaml").exists()
    assert not (home / ".codepacex" / "config.yaml").exists()
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("BAILIAN_API_KEY", "offline-bootstrap-fixture")

    config_path = stage_c_paid._write_child_config(stage_c_paid._pilot_config(), home)
    assert config_path == home / ".codepacex" / "config.yaml"
    assert "offline-bootstrap-fixture" not in config_path.read_text(encoding="utf-8")

    config = load_config()
    provider = config.providers[0]
    assert (provider.name, provider.protocol, provider.base_url, provider.model) == (
        "bailian-qwen37-max",
        "openai-compat",
        "https://llm-ipge9fy38w648m28.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        "qwen3.7-max-2026-06-08",
    )
    assert config.fallback == []
    assert stage_c_paid._pilot_config().retry_budget == 0

    # Constructing the concrete client and Agent reaches the exact startup
    # boundary before Provider transport; no completion/stream call occurs.
    agent = Agent(
        client=create_client(provider, max_retries=0), registry=create_default_registry(),
        protocol=provider.protocol, work_dir=str(workspace), max_iterations=stage_c.MAX_REQUESTS,
        active_provider=provider, providers=config.providers, fallback=config.fallback,
        experiment_profile=stage_c.stage_c_profile(),
    )
    assert agent.active_provider is provider
    assert agent.max_iterations == stage_c.MAX_REQUESTS
    assert not (workspace / ".codepacex" / "config.yaml").exists()

    # No gate is opened and no Agent turn is run: therefore no Provider request,
    # Usage, charge, settlement, or active reservation can exist in this test.
    evidence_root = tmp_path / "zero-provider-evidence"
    _prepare(evidence_root)
    ledger = BudgetLedger.model_validate_json((evidence_root / "terminal-ledger.json").read_text())
    assert ledger.active_reservation is None
    assert ledger.request_charges == []
    assert ledger.settlements == []


def test_paid_phase_reserves_exactly_one_request_and_settles_to_no_active_reservation(tmp_path: Path) -> None:
    _prepare(tmp_path)
    with patch("evals.paid_gate._git_is_clean", return_value=True):
        _binding, gate = stage_c_paid._load_phase_gate(root=ROOT, evidence_root=tmp_path)
        budget = ProviderRequestBudget(
            gate, trial_id="swe/stage-c/test/phase_1/1/aws-cloudformation__cfn-lint-3749",
            maximum_input_tokens_per_request=stage_c.MAX_INPUT_TOKENS,
            maximum_output_tokens_per_request=stage_c.MAX_OUTPUT_TOKENS,
            maximum_reasoning_tokens_per_request=stage_c.MAX_REASONING_TOKENS,
            maximum_provider_requests_per_trial=40,
        )
        reservation = budget.reserve_before_request()
        assert reservation.maximum_requests == 1
        assert reservation.reserved_cny == Decimal("1.830912")
        budget.settle_after_usage(reservation, {"prompt_tokens": 10, "completion_tokens": 1})
    ledger = BudgetLedger.model_validate_json((tmp_path / "terminal-ledger.json").read_text())
    assert ledger.active_reservation is None
    assert len(ledger.request_charges) == len(ledger.settlements) == 1


def test_task_bundle_rejects_gold_and_historical_data(tmp_path: Path) -> None:
    task = {
        "instance_id": stage_c.PHASE_1_IDS[0], "repo": "owner/repo", "base_commit": "a" * 40,
        "problem_statement": "fix it", "platform": "linux", "version": "1", "environment_setup_commit": "b" * 40,
        "patch": "must never enter the Agent bundle",
    }
    path = tmp_path / "tasks.jsonl"
    path.write_text(json.dumps(task) + "\n")
    with pytest.raises(ValueError, match="seven Agent-visible"):
        stage_c_paid.load_agent_task_bundle(path, phase="phase_1")


def test_task_bundle_preserves_nullable_formal_dataset_metadata(tmp_path: Path) -> None:
    source = tmp_path / "formal-dataset.jsonl"
    source.write_text("".join(json.dumps({
        "instance_id": instance_id, "repo": "owner/repo", "base_commit": "a" * 40,
        "problem_statement": "Repair the current repository.", "platform": None,
        "version": None, "environment_setup_commit": None,
    }) + "\n" for instance_id in stage_c.PHASE_1_IDS))
    output = tmp_path / "tasks.jsonl"
    built = stage_c_paid.build_agent_task_bundle(source_dataset=source, output=output, phase="phase_1")
    rows = stage_c_paid.load_agent_task_bundle(output, phase="phase_1")
    assert built["task_ids"] == list(stage_c.PHASE_1_IDS)
    assert all(row["platform"] is None and row["version"] is None and row["environment_setup_commit"] is None for row in rows)


def test_phase_two_deducts_verified_phase_one_conservative_consumption(tmp_path: Path) -> None:
    ledger = tmp_path / "terminal-ledger.json"
    report = tmp_path / "phase-report.json"
    ledger.write_text("ledger")
    report.write_text("report")
    artifact = tmp_path / "phase-1.json"
    artifact.write_text(json.dumps({
        "artifact_id": "stage-c-phase_1-run", "phase": "phase_1", "task_ids": list(stage_c.PHASE_1_IDS),
        "phase_1_instance_ids": list(stage_c.PHASE_1_IDS), "phase_2_instance_ids": list(stage_c.PHASE_2_IDS),
        "terminal_statuses": {item: "unresolved" for item in stage_c.PHASE_1_IDS},
        "active_reservation": None, "combined_conservative_consumption_cny": "12.345678",
        "formal_stage_c_trial": True,
        "ledger_sha256": stage_c_paid._sha256(ledger), "report_sha256": stage_c_paid._sha256(report),
    }))
    identities = _identities()
    target = tmp_path / "phase-2"
    prepared = stage_c_paid.prepare_phase(
        root=ROOT, freeze_dir=FREEZE, evidence_root=target, phase="phase_2",
        approved_commit=_commit(), authorization_identity="phase-2-auth",
        supplied_freeze_sha256=identities["freeze_sha256"], supplied_pricing_hash=identities["pricing_snapshot_hash"],
        phase_1_conservative_consumption=Decimal("12.345678"), phase_1_artifact=artifact,
        phase_1_artifact_id="stage-c-phase_1-run", phase_1_archive_sha256="c" * 64,
    )
    assert prepared["authorization_cap_cny"] == "237.654322"
    binding = json.loads((target / "phase-authorization.json").read_text())
    assert binding["phase_1_artifact_id"] == "stage-c-phase_1-run"
    assert binding["phase_1_ledger_sha256"] == stage_c_paid._sha256(ledger)


def test_phase_two_rejects_a_phase_one_budget_block_or_active_reservation(tmp_path: Path) -> None:
    artifact = tmp_path / "bad-phase-1.json"
    artifact.write_text(json.dumps({
        "artifact_id": "stage-c-phase_1-run", "phase": "phase_1", "task_ids": list(stage_c.PHASE_1_IDS),
        "phase_1_instance_ids": list(stage_c.PHASE_1_IDS), "phase_2_instance_ids": list(stage_c.PHASE_2_IDS),
        "terminal_statuses": {item: "unresolved" for item in stage_c.PHASE_1_IDS},
        "active_reservation": {"reservation_id": "still-open"}, "combined_conservative_consumption_cny": "0",
        "formal_stage_c_trial": True,
        "ledger_sha256": "a" * 64, "report_sha256": "b" * 64,
    }))
    with pytest.raises(ValueError, match="active reservation"):
        stage_c_paid.validate_phase_1_artifact(artifact)


def test_synthetic_phase_one_is_serial_and_partial_claim_never_becomes_twenty_task_result(tmp_path: Path) -> None:
    _prepare(tmp_path)
    bundle = tmp_path / "phase-1-tasks.jsonl"
    bundle.write_text("".join(json.dumps({
        "instance_id": instance_id, "repo": "owner/repo", "base_commit": "a" * 40,
        "problem_statement": "Repair the current repository.", "platform": "linux",
        "version": "1", "environment_setup_commit": "b" * 40,
    }) + "\n" for instance_id in stage_c.PHASE_1_IDS))

    def fake_executor(task: dict[str, str], _environment: dict[str, str], _workspace: Path) -> stage_c_paid.TaskExecution:
        return stage_c_paid.TaskExecution("", "", "diff --git a/x b/x\n", 0)

    def fake_evaluator(task: dict[str, str], execution: stage_c_paid.TaskExecution,
                       _recorder: object, _run_id: str) -> stage_c_paid.TaskExecution:
        return stage_c_paid.TaskExecution(execution.stdout, execution.stderr, execution.patch,
                                          execution.returncode, "{\"resolved\": false}", False)

    with patch("evals.paid_gate._git_is_clean", return_value=True):
        artifact = stage_c_paid.execute_phase(
            root=ROOT, freeze_dir=FREEZE, evidence_root=tmp_path, phase="phase_1", run_id="synthetic-phase-1",
            task_bundle=bundle, confirmed=True, executor=fake_executor, evaluator=fake_evaluator,
        )
    assert artifact["provider_requests"] == 0
    assert artifact["formal_stage_c_trial"] is False
    assert artifact["active_reservation"] is None
    assert set(artifact["terminal_statuses"]) == set(stage_c.PHASE_1_IDS)
    report = json.loads((tmp_path / "phase-report.json").read_text())
    assert report["claim_kind"] == "phase_1_smoke_pilot"
    assert report["full_claim"] is False
    assert report["scorable_denominator"] == 6


def test_partial_phase_uses_a_supported_infrastructure_terminal_status(tmp_path: Path) -> None:
    _prepare(tmp_path)
    bundle = tmp_path / "phase-1-tasks.jsonl"
    bundle.write_text("".join(json.dumps({
        "instance_id": instance_id, "repo": "owner/repo", "base_commit": "a" * 40,
        "problem_statement": "Repair the current repository.", "platform": None,
        "version": None, "environment_setup_commit": None,
    }) + "\n" for instance_id in stage_c.PHASE_1_IDS))

    def failing_executor(_task: dict[str, str], _environment: dict[str, str], _workspace: Path) -> stage_c_paid.TaskExecution:
        return stage_c_paid.TaskExecution("", "simulated pre-transport failure", "", 1)

    with patch("evals.paid_gate._git_is_clean", return_value=True):
        artifact = stage_c_paid.execute_phase(
            root=ROOT, freeze_dir=FREEZE, evidence_root=tmp_path, phase="phase_1", run_id="synthetic-partial",
            task_bundle=bundle, confirmed=True, executor=failing_executor,
        )
    assert artifact["terminal_statuses"][stage_c.PHASE_1_IDS[0]] == "infrastructure_error"
    assert all(artifact["terminal_statuses"][item] == "not_run" for item in stage_c.PHASE_1_IDS[1:])
    result_path = next(tmp_path.rglob("result.json"))
    assert json.loads(result_path.read_text())["status"] == "infrastructure_error"


def test_phase_one_continuation_never_reexecutes_the_recovered_first_task(tmp_path: Path) -> None:
    _prepare(tmp_path)
    binding_path = tmp_path / "phase-authorization.json"
    binding = json.loads(binding_path.read_text())
    binding["completed_statuses"] = {stage_c.PHASE_1_IDS[0]: "unresolved"}
    binding_path.write_text(json.dumps(binding))
    bundle = tmp_path / "phase-1-tasks.jsonl"
    bundle.write_text("".join(json.dumps({
        "instance_id": item, "repo": "owner/repo", "base_commit": "a" * 40,
        "problem_statement": "Repair.", "platform": None, "version": None,
        "environment_setup_commit": None,
    }) + "\n" for item in stage_c.PHASE_1_IDS))
    seen: list[str] = []
    def executor(task, _environment, _workspace):
        seen.append(task["instance_id"])
        return stage_c_paid.TaskExecution("", "", "", 0)
    def evaluator(task, execution, _recorder, _run_id):
        return stage_c_paid.TaskExecution(execution.stdout, execution.stderr, execution.patch, 0, "{}", False)
    with patch("evals.paid_gate._git_is_clean", return_value=True):
        artifact = stage_c_paid.execute_phase(root=ROOT, freeze_dir=FREEZE, evidence_root=tmp_path,
            phase="phase_1", run_id="continuation", task_bundle=bundle, confirmed=True,
            executor=executor, evaluator=evaluator)
    assert seen == list(stage_c.PHASE_1_IDS[1:])
    assert artifact["terminal_statuses"][stage_c.PHASE_1_IDS[0]] == "unresolved"

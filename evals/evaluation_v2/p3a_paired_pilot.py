"""P3-A paired-Pilot freeze and zero-provider readiness.

This module intentionally has no paid execution command.  It derives its
identity inputs from the committed Evaluation V2 freeze and only writes
pre-registered P3-A evidence.  P3-B needs a separate explicit authorization.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path
from typing import Any, AsyncIterator, Mapping, Sequence

from codepacex.agent import Agent
from codepacex.capability_v3 import CapabilityV3Config, CapabilityV3Flag
from codepacex.client import LLMClient
from codepacex.conversation import ConversationManager
from codepacex.permissions import DangerousCommandDetector, PathSandbox, PermissionChecker, PermissionMode, RuleEngine
from codepacex.tools import create_default_registry
from codepacex.tools.base import StreamEnd, StreamEvent, TextDelta, ToolCallComplete
from codepacex.tools.run_test import RunTest
from evals.benchmark import canonical_hash, current_git_commit
from evals.costing import load_pricing, pricing_snapshot_hash
from evals.evaluation_v2 import control_canary, full_replay
from evals.paid_gate import BudgetAuthorization, BudgetLedger, PaidRunGate, authorization_hash, worst_case_reservation


SCHEMA_VERSION = 1
EXPERIMENT_NAME = "p3a-strict-paired-pilot"
BOUND_MAIN_COMMIT = "9e076874894ccf155d990fa8a176b2191e258652"
ARTIFACT_DIRECTORY = Path("evals/evaluation_v2/p3a_paired_pilot")
FREEZE_NAME = "p3a-paired-pilot-freeze.json"
MANIFEST_NAME = "8-run-manifest.json"
TREATMENT_ORDER_NAME = "treatment-order-manifest.json"
BUDGET_NAME = "budget-proposal.json"
PARENT_NAME = "parent-authorization-draft.json"
CHILDREN_NAME = "child-allocation-drafts.json"
SCHEMA_NAME = "paired-artifact-schema.json"
READINESS_NAME = "zero-provider-readiness.json"
REHEARSAL_DIRECTORY = "rehearsal"
REQUEST_CEILING = 40
RETRY = 0
FALLBACK = False
SAFETY_RESERVE_CNY = Decimal("0.000001")

# The order is pre-registered, deliberately alternates the first treatment,
# and is the only source of task order for this module.
P3A_TASK_ORDER = (
    ("beetbox__beets-5457", "V2_CONTROL", "V3_CORE"),
    ("deepset-ai__haystack-8489", "V3_CORE", "V2_CONTROL"),
    ("dynaconf__dynaconf-1249", "V2_CONTROL", "V3_CORE"),
    ("delgan__loguru-1297", "V3_CORE", "V2_CONTROL"),
)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tasks(root: Path) -> dict[str, dict[str, Any]]:
    return {item["instance_id"]: item for item in full_replay.load_tasks(root)}


def task_runs(root: Path) -> list[dict[str, Any]]:
    """Materialize the exact eight task-run identities in registered order."""
    tasks = _tasks(root)
    expected_ids = {item[0] for item in P3A_TASK_ORDER}
    if not expected_ids.issubset(tasks):
        raise ValueError("P3-A task is absent from the formal Goal 4 payload")
    runs: list[dict[str, Any]] = []
    for pair_index, (instance_id, first, second) in enumerate(P3A_TASK_ORDER, start=1):
        task = tasks[instance_id]
        for treatment in (first, second):
            ordinal = len(runs) + 1
            runs.append({
                "ordinal": ordinal,
                "pair_index": pair_index,
                "task_run_id": f"p3a-{ordinal:02d}-{instance_id}-{treatment}",
                "instance_id": instance_id,
                "repo": task["repo"],
                "base_commit": task["base_commit"],
                "problem_statement_sha256": hashlib.sha256(
                    task["problem_statement"].encode("utf-8")
                ).hexdigest(),
                "treatment": treatment,
                "expected_artifact_path": f"runs/{ordinal:02d}-{treatment}/tasks/{instance_id}",
            })
    if len(runs) != 8 or len({item["task_run_id"] for item in runs}) != 8:
        raise AssertionError("P3-A requires exactly eight unique task-run identities")
    return runs


def _formal_identities(root: Path) -> dict[str, Any]:
    """Project all model, Prompt, Provider, evaluator and Pricing fields."""
    formal = full_replay.freeze_payload(root)
    pricing = load_pricing(root / full_replay.PRICING_PATH)
    return {
        "model": {"model_id": formal["provider_contract"]["model_id"]},
        "prompt": {
            "construction": full_replay.control_canary.payload_contract(root)["prompt_construction"],
            "system_instruction_sha256": formal["runtime_contract"]["system_instruction_sha256"],
        },
        "provider": dict(formal["provider_contract"]),
        "official_evaluator": dict(formal["official_evaluator"]),
        "tools_and_permissions": dict(formal["runtime_contract"]),
        "pricing": {
            "path": str(full_replay.PRICING_PATH),
            "snapshot_sha256": pricing_snapshot_hash(pricing),
        },
    }


def budget_proposal(root: Path) -> dict[str, Any]:
    """Use frozen Pricing plus selected Goal 4 cost evidence; authorize nothing."""
    pricing = load_pricing(root / full_replay.PRICING_PATH)
    one_request = worst_case_reservation(
        pricing,
        maximum_requests=1,
        maximum_input_tokens_per_request=full_replay.MAX_INPUT_TOKENS,
        maximum_output_tokens_per_request=full_replay.MAX_OUTPUT_TOKENS,
    )
    per_run = one_request * REQUEST_CEILING
    hard_cap = per_run * 8
    selected = {item["instance_id"]: item for item in full_replay._baseline_rows(root)}
    historical = [
        Decimal(str(selected[instance_id]["goal4_selected_terminal_cost_cny"]))
        for instance_id, _first, _second in P3A_TASK_ORDER
    ]
    expected = sum(historical, Decimal("0")) * 2
    # Conservative is the larger historical paired envelope or 50% of the
    # theoretical ceiling.  The hard cap always remains the frozen worst case.
    conservative = max(expected * Decimal("1.5"), hard_cap * Decimal("0.5"))
    return {
        "schema_version": SCHEMA_VERSION,
        "currency": "CNY",
        "pricing_snapshot_path": str(full_replay.PRICING_PATH),
        "pricing_snapshot_sha256": pricing_snapshot_hash(pricing),
        "historical_source": "Goal 4 selected terminal cost rows committed in full_replay payloads",
        "historical_selected_cost_cny_by_task": {
            instance_id: str(cost)
            for (instance_id, _first, _second), cost in zip(P3A_TASK_ORDER, historical)
        },
        "one_request_theoretical_exposure_cny": str(one_request),
        "per_run_theoretical_exposure_cny": str(per_run),
        "expected_proposal_cny": str(expected),
        "conservative_proposal_cny": str(conservative),
        "hard_cap_proposal_cny": str(hard_cap),
        "safety_reserve_cny": str(SAFETY_RESERVE_CNY),
        "request_ceiling_per_run": REQUEST_CEILING,
        "derivation": "expected=2*sum(selected Goal 4 costs for the four tasks); conservative=max(1.5*expected, 50%*hard-cap); hard-cap=8*40*frozen per-request worst case",
        "authorization": "proposal_only_requires_new_explicit_P3_B_paid_authorization",
    }


def _allocation_drafts(runs: Sequence[Mapping[str, Any]], budget: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    parent_id = "p3a-paired-pilot-parent-authorization-draft"
    parent = {
        "status": "draft_not_authorized",
        "authorization_id": parent_id,
        "bound_main_commit": BOUND_MAIN_COMMIT,
        "hard_cap_proposal_cny": budget["hard_cap_proposal_cny"],
        "safety_reserve_cny": budget["safety_reserve_cny"],
        "requires_new_explicit_paid_authorization": True,
    }
    children = []
    for run in runs:
        core = {
            "parent_authorization_id": parent_id,
            "task_run_id": run["task_run_id"],
            "instance_id": run["instance_id"],
            "treatment": run["treatment"],
            "expected_artifact_path": run["expected_artifact_path"],
            "theoretical_ceiling_cny": budget["per_run_theoretical_exposure_cny"],
        }
        children.append({
            **core,
            "status": "draft_not_authorized",
            "child_allocation_id": f"{parent_id}-run-{run['ordinal']:02d}",
            "child_allocation_sha256": canonical_hash(core),
        })
    return parent, children


def paired_artifact_schema() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "comparison_key": ["pair_index", "instance_id", "repo", "base_commit", "problem_statement_sha256"],
        "required_treatments": ["V2_CONTROL", "V3_CORE"],
        "required_result_fields": [
            "task_run_id", "pair_index", "instance_id", "treatment", "provider_requests",
            "usage", "charge_cny", "provider_secret_read", "artifact_path", "terminal_status",
        ],
        "merge_rule": "exactly one V2_CONTROL and one V3_CORE record per identical comparison key; reject all other joins",
        "v2_contract": "V2_CONTROL has no V3 Advice or V3 activation Artifact requirement",
        "v3_contract": "V3_CORE records Advice/activation expectation and raw V3 Artifact requirement",
    }


def merge_paired_results(
    frozen: Mapping[str, Any], results: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Merge exactly the complete frozen four-pair / eight-run manifest.

    The result stream is never self-describing: every record must identify one
    and only one frozen task-run, with all pairing fields equal to that run.
    """
    expected_runs = {item["task_run_id"]: item for item in frozen["task_runs"]}
    if len(expected_runs) != 8 or len({item["pair_index"] for item in expected_runs.values()}) != 4:
        raise ValueError("frozen manifest is not exactly four pairs and eight task-runs")
    if len(results) != 8:
        raise ValueError("paired result must contain exactly eight frozen task-runs")
    actual: dict[str, Mapping[str, Any]] = {}
    fields = ("pair_index", "instance_id", "repo", "base_commit", "problem_statement_sha256", "treatment")
    for result in results:
        task_run_id = str(result.get("task_run_id", ""))
        if task_run_id not in expected_runs:
            raise ValueError("paired result has an unexpected task-run")
        if task_run_id in actual:
            raise ValueError("paired result has a duplicate task-run")
        expected = expected_runs[task_run_id]
        if any(result.get(field) != expected[field] for field in fields):
            raise ValueError("paired result task-run does not match its frozen pair key")
        actual[task_run_id] = result
    if set(actual) != set(expected_runs):
        raise ValueError("paired result omits a frozen task-run or pair")
    grouped: dict[int, dict[str, Mapping[str, Any]]] = {}
    for task_run_id, result in actual.items():
        expected = expected_runs[task_run_id]
        pair = grouped.setdefault(int(expected["pair_index"]), {})
        treatment = str(expected["treatment"])
        if treatment in pair:
            raise ValueError("paired result has a duplicate treatment")
        pair[treatment] = result
    if set(grouped) != {1, 2, 3, 4}:
        raise ValueError("paired result omits an entire frozen pair")
    merged = []
    for pair_index in range(1, 5):
        pair = grouped[pair_index]
        if set(pair) != {"V2_CONTROL", "V3_CORE"}:
            raise ValueError("paired result omits a frozen treatment")
        v2, v3 = pair["V2_CONTROL"], pair["V3_CORE"]
        key = [v2[field] for field in paired_artifact_schema()["comparison_key"]]
        if key != [v3[field] for field in paired_artifact_schema()["comparison_key"]]:
            raise ValueError("paired result treatments do not share an identical pair key")
        merged.append({"comparison_key": key, "V2_CONTROL": v2, "V3_CORE": v3})
    return merged


def freeze_payload(root: Path) -> dict[str, Any]:
    runs = task_runs(root)
    identities = _formal_identities(root)
    budget = budget_proposal(root)
    parent, children = _allocation_drafts(runs, budget)
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_name": EXPERIMENT_NAME,
        "status": "frozen_zero_provider_readiness_only",
        "bound_main_commit": BOUND_MAIN_COMMIT,
        "source_formal_freeze": {
            "path": str(full_replay.COMMITTED_FREEZE),
            "sha256": _sha256(root / full_replay.COMMITTED_FREEZE),
        },
        "task_runs": runs,
        "task_runs_sha256": canonical_hash(runs),
        "treatment_order": [
            {"pair_index": index, "instance_id": task, "order": [first, second]}
            for index, (task, first, second) in enumerate(P3A_TASK_ORDER, start=1)
        ],
        "execution_contract": {
            "strict_serial": True,
            "request_ceiling_per_run": REQUEST_CEILING,
            "retry": RETRY,
            "fallback": FALLBACK,
            "only_treatment_difference": "treatment",
            "paid_execution": False,
            "automatic_retry_rerun_or_continuation": False,
        },
        "frozen_identities": identities,
        "budget_proposal": budget,
        "parent_authorization_draft": parent,
        "child_allocation_drafts": children,
        "paired_artifact_schema": paired_artifact_schema(),
        "p3_b": "blocked_pending_new_explicit_paid_authorization",
    }


def validate_freeze(root: Path, frozen: Mapping[str, Any]) -> None:
    expected = freeze_payload(root)
    if dict(frozen) != expected:
        raise ValueError("P3-A freeze differs from its canonical formal configuration projection")
    runs = frozen["task_runs"]
    if len(runs) != 8 or len({item["task_run_id"] for item in runs}) != 8:
        raise ValueError("P3-A freeze lacks eight unique task runs")
    if len(frozen["child_allocation_drafts"]) != 8:
        raise ValueError("P3-A freeze lacks eight child allocation drafts")


class _RecordingFakeTransport(LLMClient):
    """In-process fake transport that records actual Agent request assembly."""

    def __init__(self, workspace: Path, source: Path) -> None:
        self.workspace, self.source, self.calls = workspace, source, 0
        self.request_records: list[dict[str, Any]] = []

    async def stream(
        self, conversation: Any, system: str = "", tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.calls += 1
        self.request_records.append({
            "system_sha256": hashlib.sha256(system.encode()).hexdigest(),
            "tools_sha256": canonical_hash(tools or []),
            "message_count": len(conversation.get_messages()),
        })
        if self.calls == 1:
            yield ToolCallComplete("read", "ReadFile", {"file_path": str(self.source), "offset": 0, "limit": 20})
        elif self.calls == 2:
            yield ToolCallComplete("edit", "EditFile", {"file_path": str(self.source), "old_string": "VALUE = 0", "new_string": "VALUE = 1"})
        elif self.calls == 3:
            yield ToolCallComplete("test", "RunTest", {"cwd": str(self.workspace), "argv": ["test_rehearsal.py"], "timeout_seconds": 30})
        else:
            yield TextDelta("P3-A recording fake transport complete")
        yield StreamEnd("end_turn")


def _synthetic_task_workspace(_task: Mapping[str, Any], workspace: Path) -> None:
    """A disposable git workspace lets the formal runner execute without a checkout."""
    workspace.mkdir(parents=True)
    (workspace / "tracked.py").write_text("VALUE = 0\n", encoding="utf-8")
    (workspace / "test_rehearsal.py").write_text(
        "def test_rehearsal_workspace_is_available():\n    assert True\n", encoding="utf-8",
    )
    for command in (
        ["git", "init"], ["git", "config", "user.email", "p3a@example.test"],
        ["git", "config", "user.name", "P3-A Zero Provider"], ["git", "add", "."],
        ["git", "commit", "-m", "p3a rehearsal base"],
    ):
        subprocess.run(command, cwd=workspace, check=True, capture_output=True, text=True)


def _rehearsal_gate(root: Path, frozen: Mapping[str, Any], artifact_root: Path) -> PaidRunGate:
    pricing = load_pricing(root / full_replay.PRICING_PATH)
    hard_cap = Decimal(str(frozen["budget_proposal"]["hard_cap_proposal_cny"]))
    authorization = BudgetAuthorization(
        authorized_total_cny=hard_cap,
        stage_limits_cny={"A": hard_cap, "B": hard_cap, "C": hard_cap},
        pricing_snapshot_hash=pricing_snapshot_hash(pricing), experiment_commit=current_git_commit(root),
        authorized_at="p3a-zero-provider-rehearsal", authorized_by="user",
    )
    ledger = BudgetLedger(authorization_hash=authorization_hash(authorization), updated_at="p3a-zero-provider-rehearsal")
    authorization_path, ledger_path = artifact_root / "authorization.json", artifact_root / "ledger.json"
    allocation_path = artifact_root / "stage-c-allocation.json"
    _write_json(authorization_path, authorization.model_dump(mode="json"))
    _write_json(ledger_path, ledger.model_dump(mode="json"))
    allocation = control_canary._fresh_rehearsal_allocation(
        authorization, ledger, pricing_snapshot_hash(pricing),
    )
    _write_json(allocation_path, allocation.model_dump(mode="json"))
    return PaidRunGate(
        root=root, authorization_path=authorization_path, ledger_path=ledger_path,
        pricing_path=root / full_replay.PRICING_PATH, pricing=pricing, stage="C",
        allocation_path=allocation_path,
    )


def run_zero_provider_rehearsal(root: Path, frozen: Mapping[str, Any], artifact_root: Path) -> dict[str, Any]:
    """Exercise manifest -> runner -> Agent -> fake transport -> evaluator -> ledger."""
    validate_freeze(root, frozen)
    if artifact_root.exists():
        raise ValueError("refusing to overwrite P3-A rehearsal Artifact")
    artifact_root.mkdir(parents=True)
    gate = _rehearsal_gate(root, frozen, artifact_root)
    records: list[dict[str, Any]] = []
    tasks = _tasks(root)
    for run in frozen["task_runs"]:
        run_root = artifact_root / "runs" / f"{run['ordinal']:02d}-{run['treatment']}"
        task_root = run_root / "tasks" / run["instance_id"]
        workspace = task_root / "workspace"
        _synthetic_task_workspace(tasks[run["instance_id"]], workspace)
        source = workspace / "tracked.py"
        registry = create_default_registry()
        registry.register(RunTest())
        checker = PermissionChecker(
            DangerousCommandDetector(), PathSandbox(str(workspace)), RuleEngine(),
            PermissionMode.DEFAULT, session_allow_all=True,
        )
        transport = _RecordingFakeTransport(workspace, source)
        treatment = CapabilityV3Flag(run["treatment"])
        agent = Agent(
            transport, registry, "openai-compat", work_dir=str(workspace),
            permission_checker=checker, max_iterations=8,
            capability_v3_config=CapabilityV3Config.from_flag(treatment),
            capability_v3_flag=treatment.value,
            capability_v3_artifact_root=task_root / "capability-v3",
            capability_v3_task_id=run["instance_id"],
            capability_v3_base_commit=run["base_commit"],
        )
        conversation = ConversationManager()
        conversation.add_user_message(str(tasks[run["instance_id"]]["problem_statement"]))

        # This calls the real reserve/cancel ledger wiring immediately around
        # Agent dispatch.  The in-process client never crosses a Provider
        # boundary, so the only valid terminal settlement is CNY-zero cancel.
        reservation = gate.reserve(
            f"swe/p3a-rehearsal/{run['task_run_id']}", maximum_requests=1,
            maximum_input_tokens_per_request=full_replay.MAX_INPUT_TOKENS,
            maximum_output_tokens_per_request=full_replay.MAX_OUTPUT_TOKENS,
        )
        events: list[str] = []

        async def consume() -> None:
            async for event in agent.run(conversation):
                events.append(type(event).__name__)

        try:
            asyncio.run(consume())
        finally:
            gate.cancel(reservation, reason="provider_confirmed_not_submitted")

        patch_text = control_canary._goal3_extract_patch(workspace)
        _write_json(task_root / "agent-request-record.json", {
            "task_run_id": run["task_run_id"], "treatment": treatment.value,
            "fake_transport_calls": transport.calls,
            "assembled_requests": transport.request_records,
            "agent_event_types": events,
        })
        _write_json(task_root / "task-run-contract.json", {
            key: run[key] for key in (
                "task_run_id", "pair_index", "instance_id", "repo", "base_commit",
                "problem_statement_sha256", "treatment",
            )
        })
        (task_root / "candidate.patch").write_text(patch_text, encoding="utf-8")
        prediction_path = task_root / "predictions.json"
        _write_json(prediction_path, [{
            "instance_id": run["instance_id"], "model_name_or_path": "p3a-zero-provider-rehearsal",
            "model_patch": patch_text,
        }])
        evaluator_run_id = f"p3a-rehearsal-{run['ordinal']:02d}"
        evaluator = full_replay._shadow_evaluator_runner(
            predictions_path=prediction_path, instance_ids=[run["instance_id"]],
            run_id=evaluator_run_id, cwd=task_root,
        )
        report_source = (
            task_root / "logs" / "run_evaluation" / evaluator_run_id /
            "p3a-zero-provider-rehearsal" / run["instance_id"] / "report.json"
        )
        if evaluator.returncode != 0 or not report_source.is_file():
            raise RuntimeError("P3-A rehearsal evaluator interface did not emit a raw report")
        shutil.copyfile(report_source, task_root / "official-report.json")
        _write_json(task_root / "task-result.json", {
            "task_run_id": run["task_run_id"], "terminal_status": "zero_provider_rehearsal_only",
            "agent_dispatch_started": transport.calls > 0, "provider_requests": 0,
            "usage": 0, "charge_cny": "0", "provider_secret_read": False,
        })
        fidelity = control_canary._validate_capability_v3_artifact(
            task_root=task_root, workspace=workspace, instance_id=run["instance_id"], treatment=treatment,
        )
        if treatment is CapabilityV3Flag.V3_CORE and not fidelity["valid"]:
            raise RuntimeError(f"P3-A rehearsal V3 artifact failed: {fidelity['errors']}")
        if treatment is CapabilityV3Flag.V2_CONTROL and (task_root / "capability-v3").exists():
            raise RuntimeError("P3-A V2_CONTROL rehearsal unexpectedly wrote a V3 artifact")
        records.append({
            **{key: run[key] for key in ("task_run_id", "pair_index", "instance_id", "repo", "base_commit", "problem_statement_sha256", "treatment", "expected_artifact_path")},
            "artifact_path": str(task_root.relative_to(artifact_root)),
            "task_result_path": str((task_root / "task-result.json").relative_to(artifact_root)),
            "official_report_path": str((task_root / "official-report.json").relative_to(artifact_root)),
            "terminal_status": "zero_provider_rehearsal_only", "evaluator_status": "completed",
            "agent_dispatch_started": transport.calls > 0,
            "recording_fake_transport_requests": transport.calls,
            "provider_requests": 0, "usage": 0, "charge_cny": "0", "provider_secret_read": False,
            "paid_execution": False,
            "v3_advice_present": treatment is CapabilityV3Flag.V3_CORE,
            "v3_activation_schema_present": treatment is CapabilityV3Flag.V3_CORE,
            "treatment_fidelity": fidelity,
        })
    ledger = BudgetLedger.model_validate_json((artifact_root / "ledger.json").read_text(encoding="utf-8"))
    for record in records:
        root_path = artifact_root / record["artifact_path"]
        if not (root_path / "task-result.json").is_file() or not (root_path / "official-report.json").is_file():
            raise RuntimeError("P3-A rehearsal raw task or evaluator Artifact is missing")
        if record["treatment"] == "V3_CORE" and not record["treatment_fidelity"]["valid"]:
            raise RuntimeError("P3-A rehearsal raw V3 Artifact failed validation")
    return {
        "schema_version": SCHEMA_VERSION, "executed": True, "runner": "p3a_paired_pilot.real_agent_dispatch",
        "transport": "recording_fake_llmclient_in_process", "provider_requests": 0, "usage": 0,
        "charge_cny": "0", "provider_secret_read": False, "paid_execution": False,
        "run_records": records, "agent_dispatch_count": sum(item["agent_dispatch_started"] for item in records),
        "recording_fake_transport_requests": sum(item["recording_fake_transport_requests"] for item in records),
        "simulated_provider_requests": len(ledger.request_charges),
        "simulated_usage": sum(item.input_tokens + item.output_tokens for item in ledger.request_charges),
        "simulated_charge_cny": str(ledger.spent_cny), "ledger_settlement_count": len(ledger.settlements),
        "ledger_closed": ledger.active_reservation is None,
        "active_reservation": None if ledger.active_reservation is None else ledger.active_reservation.model_dump(mode="json"),
    }


def readiness_payload(
    root: Path, frozen: Mapping[str, Any], *, freeze_path: Path, rehearsal: Mapping[str, Any],
) -> dict[str, Any]:
    validate_freeze(root, frozen)
    required = (
        rehearsal.get("executed") is True, rehearsal.get("ledger_closed") is True,
        rehearsal.get("active_reservation") is None, rehearsal.get("provider_requests") == 0,
        rehearsal.get("usage") == 0, rehearsal.get("charge_cny") == "0",
        rehearsal.get("provider_secret_read") is False, rehearsal.get("agent_dispatch_count") == 8,
        rehearsal.get("recording_fake_transport_requests", 0) >= 8,
    )
    if not all(required):
        raise ValueError("P3-A readiness requires a completed zero-provider rehearsal and closed zero-cost ledger")
    records = rehearsal.get("run_records")
    if not isinstance(records, list):
        raise ValueError("P3-A readiness requires rehearsal run records")
    for record in records:
        root_path = freeze_path.parent / REHEARSAL_DIRECTORY / record["artifact_path"]
        if not (root_path / "task-result.json").is_file() or not (root_path / "official-report.json").is_file():
            raise ValueError("P3-A readiness requires raw task and evaluator Artifacts")
        if record["treatment"] == "V3_CORE" and not record.get("treatment_fidelity", {}).get("valid"):
            raise ValueError("P3-A readiness requires valid raw V3 Artifacts")
        if record["treatment"] == "V2_CONTROL" and (record.get("v3_advice_present") or record.get("v3_activation_schema_present")):
            raise ValueError("V2_CONTROL must not carry V3 Advice or activation schema")
        if record["treatment"] == "V3_CORE" and not (record.get("v3_advice_present") and record.get("v3_activation_schema_present")):
            raise ValueError("V3_CORE must carry V3 Advice and activation schema")
    merged = merge_paired_results(frozen, records)
    return {
        "schema_version": SCHEMA_VERSION, "freeze_sha256": _sha256(freeze_path),
        "freeze_canonical_sha256": canonical_hash(frozen), "status": "passed_zero_provider_readiness",
        "paid_jobs": "skipped", "provider_requests": 0, "usage": 0, "charge_cny": "0",
        "provider_secret_read": False, "task_run_count": len(records),
        "unique_task_run_count": len({item["task_run_id"] for item in records}),
        "paired_result_merge_count": len(merged), "rehearsal": dict(rehearsal),
        "p3_b": "blocked_pending_new_explicit_paid_authorization",
    }


def write_artifacts(root: Path, output: Path) -> dict[str, str]:
    """Write deterministic P3-A evidence without reading a Secret or dispatching."""
    if output.exists():
        raise ValueError("refusing to overwrite P3-A readiness Artifact")
    frozen = freeze_payload(root)
    output.mkdir(parents=True)
    _write_json(output / FREEZE_NAME, frozen)
    rehearsal = run_zero_provider_rehearsal(root, frozen, output / REHEARSAL_DIRECTORY)
    readiness = readiness_payload(root, frozen, freeze_path=output / FREEZE_NAME, rehearsal=rehearsal)
    _write_json(output / MANIFEST_NAME, {"task_runs": frozen["task_runs"]})
    _write_json(output / TREATMENT_ORDER_NAME, {"treatment_order": frozen["treatment_order"]})
    _write_json(output / BUDGET_NAME, frozen["budget_proposal"])
    _write_json(output / PARENT_NAME, frozen["parent_authorization_draft"])
    _write_json(output / CHILDREN_NAME, {"child_allocation_drafts": frozen["child_allocation_drafts"]})
    _write_json(output / SCHEMA_NAME, frozen["paired_artifact_schema"])
    _write_json(output / READINESS_NAME, readiness)
    return {
        "freeze_sha256": _sha256(output / FREEZE_NAME),
        "freeze_canonical_sha256": canonical_hash(frozen),
        "readiness_sha256": _sha256(output / READINESS_NAME),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P3-A zero-provider paired-Pilot readiness")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(write_artifacts(args.root.resolve(), args.output.resolve()), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

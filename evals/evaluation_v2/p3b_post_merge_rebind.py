"""P3-B0 post-merge binding and zero-provider readiness.

This module creates the *future* P3-B paid contract, but its only executable
path is a local recording-fake rehearsal.  The paid workflow has separate
human inputs and is deliberately never invoked by this module or its tests.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from codepacex.agent import Agent
from codepacex.capability_v3 import CapabilityV3Config, CapabilityV3Flag
from codepacex.conversation import ConversationManager
from codepacex.permissions import DangerousCommandDetector, PathSandbox, PermissionChecker, PermissionMode, RuleEngine
from codepacex.tools import create_default_registry
from codepacex.tools.run_test import RunTest
from evals.benchmark import canonical_hash
from evals.costing import load_pricing, pricing_snapshot_hash
from evals.evaluation_v2 import control_canary, full_replay, p3a_paired_pilot as p3a
from evals.paid_gate import (
    BudgetAuthorization, BudgetLedger, Settlement, StageCBudgetAllocation,
    TaskRunBudgetAllocation, allocation_hash, authorization_hash,
    ledger_fingerprint, task_run_allocation_hash,
)


SCHEMA_VERSION = 1
EXPERIMENT_NAME = "p3b-strict-paired-pilot"
BOUND_MAIN_COMMIT = "2794e27220d3fada3bd0fdd3a1a14ff50e3a6034"
ARTIFACT_DIRECTORY = Path("evals/evaluation_v2/p3b_post_merge_rebind")
FREEZE_NAME = "p3b-paired-pilot-freeze.json"
MANIFEST_NAME = "8-run-manifest.json"
TREATMENT_ORDER_NAME = "treatment-order-manifest.json"
PARENT_NAME = "stage-c-parent-authorization.json"
CHILDREN_NAME = "stage-c-child-allocations.json"
ALLOCATION_NAME = "stage-c-allocation.json"
SCHEMA_NAME = "paired-artifact-schema.json"
READINESS_NAME = "zero-provider-readiness.json"
REHEARSAL_DIRECTORY = "rehearsal"
WORKFLOW_PATH = Path(".github/workflows/p3b-paired-pilot.yml")
REQUEST_CEILING = 40
PARENT_CAP = Decimal("292.945921")
CHILD_CAP = Decimal("36.618240")
SPENDABLE_TOTAL = Decimal("292.945920")
SAFETY_RESERVE = Decimal("0.000001")
DISPATCH_IDENTITY = "p3b-paid-pilot-single-dispatch-v1"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _runtime_hashes(root: Path) -> dict[str, str]:
    paths = [
        Path("evals/evaluation_v2/p3b_post_merge_rebind.py"), WORKFLOW_PATH,
        Path("evals/paid_gate.py"), Path("evals/evaluation_v2/p3a_paired_pilot.py"),
        Path("evals/evaluation_v2/control_canary.py"), Path("evals/evaluation_v2/full_replay.py"),
        Path("codepacex/agent.py"), Path("codepacex/client.py"),
        Path("codepacex/capability_v3/controller.py"),
    ]
    return {str(path): _sha256(root / path) for path in paths}


def _p3a_frozen(root: Path) -> Mapping[str, Any]:
    frozen = p3a.freeze_payload(root)
    if frozen["bound_main_commit"] != p3a.BOUND_MAIN_COMMIT:
        raise ValueError("P3-A history is not the required frozen source")
    return frozen


def task_runs(root: Path) -> list[dict[str, Any]]:
    """Copy only the pre-registered pairing identities from immutable P3-A."""
    runs = [dict(item) for item in _p3a_frozen(root)["task_runs"]]
    for run in runs:
        run["task_run_id"] = run["task_run_id"].replace("p3a-", "p3b-", 1)
        run["expected_artifact_path"] = run["expected_artifact_path"].replace("runs/", "runs/", 1)
    if len(runs) != 8 or len({item["task_run_id"] for item in runs}) != 8:
        raise ValueError("P3-B requires exactly eight unique task-runs")
    return runs


def _identities(root: Path) -> dict[str, Any]:
    historical = _p3a_frozen(root)["frozen_identities"]
    return {
        "model": dict(historical["model"]),
        "prompt": dict(historical["prompt"]),
        "provider": dict(historical["provider"]),
        "official_evaluator": dict(historical["official_evaluator"]),
        "pricing": dict(historical["pricing"]),
    }


def _allocation_binding(root: Path, runs: Sequence[Mapping[str, Any]]) -> tuple[BudgetAuthorization, BudgetLedger, StageCBudgetAllocation]:
    pricing = load_pricing(root / full_replay.PRICING_PATH)
    pricing_hash = pricing_snapshot_hash(pricing)
    authorization = BudgetAuthorization(
        authorized_total_cny=PARENT_CAP,
        stage_limits_cny={"A": PARENT_CAP, "B": PARENT_CAP, "C": PARENT_CAP},
        pricing_snapshot_hash=pricing_hash, experiment_commit=BOUND_MAIN_COMMIT,
        authorized_at="p3b0-post-merge-rebind-proposal", authorized_by="user",
    )
    ledger = BudgetLedger(
        authorization_hash=authorization_hash(authorization),
        updated_at="p3b0-zero-provider-readiness",
    )
    allocations: list[TaskRunBudgetAllocation] = []
    for run in runs:
        payload = {
            "task_run_id": run["task_run_id"],
            "task_run_allocation_id": f"p3b-stage-c-{run['ordinal']:02d}",
            "instance_id": run["instance_id"], "treatment": run["treatment"],
            "expected_artifact_path": run["expected_artifact_path"],
            "execution_run_id": f"{DISPATCH_IDENTITY}-{run['ordinal']:02d}",
            "theoretical_ceiling_cny": str(CHILD_CAP),
        }
        allocations.append(TaskRunBudgetAllocation.model_validate({
            **payload, "task_run_allocation_hash": task_run_allocation_hash(payload),
        }))
    allocation = StageCBudgetAllocation(
        allocation_id="p3b-stage-c-parent", experiment_commit=BOUND_MAIN_COMMIT,
        pricing_snapshot_hash=pricing_hash, baseline_ledger_sha256=ledger_fingerprint(ledger),
        baseline_authorization_hash=authorization_hash(authorization), baseline_spent_cny=Decimal("0"),
        baseline_request_charge_count=0, baseline_settlement_count=0, baseline_budget_block_count=0,
        baseline_rebind_count=0, safety_reserve_cny=SAFETY_RESERVE,
        spendable_total_cny=SPENDABLE_TOTAL,
        category_limits_cny={"swe": SPENDABLE_TOTAL, "mcp": Decimal("0"), "retention": Decimal("0"),
                             "permission": Decimal("0"), "multi_agent": Decimal("0"), "long_session": Decimal("0")},
        task_run_allocations=allocations,
    )
    if sum((item.theoretical_ceiling_cny for item in allocations), Decimal("0")) != SPENDABLE_TOTAL:
        raise AssertionError("eight P3-B child caps must close to spendable total")
    if SPENDABLE_TOTAL + SAFETY_RESERVE != PARENT_CAP:
        raise AssertionError("P3-B parent budget does not close")
    return authorization, ledger, allocation


def paired_artifact_schema() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "comparison_key": ["pair_index", "instance_id", "repo", "base_commit", "problem_statement_sha256"],
        "required_treatments": ["V2_CONTROL", "V3_CORE"],
        "required_raw_artifacts": ["agent-request-record.json", "candidate.patch", "task-result.json", "official-report.json"],
        "v2_contract": "V2_CONTROL must not contain V3 Advice or activation artifact",
        "v3_contract": "V3_CORE requires raw capability-v3 summary.json, events.jsonl, and final.patch",
        "merge_rule": "complete frozen 4-pair/8-run set only; reject missing, duplicate, unexpected, or pair-key mismatch",
        "ledger_contract": "every terminal run has a closed CNY-zero rehearsal cancellation; future paid runs use the same bound allocation",
    }


def merge_paired_results(frozen: Mapping[str, Any], results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    expected = {item["task_run_id"]: item for item in frozen["task_runs"]}
    if len(expected) != 8 or {item["pair_index"] for item in expected.values()} != {1, 2, 3, 4}:
        raise ValueError("frozen manifest is not exactly four pairs and eight task-runs")
    if len(results) != 8:
        raise ValueError("paired merge requires exactly eight results")
    received: dict[str, Mapping[str, Any]] = {}
    fields = ("pair_index", "instance_id", "repo", "base_commit", "problem_statement_sha256", "treatment")
    for result in results:
        task_run_id = str(result.get("task_run_id", ""))
        if task_run_id not in expected:
            raise ValueError("paired merge has unexpected task-run")
        if task_run_id in received:
            raise ValueError("paired merge has duplicate task-run")
        if any(result.get(field) != expected[task_run_id][field] for field in fields):
            raise ValueError("paired merge task and pair key mismatch")
        received[task_run_id] = result
    if set(received) != set(expected):
        raise ValueError("paired merge omits frozen task-run")
    pairs: dict[int, dict[str, Mapping[str, Any]]] = {}
    for task_run_id, result in received.items():
        pair = pairs.setdefault(expected[task_run_id]["pair_index"], {})
        treatment = expected[task_run_id]["treatment"]
        if treatment in pair:
            raise ValueError("paired merge has duplicate treatment")
        pair[treatment] = result
    if set(pairs) != {1, 2, 3, 4}:
        raise ValueError("paired merge omits an entire pair")
    merged = []
    for pair_index in range(1, 5):
        pair = pairs[pair_index]
        if set(pair) != {"V2_CONTROL", "V3_CORE"}:
            raise ValueError("paired merge omits a treatment")
        v2, v3 = pair["V2_CONTROL"], pair["V3_CORE"]
        key = paired_artifact_schema()["comparison_key"]
        if [v2[key_name] for key_name in key] != [v3[key_name] for key_name in key]:
            raise ValueError("paired merge treatment comparison keys differ")
        merged.append({"pair_index": pair_index, "V2_CONTROL": v2, "V3_CORE": v3})
    return merged


class DispatchGuard:
    """Durable one-dispatch permit: replay and a different second dispatch both fail."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def claim(self, *, dispatch_token: str, run_id: str) -> None:
        if not dispatch_token or not run_id or len(dispatch_token) < 12 or len(run_id) < 8:
            raise ValueError("dispatch token and run ID must be explicit safe identities")
        if self.path.exists():
            prior = json.loads(self.path.read_text(encoding="utf-8"))
            if prior == {"dispatch_token": dispatch_token, "run_id": run_id}:
                raise ValueError("duplicate dispatch rejected")
            raise ValueError("second dispatch rejected")
        _write_json(self.path, {"dispatch_token": dispatch_token, "run_id": run_id})


def freeze_payload(root: Path) -> dict[str, Any]:
    runs = task_runs(root)
    authorization, ledger, allocation = _allocation_binding(root, runs)
    p3a_frozen = _p3a_frozen(root)
    return {
        "schema_version": SCHEMA_VERSION, "experiment_name": EXPERIMENT_NAME,
        "status": "formal_paid_freeze_not_authorized", "bound_main_commit": BOUND_MAIN_COMMIT,
        "inherits_p3a_freeze": {"path": str(p3a.ARTIFACT_DIRECTORY / p3a.FREEZE_NAME), "sha256": _sha256(root / p3a.ARTIFACT_DIRECTORY / p3a.FREEZE_NAME), "historical_bound_main_commit": p3a_frozen["bound_main_commit"]},
        "task_runs": runs, "task_runs_sha256": canonical_hash(runs),
        "treatment_order": [{"pair_index": index, "instance_id": task, "order": [first, second]} for index, (task, first, second) in enumerate(p3a.P3A_TASK_ORDER, start=1)],
        "execution_contract": {"strict_serial": True, "request_ceiling_per_run": REQUEST_CEILING, "retry": 0, "fallback": False, "automatic_retry_rerun_or_continuation": False, "only_treatment_difference": "treatment", "future_paid_execution_requires_new_user_authorization": True},
        "frozen_identities": _identities(root), "runtime_source_sha256": _runtime_hashes(root),
        "dispatch_contract": {"unique_dispatch_identity": DISPATCH_IDENTITY, "workflow": str(WORKFLOW_PATH), "one_paid_job_only": "p3b-paid-execution", "second_dispatch": "fail_closed", "future_inputs": ["paid_execution", "expected_freeze_sha256", "expected_allocation_hash", "approved_parent_cap_cny", "authorization_acknowledgement", "dispatch_token", "run_id"]},
        "budget_proposal": {"currency": "CNY", "parent_cap_proposal_cny": str(PARENT_CAP), "child_cap_each_proposal_cny": str(CHILD_CAP), "child_count": 8, "spendable_total_cny": str(SPENDABLE_TOTAL), "safety_reserve_cny": str(SAFETY_RESERVE), "closure": "8 * child_cap_each + safety_reserve == parent_cap", "authorization": "proposal_only_not_paid_authorization"},
        "formal_stage_c_parent_authorization": {**authorization.model_dump(mode="json"), "status": "formal_proposal_not_authorized", "authorization_hash": authorization_hash(authorization)},
        "formal_stage_c_allocation": {**allocation.model_dump(mode="json"), "allocation_hash": allocation_hash(allocation), "status": "formal_proposal_not_authorized"},
        "formal_child_allocations": [item.model_dump(mode="json") for item in allocation.task_run_allocations],
        "paired_artifact_schema": paired_artifact_schema(), "p3_b": "blocked_pending_separate_explicit_paid_authorization",
    }


def validate_freeze(root: Path, frozen: Mapping[str, Any]) -> None:
    if dict(frozen) != freeze_payload(root):
        raise ValueError("P3-B freeze differs from its committed formal projection")
    if len(frozen["formal_child_allocations"]) != 8:
        raise ValueError("P3-B requires eight formal child allocations")
    if frozen["bound_main_commit"] != BOUND_MAIN_COMMIT:
        raise ValueError("P3-B must bind the merged formal main")


def _settle_zero_cost(ledger: BudgetLedger, allocation: StageCBudgetAllocation, run: Mapping[str, Any]) -> None:
    child = next(item for item in allocation.task_run_allocations if item.task_run_id == run["task_run_id"])
    ledger.allocation_hash = allocation_hash(allocation)
    ledger.settlements.append(Settlement(
        reservation_id=f"p3b-zero-{run['ordinal']:02d}", trial_id=f"swe/p3b/{run['task_run_id']}", stage="C",
        requests=0, input_tokens=0, output_tokens=0, reasoning_tokens=0, actual_cny=Decimal("0"),
        status="cancelled", settlement_method="provider_confirmed_not_submitted", usage_status="known",
        settled_at="p3b0-zero-provider-rehearsal", task_run_id=child.task_run_id,
        task_run_allocation_id=child.task_run_allocation_id, task_run_allocation_hash=child.task_run_allocation_hash,
    ))


def run_zero_provider_rehearsal(root: Path, frozen: Mapping[str, Any], artifact_root: Path) -> dict[str, Any]:
    """Run the future strict-serial shape through Agent and fake transport only."""
    validate_freeze(root, frozen)
    if artifact_root.exists():
        raise ValueError("refusing to overwrite P3-B rehearsal Artifact")
    artifact_root.mkdir(parents=True)
    authorization = BudgetAuthorization.model_validate({
        key: value for key, value in frozen["formal_stage_c_parent_authorization"].items()
        if key not in {"status", "authorization_hash"}
    })
    allocation = StageCBudgetAllocation.model_validate({
        key: value for key, value in frozen["formal_stage_c_allocation"].items()
        if key not in {"status", "allocation_hash"}
    })
    ledger = BudgetLedger(authorization_hash=authorization_hash(authorization), updated_at="p3b0-zero-provider-rehearsal")
    _write_json(artifact_root / "authorization.json", authorization.model_dump(mode="json"))
    _write_json(artifact_root / "stage-c-allocation.json", allocation.model_dump(mode="json"))
    guard = DispatchGuard(artifact_root / "dispatch-guard.json")
    token, run_id = "p3b0-readiness-token", "p3b0-readiness-run"
    guard.claim(dispatch_token=token, run_id=run_id)
    guard_results = []
    for next_token, next_run in ((token, run_id), ("p3b0-second-token", "p3b0-second-run")):
        try:
            guard.claim(dispatch_token=next_token, run_id=next_run)
        except ValueError as exc:
            guard_results.append(str(exc))
    tasks = {item["instance_id"]: item for item in full_replay.load_tasks(root)}
    records: list[dict[str, Any]] = []
    previous_ordinal = 0
    for run in frozen["task_runs"]:
        if run["ordinal"] != previous_ordinal + 1:
            raise ValueError("strict serial runner rejected out-of-order task-run")
        previous_ordinal = run["ordinal"]
        task_root = artifact_root / "runs" / f"{run['ordinal']:02d}-{run['treatment']}" / "tasks" / run["instance_id"]
        workspace = task_root / "workspace"
        p3a._synthetic_task_workspace(tasks[run["instance_id"]], workspace)
        source = workspace / "tracked.py"
        transport = p3a._RecordingFakeTransport(workspace, source)
        treatment = CapabilityV3Flag(run["treatment"])
        registry = create_default_registry(); registry.register(RunTest())
        checker = PermissionChecker(DangerousCommandDetector(), PathSandbox(str(workspace)), RuleEngine(), PermissionMode.DEFAULT, session_allow_all=True)
        agent = Agent(transport, registry, "openai-compat", work_dir=str(workspace), permission_checker=checker, max_iterations=8, capability_v3_config=CapabilityV3Config.from_flag(treatment), capability_v3_flag=treatment.value, capability_v3_artifact_root=task_root / "capability-v3", capability_v3_task_id=run["instance_id"], capability_v3_base_commit=run["base_commit"])
        conversation = ConversationManager(); conversation.add_user_message(str(tasks[run["instance_id"]]["problem_statement"]))
        events: list[str] = []
        async def consume() -> None:
            async for event in agent.run(conversation):
                events.append(type(event).__name__)
        asyncio.run(consume())
        patch = control_canary._goal3_extract_patch(workspace)
        _write_json(task_root / "agent-request-record.json", {"task_run_id": run["task_run_id"], "treatment": treatment.value, "fake_transport_calls": transport.calls, "assembled_requests": transport.request_records, "agent_event_types": events})
        (task_root / "candidate.patch").write_text(patch, encoding="utf-8")
        predictions = task_root / "predictions.json"
        _write_json(predictions, [{"instance_id": run["instance_id"], "model_name_or_path": "p3b-zero-provider-rehearsal", "model_patch": patch}])
        evaluator_id = f"p3b-rehearsal-{run['ordinal']:02d}"
        evaluator = full_replay._shadow_evaluator_runner(predictions_path=predictions, instance_ids=[run["instance_id"]], run_id=evaluator_id, cwd=task_root)
        report = task_root / "logs" / "run_evaluation" / evaluator_id / "p3b-zero-provider-rehearsal" / run["instance_id"] / "report.json"
        if evaluator.returncode != 0 or not report.is_file():
            raise RuntimeError("P3-B rehearsal evaluator did not emit raw report")
        shutil.copyfile(report, task_root / "official-report.json")
        fidelity = control_canary._validate_capability_v3_artifact(task_root=task_root, workspace=workspace, instance_id=run["instance_id"], treatment=treatment)
        if treatment is CapabilityV3Flag.V3_CORE and not fidelity["valid"]:
            raise RuntimeError("P3-B V3 activation Artifact is invalid")
        if treatment is CapabilityV3Flag.V2_CONTROL and (task_root / "capability-v3").exists():
            raise RuntimeError("P3-B V2_CONTROL carried V3 Advice/activation Artifact")
        _write_json(task_root / "task-result.json", {"task_run_id": run["task_run_id"], "terminal_status": "zero_provider_rehearsal_only", "provider_requests": 0, "usage": 0, "charge_cny": "0", "provider_secret_read": False})
        for transient in (".git", ".pytest_cache", "__pycache__", ".codepacex"):
            transient_path = workspace / transient
            if transient_path.exists(): shutil.rmtree(transient_path)
        _settle_zero_cost(ledger, allocation, run)
        records.append({**run, "artifact_path": str(task_root.relative_to(artifact_root)), "official_report_path": str((task_root / "official-report.json").relative_to(artifact_root)), "terminal_status": "zero_provider_rehearsal_only", "agent_dispatch_started": True, "recording_fake_transport_requests": transport.calls, "provider_requests": 0, "usage": 0, "charge_cny": "0", "provider_secret_read": False, "v3_advice_present": treatment is CapabilityV3Flag.V3_CORE, "v3_activation_schema_present": treatment is CapabilityV3Flag.V3_CORE, "treatment_fidelity": fidelity})
    _write_json(artifact_root / "ledger.json", ledger.model_dump(mode="json"))
    return {"executed": True, "runner": "p3b_post_merge_rebind.strict_serial_agent_dispatch", "transport": "recording_fake_llmclient_in_process", "paid_execution": False, "dispatch_identity": DISPATCH_IDENTITY, "dispatch_guard_rejections": guard_results, "provider_requests": 0, "usage": 0, "charge_cny": "0", "provider_secret_read": False, "run_records": records, "agent_dispatch_count": len(records), "recording_fake_transport_requests": sum(item["recording_fake_transport_requests"] for item in records), "ledger_settlement_count": len(ledger.settlements), "ledger_closed": ledger.active_reservation is None, "active_reservation": None, "allocation_hash": allocation_hash(allocation)}


def readiness_payload(root: Path, frozen: Mapping[str, Any], *, freeze_path: Path, rehearsal: Mapping[str, Any]) -> dict[str, Any]:
    validate_freeze(root, frozen)
    requirements = (rehearsal.get("executed") is True, rehearsal.get("ledger_closed") is True, rehearsal.get("active_reservation") is None, rehearsal.get("provider_requests") == 0, rehearsal.get("usage") == 0, rehearsal.get("charge_cny") == "0", rehearsal.get("provider_secret_read") is False, rehearsal.get("agent_dispatch_count") == 8, rehearsal.get("recording_fake_transport_requests") == 32, rehearsal.get("dispatch_guard_rejections") == ["duplicate dispatch rejected", "second dispatch rejected"])
    if not all(requirements):
        raise ValueError("P3-B readiness requires complete zero-provider rehearsal and dispatch protection")
    records = rehearsal.get("run_records")
    if not isinstance(records, list): raise ValueError("P3-B readiness lacks run records")
    for record in records:
        task_root = freeze_path.parent / REHEARSAL_DIRECTORY / record["artifact_path"]
        if not all((task_root / name).is_file() for name in paired_artifact_schema()["required_raw_artifacts"]):
            raise ValueError("P3-B readiness lacks raw Artifact")
        if record["treatment"] == "V2_CONTROL" and (record["v3_advice_present"] or record["v3_activation_schema_present"]):
            raise ValueError("V2_CONTROL carries V3 Advice")
        if record["treatment"] == "V3_CORE" and not record["treatment_fidelity"]["valid"]:
            raise ValueError("V3_CORE lacks activation schema")
    merged = merge_paired_results(frozen, records)
    ledger = BudgetLedger.model_validate_json((freeze_path.parent / REHEARSAL_DIRECTORY / "ledger.json").read_text(encoding="utf-8"))
    if ledger.active_reservation is not None or len(ledger.settlements) != 8 or ledger.spent_cny != 0:
        raise ValueError("P3-B zero-provider ledger is not closed at CNY zero")
    return {"schema_version": SCHEMA_VERSION, "status": "passed_zero_provider_readiness", "freeze_sha256": _sha256(freeze_path), "freeze_canonical_sha256": canonical_hash(frozen), "paid_job": "skipped", "provider_requests": 0, "usage": 0, "charge_cny": "0", "provider_secret_read": False, "secret_presence_check": "not_performed_in_local_rehearsal", "task_run_count": 8, "unique_task_run_count": 8, "paired_result_merge_count": len(merged), "rehearsal": dict(rehearsal), "p3_b": "blocked_pending_separate_explicit_paid_authorization"}


def write_artifacts(root: Path, output: Path) -> dict[str, str]:
    if output.exists(): raise ValueError("refusing to overwrite P3-B formal Artifact")
    frozen = freeze_payload(root); output.mkdir(parents=True)
    freeze_path = output / FREEZE_NAME; _write_json(freeze_path, frozen)
    rehearsal = run_zero_provider_rehearsal(root, frozen, output / REHEARSAL_DIRECTORY)
    readiness = readiness_payload(root, frozen, freeze_path=freeze_path, rehearsal=rehearsal)
    _write_json(output / MANIFEST_NAME, {"task_runs": frozen["task_runs"]})
    _write_json(output / TREATMENT_ORDER_NAME, {"treatment_order": frozen["treatment_order"]})
    _write_json(output / PARENT_NAME, frozen["formal_stage_c_parent_authorization"])
    _write_json(output / ALLOCATION_NAME, frozen["formal_stage_c_allocation"])
    _write_json(output / CHILDREN_NAME, {"formal_child_allocations": frozen["formal_child_allocations"]})
    _write_json(output / SCHEMA_NAME, frozen["paired_artifact_schema"])
    _write_json(output / READINESS_NAME, readiness)
    return {"freeze_sha256": _sha256(freeze_path), "freeze_canonical_sha256": canonical_hash(frozen), "allocation_hash": frozen["formal_stage_c_allocation"]["allocation_hash"], "readiness_sha256": _sha256(output / READINESS_NAME)}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P3-B0 zero-provider post-merge rebind")
    parser.add_argument("--root", type=Path, required=True); parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(write_artifacts(args.root.resolve(), args.output.resolve()), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

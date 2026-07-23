"""Zero-provider evaluator-only recovery for the frozen Stage D.1 Candidate.

This module never starts the Agent, creates a Provider client, or touches the
source Artifact.  It binds one preserved Candidate to the frozen official
SWE-bench evaluator and writes recovery evidence to a separate directory.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from evals.goal3_swe import _installed_evaluator_commit, collect_goal3_official_outcome
from evals.swe_bench_live import official_evaluator_report_path, run_official_evaluator
from evals.stage_d1_freeze import OFFICIAL_EVALUATOR_COMMIT


SOURCE_RUN_ID = "29941188060"
SOURCE_COMMIT = "52d2b5dab1ce3c3c7c787e0bec91910d7afd467d"
SOURCE_ARTIFACT_ID = "8538459144"
SOURCE_ARTIFACT_SHA256 = "73632767fb4d2f2ceb37b88466ca9d8d632b288d04b460d0c7f7eb981e75fbb2"
SOURCE_FREEZE_SHA256 = "063429849f64871adc737ff3a1f52aaa7a2212ae9fee9c49491f09866fc90e45"
SOURCE_RUNTIME_CONTRACT_HASH = "dc6302be4f0134bde5b60493939b577dd11d77587ae31f3ca0d3512fde118821"
SOURCE_PRICING_SHA256 = "a09eb6e6955b9fb68d3e011771c948f7a14b7bbca5316a2433cab099d0b643d3"
INSTANCE_ID = "beetbox__beets-5495"
MODEL_ID = "qwen3.7-max-2026-06-08"
VERIFIED_COST_CNY = "7.834968"
SOURCE_CANDIDATE_SHA256 = "2e276e38a21243345c2bfc0aeb7d6e0faf61124d6f8c4bc851c68aee5667a038"
_PREDICTION_NAME = hashlib.sha256(INSTANCE_ID.encode("utf-8")).hexdigest() + "-prediction.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_new(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        raise ValueError(f"refusing to overwrite evaluator recovery evidence: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _source_paths(source_root: Path) -> tuple[Path, Path, Path]:
    root = source_root.resolve()
    # The source run ID and its internal Run ID deliberately differ.  Keep the
    # latter explicit to prevent a broad search from accepting another Candidate.
    internal = root / "stage-d1-replacement-20260723-52d2b5d-29940818663"
    prediction = internal / _PREDICTION_NAME
    return prediction, root / "terminal-ledger.json", internal / "validation-events.jsonl"


def audit_source_candidate(source_root: Path) -> dict[str, Any]:
    """Read and bind the one preserved Candidate without mutating its Artifact."""
    prediction_path, ledger_path, validation_path = _source_paths(source_root)
    manifest_path = source_root / "stage-d1-replacement-20260723-52d2b5d-29940818663" / "manifest.json"
    binding_path = source_root / "canary-authorization.json"
    authorization_path = source_root / "budget-authorization.json"
    if not all(path.is_file() for path in (prediction_path, ledger_path, validation_path, manifest_path, binding_path, authorization_path)):
        raise ValueError("source Artifact is missing required immutable Candidate evidence")
    predictions = json.loads(prediction_path.read_text(encoding="utf-8"))
    if not isinstance(predictions, list) or len(predictions) != 1 or not isinstance(predictions[0], dict):
        raise ValueError("source Artifact must contain exactly one Candidate prediction")
    prediction = predictions[0]
    patch = prediction.get("model_patch")
    if prediction.get("instance_id") != INSTANCE_ID or prediction.get("model_name_or_path") != MODEL_ID:
        raise ValueError("source Candidate identity differs from Stage D.1")
    if not isinstance(patch, str) or not patch.strip():
        raise ValueError("source Candidate is empty")
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    if ledger.get("active_reservation") is not None:
        raise ValueError("source ledger has an active reservation")
    if len(ledger.get("request_charges", [])) != 40 or len(ledger.get("settlements", [])) != 40:
        raise ValueError("source ledger does not bind the preserved 40-request attempt")
    if str(ledger.get("spent_cny")) != VERIFIED_COST_CNY:
        raise ValueError("source ledger cost differs from the preserved Candidate attempt")
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    required_binding = {
        "approved_commit": SOURCE_COMMIT,
        "freeze_sha256": SOURCE_FREEZE_SHA256,
        "runtime_contract_hash": SOURCE_RUNTIME_CONTRACT_HASH,
        "pricing_snapshot_hash": SOURCE_PRICING_SHA256,
        "budget_stage_key": "STAGE_D1_CANARY",
    }
    if any(binding.get(key) != value for key, value in required_binding.items()) or binding.get("task_ids") != [INSTANCE_ID]:
        raise ValueError("source Candidate binding differs from the frozen Stage D.1 identity")
    authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
    if authorization.get("experiment_commit") != SOURCE_COMMIT or authorization.get("pricing_snapshot_hash") != SOURCE_PRICING_SHA256:
        raise ValueError("source budget authorization differs from the frozen Stage D.1 identity")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("git_commit") != SOURCE_COMMIT:
        raise ValueError("source Candidate environment differs from the frozen commit")

    tool_counts = {"EditFile": 0, "WriteFile": 0, "RunTest": 0}
    run_test_results: list[dict[str, Any]] = []
    for line in (source_root / "stage-d1-replacement-20260723-52d2b5d-29940818663" / "artifacts" / f"{hashlib.sha256(INSTANCE_ID.encode()).hexdigest()}-stdout.txt").read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "tool_result" and event.get("tool_name") in tool_counts:
            tool_counts[str(event["tool_name"])] += 1
            if event["tool_name"] == "RunTest":
                run_test_results.append({"is_error": bool(event.get("is_error")), "output": str(event.get("output", ""))[:500]})

    declarations: list[dict[str, Any]] = []
    checkpoints: list[dict[str, Any]] = []
    completion: list[dict[str, Any]] = []
    for line in validation_path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("event_type") == "validation_declaration":
            declarations.append(dict(event.get("payload", {})))
        elif event.get("event_type") == "validation_checkpoint":
            checkpoints.append(dict(event.get("payload", {})))
        elif event.get("event_type") == "validation_blocked" and event.get("payload", {}).get("status") == "INVALID_COMPLETION_ATTEMPT":
            completion.append(dict(event["payload"]))

    candidate_sha = hashlib.sha256(patch.encode("utf-8")).hexdigest()
    if candidate_sha != SOURCE_CANDIDATE_SHA256:
        raise ValueError("source Candidate SHA-256 differs from the authorized recovery identity")
    return {
        "schema_version": 1,
        "source_run_id": SOURCE_RUN_ID,
        "source_commit": SOURCE_COMMIT,
        "source_artifact_id": SOURCE_ARTIFACT_ID,
        "source_artifact_sha256": SOURCE_ARTIFACT_SHA256,
        "source_freeze_sha256": SOURCE_FREEZE_SHA256,
        "source_runtime_contract_hash": SOURCE_RUNTIME_CONTRACT_HASH,
        "source_pricing_sha256": SOURCE_PRICING_SHA256,
        "instance_id": INSTANCE_ID,
        "candidate_reference": str(prediction_path.relative_to(source_root)),
        "candidate_file_sha256": _sha256(prediction_path),
        "candidate_sha256": candidate_sha,
        # The source runner extracts the final git diff once and writes those
        # exact bytes to model_patch.  The Artifact does not retain a second
        # workspace-diff file, so this is a semantic identity, not two blobs.
        "workspace_diff_sha256": candidate_sha,
        "candidate_matches_workspace_diff": True,
        "workspace_diff_evidence": "source runner exports the exact extracted workspace diff as model_patch",
        "candidate_nonempty": True,
        "tool_counts": tool_counts,
        "run_test_results": run_test_results,
        "reproduction_declarations": declarations,
        "checkpoints": checkpoints,
        "completion_gate": completion,
        "provider_requests": 40,
        "usage": 40,
        "settlements": 40,
        "verified_cost_cny": VERIFIED_COST_CNY,
        "active_reservation": None,
    }


def _installed_evaluator() -> dict[str, Any]:
    try:
        module = importlib.util.find_spec("swebench.harness.run_evaluation")
    except ModuleNotFoundError:
        module = None
    commit = _installed_evaluator_commit(str(module.origin) if module and module.origin else None)
    docker = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], text=True, capture_output=True, check=False)
    result = {
        "docker_available": docker.returncode == 0,
        "docker_server_version": docker.stdout.strip() if docker.returncode == 0 else None,
        "official_evaluator_module_available": module is not None,
        "installed_evaluator_commit": commit,
        "expected_evaluator_commit": OFFICIAL_EVALUATOR_COMMIT,
        "evaluator_commit_matches": commit == OFFICIAL_EVALUATOR_COMMIT,
    }
    if not all((result["docker_available"], result["official_evaluator_module_available"], result["evaluator_commit_matches"])):
        raise ValueError("frozen official evaluator or Docker is unavailable")
    return result


def _evaluate(*, prediction_path: Path, output_root: Path, run_id: str, model_id: str) -> tuple[Path, bool, subprocess.CompletedProcess[str]]:
    result = run_official_evaluator(
        dataset_name="SWE-bench-Live/SWE-bench-Live", split="lite", predictions_path=prediction_path,
        instance_ids=[INSTANCE_ID], max_workers=1, run_id=run_id, namespace="starryzhang",
        cwd=output_root, evaluator_architecture="native",
    )
    (output_root / "evaluator-stdout.txt").write_text(result.stdout or "", encoding="utf-8")
    (output_root / "evaluator-stderr.txt").write_text(result.stderr or "", encoding="utf-8")
    if result.returncode:
        raise ValueError(f"official evaluator failed with exit status {result.returncode}")
    report = official_evaluator_report_path(cwd=output_root, run_id=run_id, model_id=model_id, instance_id=INSTANCE_ID)
    return report, collect_goal3_official_outcome(report, INSTANCE_ID), result


def evaluator_smoke(*, output_root: Path, run_id: str) -> dict[str, Any]:
    """Run the frozen evaluator on a fixed no-op prediction, never a Provider call."""
    if not run_id or Path(run_id).name != run_id:
        raise ValueError("invalid evaluator smoke run ID")
    output_root = output_root.resolve()
    if output_root.exists():
        raise ValueError("refusing to overwrite evaluator smoke evidence")
    output_root.mkdir(parents=True)
    installed = _installed_evaluator()
    prediction = output_root / "smoke-prediction.json"
    # A fixed, non-gold no-op patch exercises Docker, evaluator invocation,
    # report location, and unresolved parsing without using a model output.
    prediction.write_text(json.dumps([{
        "instance_id": INSTANCE_ID, "model_name_or_path": "stage-d1-evaluator-smoke",
        "model_patch": "diff --git a/.stage-d1-smoke b/.stage-d1-smoke\nnew file mode 100644\nindex 0000000..e69de29\n",
    }]) + "\n", encoding="utf-8")
    report, resolved, _ = _evaluate(prediction_path=prediction, output_root=output_root, run_id=run_id, model_id="stage-d1-evaluator-smoke")
    result = {"schema_version": 1, "kind": "zero_provider_evaluator_smoke", **installed,
              "report_reference": str(report.relative_to(output_root)), "report_sha256": _sha256(report),
              "resolved": resolved, "provider_requests": 0, "usage": 0, "charge": "0", "settlement": "0",
              "active_reservation": None}
    _write_new(output_root / "smoke-result.json", result)
    return result


def recover(*, source_root: Path, evidence_root: Path, run_id: str) -> dict[str, Any]:
    """Evaluate the preserved Candidate exactly once, in a new evidence root."""
    if not run_id or Path(run_id).name != run_id:
        raise ValueError("invalid evaluator recovery run ID")
    evidence_root = evidence_root.resolve()
    if evidence_root.exists():
        raise ValueError("refusing to overwrite evaluator recovery evidence")
    audit = audit_source_candidate(source_root)
    evidence_root.mkdir(parents=True)
    _write_new(evidence_root / "candidate-audit.json", audit)
    source_prediction = source_root.resolve() / audit["candidate_reference"]
    recovery_prediction = evidence_root / "preserved-candidate.json"
    recovery_prediction.write_bytes(source_prediction.read_bytes())
    installed = _installed_evaluator()
    report, resolved, _ = _evaluate(prediction_path=recovery_prediction, output_root=evidence_root, run_id=run_id, model_id=MODEL_ID)
    report_copy = evidence_root / "official-report.json"
    report_copy.write_bytes(report.read_bytes())
    result = {
        "schema_version": 1, "kind": "stage_d1_evaluator_only_recovery", "recovered_at": _utc_now(),
        "source": audit, "candidate_reference": recovery_prediction.name,
        "recovery_identity": {
            "source_run_id": SOURCE_RUN_ID,
            "source_artifact_id": SOURCE_ARTIFACT_ID,
            "source_artifact_sha256": SOURCE_ARTIFACT_SHA256,
            "source_commit": SOURCE_COMMIT,
            "source_freeze_sha256": SOURCE_FREEZE_SHA256,
            "evaluator_commit": OFFICIAL_EVALUATOR_COMMIT,
            "candidate_sha256": audit["candidate_sha256"],
        },
        "candidate_sha256": audit["candidate_sha256"], "workspace_diff_sha256": audit["workspace_diff_sha256"],
        "candidate_matches_workspace_diff": audit["candidate_matches_workspace_diff"], **installed,
        "evaluator_report_reference": report_copy.name, "evaluator_report_sha256": _sha256(report_copy),
        "outcome": "resolved" if resolved else "unresolved", "provider_requests_added": 0,
        "usage_added": 0, "charge_added": "0", "settlement_added": 0, "active_reservation": None,
    }
    _write_new(evidence_root / "recovery-report.json", result)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage D.1 evaluator-only recovery")
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit"); audit.add_argument("--source-root", type=Path, required=True)
    smoke = sub.add_parser("smoke"); smoke.add_argument("--evidence-root", type=Path, required=True); smoke.add_argument("--run-id", required=True)
    recovery = sub.add_parser("recover"); recovery.add_argument("--source-root", type=Path, required=True); recovery.add_argument("--evidence-root", type=Path, required=True); recovery.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)
    if args.command == "audit":
        result = audit_source_candidate(args.source_root)
    elif args.command == "smoke":
        result = evaluator_smoke(output_root=args.evidence_root, run_id=args.run_id)
    else:
        result = recover(source_root=args.source_root, evidence_root=args.evidence_root, run_id=args.run_id)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

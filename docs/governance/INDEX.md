# CodePaceX Governance and Evidence Index

This is the stable entry point for public project status, evaluation evidence,
experiment contracts, and evidence-governance rules. Historical versioned
indexes, snapshots, AI handoffs, private prompts, resume material, and internal
execution plans are kept in Git history or the ignored local workspace rather
than treated as current public navigation.

## Current project

- [Current status](CURRENT_STATUS.md)
- [Evidence governance](EVIDENCE_GOVERNANCE.md)
- [Experiment index](EXPERIMENTS.md)
- [Engineering history](archive/ENGINEERING_HISTORY.md)
- [Evaluation README](../../evals/README.md)
- [Evaluation history ledger](../../evals/EVALUATION_HISTORY.md)
- [Evaluation artifact index](../../evals/EVALUATION_ARTIFACT_INDEX.md)

## Formal evaluation tracks

| Track | Stable entry | Scope |
| --- | --- | --- |
| Lightweight six-task Eval | [evals README](../../evals/README.md) | Deterministic local regression baseline |
| Goal 2 | [Final report](../../evals/GOAL2_FINAL_REPORT.md) | MCP, Permission, retention, Multi-Agent and formal-SWE evidence boundaries |
| Goal 3 | [Evaluation history](../../evals/EVALUATION_HISTORY.md) | Official evaluator controls and three-task Pilot |
| Goal 4 | [Final report](../../evals/GOAL4_FINAL_REPORT.md) | Pre-registered 20-task Python-only SWE-bench-Live Lite subset |
| Capability V3 | [Final report](../../evals/CAPABILITY_V3_GOAL4_FINAL_REPORT.md) | Two-run infrastructure-recovery completion and activation postmortem |
| Evaluation V2 | [V2 design](../../evals/evaluation_v2/DESIGN.md) | Zero-provider harness and replay contracts |
| Stage B | [Stage B report](../../evals/STAGE_B_REPORT.md) | Validation-gate implementation and offline verification |
| Stage C | [Stage C freeze](../../evals/STAGE_C_FREEZE_REPORT.md) | Zero-provider freeze; no paid Stage C Trial |
| Stage D | [Stage D freeze](../../evals/STAGE_D_FREEZE_REPORT.md) | Zero-provider protocol canary freeze |
| P3-A | [Paired-pilot inputs](../../evals/evaluation_v2/p3a_paired_pilot/) | Frozen paired-pilot manifests and zero-provider rehearsal |
| P3-B | [Post-merge rebind inputs](../../evals/evaluation_v2/p3b_post_merge_rebind/) | Fail-closed paid-entry contract and zero-provider rehearsal |

## Evidence and raw inputs

- Human-readable reports remain under `evals/` and its `evaluation_v2/`
  subdirectories.
- Machine-readable task contracts, freeze manifests, safe payloads, ledgers,
  task-run records, and Artifact-shaped outputs remain at their existing
  `evals/` paths. Their paths and hashes are not rewritten by this cleanup.
- Formal governance-side V3 comparison evidence remains in
  [`CodePaceX_Goal4_vs_V3_20题逐题结果_20260730.csv`](CodePaceX_Goal4_vs_V3_20题逐题结果_20260730.csv),
  [`CodePaceX_Goal4_vs_V3_结果与机制激活审计_20260730.md`](CodePaceX_Goal4_vs_V3_结果与机制激活审计_20260730.md),
  and [`CodePaceX_V3_机制激活审计_20260730.csv`](CodePaceX_V3_机制激活审计_20260730.csv).

## Local and historical boundary

GPT/Codex context migration, development prompts, private plans, resume and
interview material live only in the ignored `_local/` tree. Selected historical
engineering notes live in [`archive/`](archive/). Neither location is a source
of current formal evaluation claims.

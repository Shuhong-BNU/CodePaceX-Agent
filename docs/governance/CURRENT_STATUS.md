# CodePaceX Current Status

Last consolidated: 2026-08-15. This document is the single current-state
summary. Historical snapshots and execution plans are not current status.

## Repository and product

- The public current branch is `main`. The repository-boundary and governance
  consolidation was merged through PR #88 on 2026-08-15. Its pre-cleanup base
  was `2be5380d9007cb227d0e722fca20cf54b80a0c9c`.
- Current repository state should be read from `main`; this document records
  project and evaluation status rather than pinning every subsequent
  documentation commit SHA.
- CodePaceX is a terminal Coding Agent with tool registry, permission checks,
  context and memory handling, skills, sub-agents/teams, MCP, worktree support,
  and an evaluation harness. Runtime prompts under `codepacex/` are product
  assets and remain public.
- The deterministic Lightweight Agent Eval baseline is complete: `6/6 PASS`,
  `0 FAIL`, `0 ERROR`, `0 WARNING`. Its original run identity, model, cost and
  evaluated commit are not preserved in this repository; do not add those
  fields to a claim.

## Formal study status

| Study | Current state | What is supported |
| --- | --- | --- |
| Goal 2 | Closed with bounded results and insufficiency boundaries | MCP accounting/evidence boundary, Permission terminal matrix, auditable-partial Retention, evidence-insufficient Multi-Agent, infrastructure-blocked formal SWE, deferred eight-hour Sessions. See [final report](../../evals/GOAL2_FINAL_REPORT.md). |
| Goal 3 SWE | Completed diagnostic/official-evaluator Pilot | `3/3` scorable, `1 resolved / 2 unresolved`, CNY `9.078540`; Pilot only, not a broad SWE claim. See [history](../../evals/EVALUATION_HISTORY.md). |
| Goal 4 | `GOAL4_ACCEPTED` | `20/20` scorable, `4 resolved / 16 unresolved`; the final Artifact and matrix are the immutable source. See [final report](../../evals/GOAL4_FINAL_REPORT.md). |
| Capability V3.0 | Formal closeout | Two-run infrastructure-recovery completion: `20 terminal`, `19 scorable`, `5 resolved`, `14 unresolved`, `1 infrastructure_error`; Path A retains `bridgecrewio__checkov-6893` as infrastructure and does not immediately retry it. See [report](../../evals/CAPABILITY_V3_GOAL4_FINAL_REPORT.md) and [postmortem](../../evals/CAPABILITY_V3_ACTIVATION_POSTMORTEM.md). |
| Stage B | Implemented and offline-verified | Validation-gate implementation; `1230 passed, 2 skipped` in the recorded full offline suite; no Provider Trial. See [report](../../evals/STAGE_B_REPORT.md). |
| Stage C | Freeze only | Zero-provider freeze; `0` Provider requests and `0` Stage C Trials. Any paid phase requires a new immutable authorization. See [freeze](../../evals/STAGE_C_FREEZE_REPORT.md). |
| Stage D | Protocol-canary freeze only | Zero-provider deterministic freeze; no paid dispatch and no Stage C claim changes. See [freeze](../../evals/STAGE_D_FREEZE_REPORT.md). |
| Evaluation V2 | Design/readiness and zero-provider lanes | Base/control/replay contracts are checked in; no paid V2 score is established by these files. See [design](../../evals/evaluation_v2/DESIGN.md) and [full replay contract](../../evals/evaluation_v2/FULL_20_REPLAY.md). |
| P3-A | Frozen paired-pilot inputs and rehearsal | The repository preserves the eight-run manifest, allocation, authorization draft, paired schema and zero-provider rehearsal; these are not a paid result. See [`evals/evaluation_v2/p3a_paired_pilot/`](../../evals/evaluation_v2/p3a_paired_pilot/). |
| P3-B | Blocked before a new paid result | The merged fail-closed entrypoint, canonical identity/bundle checks, A4 failure and zero-provider readiness are preserved; A4 produced `0 / 0 / CNY 0` Provider requests/Usage/charge, A5 has not run, and P4 remains gated. See [`evals/evaluation_v2/p3b_post_merge_rebind/`](../../evals/evaluation_v2/p3b_post_merge_rebind/) and the P3-B evidence notes linked from [Experiments](EXPERIMENTS.md). |

## Claim boundaries

- A formal result must identify its scope, frozen input, code/evaluator identity,
  run or Artifact identity, accounting state and limitations.
- `0` Provider requests, zero-provider readiness, CI, dry-runs and pytest are
  engineering evidence, not paid model-performance results.
- `insufficient-data`, `infrastructure_error`, `not_run`, `deferred` and
  `request_ceiling_reached` remain explicit terminal boundaries. They must not
  be silently converted into success, failure, or an inferred score.
- Goal 4 and Capability V3 use the same historical 20-task family; neither is
  a full SWE-bench-Live Lite leaderboard result, pass@k, significance test or
  generalization proof.

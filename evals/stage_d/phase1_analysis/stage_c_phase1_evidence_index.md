# Stage C Phase 1 Evidence Index

## Immutable Inputs

| Item | Identity |
| --- | --- |
| Final Phase 1 run | `29918131993` |
| Final Artifact | `8529432956` |
| Artifact archive SHA-256 | `66f8aee8d38964b3e6a74b4d3b01498fb206a7e76b5e31e2873c699ead344d0e` |
| Commit | `f82a1b6b89fde612b9db61d684a7549ae3fc5ffd` |
| Bundle run / Artifact | `29918015071` / `8528702966` |
| tasks.jsonl SHA-256 | `71a31d577daea9e8653de59b95464ecde3e7f2d680dd00f38d617a08ca2695ab` |
| Final ledger SHA-256 | `fa91fa40e6d01b873c9fd794bd756557c24e65b49d1cd3e638384c3d55a3241d` |
| Final report SHA-256 | `5a69e3c9999ac0e9dcc5072512ae31aa4781e86e01ff550a456cd2ffa435760a` |

## Read Artifacts

- Final extracted Artifact: `/private/tmp/codepacex-stage-c-phase1-29918131993/`.
- First-candidate Artifact: `/private/tmp/codepacex-stage-c-phase1-evidence-29896662738/`.
- Continuation and preserved partial Artifact: `/private/tmp/codepacex-stage-c-phase1-evidence-29900372678/`.
- Goal 4 immutable archive: `/private/tmp/goal4-artifact-8496125148.zip`, extracted for read-only comparison at `/private/tmp/codepacex-goal4-artifact-8496125148/`.
- Repository evidence: `evals/EVALUATION_ARTIFACT_INDEX.md`, `evals/EVALUATION_HISTORY.md`, `evals/GOAL4_FAILURE_ANALYSIS.md`, `evals/goal4_failure_taxonomy.csv`, `evals/STAGE_B_REPORT.md`, `evals/STAGE_B_DESIGN.md`, `codepacex/validation.py`, and `codepacex/tools/validation_checkpoint.py`.

## Per-task Evidence

For each final task, the analysis read stdout trace, prediction, task manifest, evaluator report, shared `usage.json`, `terminal-ledger.json`, `permission-events.jsonl`, and `validation-events.jsonl`. The first two task records are read from preserved predecessor Artifacts and bound into final `recovery-evidence.json`. The final run reads replacement records for tasks 3-6.

## Boundaries

- Provider requests performed by this analysis: `0`.
- Workflow dispatches: `0`.
- Evaluator reruns: `0`.
- Gold patches read: `false`.
- Historical evidence modified: `false`.
- No new Claim or Phase 2 result was created.

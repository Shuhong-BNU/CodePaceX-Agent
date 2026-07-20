# Goal 4 Runbook

Goal 4 evaluates one pre-registered 20-task Python-only SWE-bench-Live Lite subset. It is independent from the closed Goal 2 and accepted Goal 3 evidence.

## Frozen Contract

- Provider/model/protocol: `bailian-qwen37-max` / `qwen3.7-max-2026-06-08` / `openai-compat`.
- Dataset revision: `a637bd46829f3132e12938c8a0ca93173a977b8e`.
- Official evaluator: `ad79b850f15e33992e96f03f6e97f05ddf9aa0be` on native Linux x86_64/amd64.
- Generation: thinking enabled, reasoning ceiling 6144, `max_completion_tokens=8192`, no `max_tokens`, input/completion ceilings 128000/8192, fallback disabled, retry 0, and strict serial execution.
- Matrix: 8 one-file, 8 two-to-four-file, 4 five-plus-file tasks, excluding Goal 3 Pilot IDs and limiting repositories to two tasks.
- Batches: A has 2/2/1 tasks; B has 6/6/3 tasks. Both are frozen before Batch A starts.

The source dataset may contain Gold patches only while selecting the matrix and running evaluator-only controls. The formal bundle contains only Agent-visible fields.

## Accounting

Goal 4 uses a parent CNY `1684.439040` authorization and two independent child allocations:

| Batch | Execution ceiling | Safety reserve | Authorization ceiling |
| --- | ---: | ---: | ---: |
| A | 366.182400 | 54.927360 | 421.109760 |
| B | 1098.547200 | 164.782080 | 1263.329280 |

Each Provider request is reserved, charged and settled independently. The child allocations cannot transfer unused capacity. A missing Usage record, contract violation, active reservation, duplicate paid attempt, invalid evaluator report, or budget block stops all remaining paid work.

## Commands

The GitHub workflows are the authorized native execution path:

1. `goal4-swe-freeze.yml` installs the frozen evaluator, materializes the exact revision, freezes the matrix, and runs empty/gold controls.
2. `goal4-swe-paid-formal.yml` runs zero-provider accounting validation, Batch A, integrity checks, Batch B, and evidence finalization.

The CLI intentionally separates the states:

```bash
python -m evals.goal4_swe validate
python -m evals.goal4_swe freeze-formal ...
python -m evals.goal4_swe prepare-paid-artifacts ...
python -m evals.goal4_swe zero-provider ...
python -m evals.goal4_swe execute-batch --confirm-paid-run --batch A ...
python -m evals.goal4_swe execute-batch --confirm-paid-run --batch B ...
python -m evals.goal4_swe finalize ...
```

`evaluator-recovery` is allowed only for an immutable persisted prediction whose failure is solely evaluator infrastructure. It never calls the Provider.

## Claims Boundary

`GOAL4_ACCEPTED` requires 20 unique scorable terminal results, complete Usage/charges/settlements, no unresolved error state, no active reservation, and a reproducible final report and Claims file. Resolved count has no pre-set target.

The result is not a full Lite split or SWE-bench-Live leaderboard result, pass@k, a comparison against another Agent, a statistically significant result, or a general production success claim. Goal 3 Pilot outcomes are never included in the 20-task denominator.

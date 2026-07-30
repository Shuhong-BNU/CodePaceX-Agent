# CodePaceX current snapshot: P1/P2 completed, P3 pending

> **Document version**: v1.0.0  
> **Recorded**: 2026-07-30 21:24（UTC+8）  
> **Status**: Current  
> **Supersedes for current-state navigation**: `CURRENT_SNAPSHOT_P1P2_V31_ACTIVATION_20260730.md`  
> **Historical rule**: the prior snapshot remains unchanged because its “PR CI pending” field accurately records the pre-merge state.

```yaml
repository: Shuhong-BNU/CodePaceX-Agent
remote_main: 9e076874894ccf155d990fa8a176b2191e258652
p0:
  status: Completed
  pr: 72
  merge_commit: ce15722d2154d95a2dfe5abb659b220c2421f4cb
p1_p2:
  status: Completed
  pr: 73
  head_commit: e281b9fa5a7123f341865dc7293055af0073710a
  merge_commit: 9e076874894ccf155d990fa8a176b2191e258652
  agent_release_label: CPX-Agent v3.1.0 — Activation Fidelity
  release_label_status: logical label; no Git tag or GitHub Release
  activation_ladder:
    L1_constructed: passed
    L2_materialized: passed
    L3_injected: passed
    L4_behavior_and_capability: not tested
  provider_requests: 0
  provider_usage: 0
  provider_charge_cny: 0
  secret_read: false
  paid_jobs: skipped
p3:
  status: Not started
  next_step: P3-A contract freeze and zero-provider readiness
  paid_authorization: none
checkov_6893:
  decision: Path A
  retry: false
local_original_main:
  status: user-modified
  divergence: ahead 1, behind 42
  instruction: do not reset, stash, clean, rebase, sync, or overwrite
```

## Completed in P1/P2

- Repository Evidence removes generic wrappers and excludes virtualenv, `site-packages`, build, and cache paths.
- `ReadFile`, `Grep`, and `Glob` results can flow back into the existing V3 Evidence packet.
- Bounded advice is inserted into both real Agent request-assembly paths before transport.
- Contract-heavy cases can create bounded hypotheses and a contract matrix.
- Actual `RunTest` results drive baseline/post differential state.
- Candidate status can progress from C1 to C2/C3 using test evidence.
- Six deterministic zero-provider fixtures passed.
- Historical Goal 4 preserved-task identity replay recorded 20/20 `repo@base_commit` and named-entity anchors without recomputing resolved rate.
- CI passed on macOS, Ubuntu, and all zero-provider readiness jobs.
- P1/P2 does **not** prove L4 behavior improvement, resolved-rate improvement, or generalization.

## Current gate

The project may now prepare P3, but it may not dispatch a paid Pilot until:

1. the 4-task × 2-treatment contract is frozen;
2. treatment order, model, Prompt, evaluator, request ceiling, retry/fallback, pricing, and budget are frozen;
3. all eight task-run identities and allocations pass zero-provider readiness;
4. the paid hard cap is proposed from frozen pricing and historical evidence;
5. the user provides a new explicit paid authorization.

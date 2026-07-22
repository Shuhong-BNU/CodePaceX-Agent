# Stage C paid execution

This document describes execution capability only. It does not authorize a
Stage C Trial and it does not change the immutable Freeze in
[STAGE_C_CHARTER.md](STAGE_C_CHARTER.md).

`evals/stage_c_paid.py` is the only Stage C paid-runner entrypoint. It keeps the
Freeze module zero-provider and requires a clean checkout of an approved
40-character commit, matching Freeze and pricing hashes, an explicit non-secret
phase authorization identity, an Agent-safe task bundle, a present Provider
secret, and `--confirm-paid-run` before a real Agent subprocess can start.

The task bundle is exactly the pre-registered phase order and may contain only
`instance_id`, repository/base-commit metadata, and the current task's problem
statement. It rejects gold patches, Goal 4 outcome/cost data, failure taxonomy,
recommendations, evaluator reports, and other task traces. The only treatment
profile is the frozen `validation_mode=stage_b` profile and its profile/runtime
hashes are bound into both the authorization and Run manifest.

The zero-provider `build-task-bundle` command converts only the immutable
formal Dataset into a fresh `tasks.jsonl`. Its schema is exactly seven fields:
the four core fields are non-empty strings; `platform`, `version`, and
`environment_setup_commit` retain their source string-or-`null` semantics.
It never replaces `null` with synthetic metadata, and the generated file is
reloaded through `load_agent_task_bundle()` before a workflow can upload it.

## Budget and terminal evidence

Every actual Provider request uses the existing `ProviderRequestBudget` bridge:
it makes exactly one rolling reservation from the frozen pricing and token
limits, sends transport only after that succeeds, preserves raw Provider Usage,
settles its charge, and clears `active_reservation`. The maximum reservation is
`CNY 1.830912`; Phase 1 is hard-capped at `CNY 80`, and Phase 2 is capped at
`CNY 250 - Phase 1 combined conservative consumption`. The retained accounting
schema has a one-micro-CNY safety-envelope field, while its allocation's
`spendable_total_cny` is the exact user cap enforced before transport.

The frozen 40-request ceiling and 20/30/36 Stage B checkpoints apply unchanged.
Request 41 is refused by the existing bridge before reservation or transport.
Budget exhaustion is recorded as `budget_blocked`, stops the phase, and leaves
unstarted tasks as `not_run`; it is never counted as evaluator `unresolved`.
Infrastructure errors also stop the phase without retry or a second candidate.

Each task writes a manifest, prediction (or a durable absence marker), stdout,
trace-derived validation evidence, raw Usage/charge/settlement ledger links,
secret-scan result, and the official evaluator report when scorable. Only six
scorable Phase 1 terminals form the smoke result. Only all twenty scorable
terminals form the full paired Claim.

## Workflows

[`stage-c-smoke-paid.yml`](../.github/workflows/stage-c-smoke-paid.yml) and
[`stage-c-continuation-paid.yml`](../.github/workflows/stage-c-continuation-paid.yml)
are `workflow_dispatch` only and default `paid_execution=false`. Their false
path performs only zero-provider validation. Their true paths require separate
authorization input and download immutable Agent-safe inputs; the continuation
also verifies the Phase 1 Artifact manifest, Artifact ID/archive digest,
report/ledger hashes, all six scorable terminals, and exact 6/14 task binding.
Neither workflow has schedule, push, or pull-request triggers, and Phase 1
never starts Phase 2 automatically.

[`stage-c-task-bundle.yml`](../.github/workflows/stage-c-task-bundle.yml) is a
separate dispatch-only, zero-provider preparation workflow. It checks out an
explicit immutable commit, verifies the fixed Goal 4 source Artifact digest,
and uploads only a six-row `tasks.jsonl` plus a no-statement manifest/hash. It
does not initialize a Provider, run an Agent or evaluator, or create a Stage C
Trial.

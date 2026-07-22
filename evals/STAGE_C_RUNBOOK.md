# Stage C Freeze Runbook

## Freeze Verification

The committed bundle is generated without Provider access:

```bash
python -m evals.stage_c validate --output-dir evals/stage_c
python -m evals.stage_c dry-run --root . --phase phase_1 --output-dir /tmp/stage-c-smoke
python -m evals.stage_c dry-run --root . --phase phase_2 --output-dir /tmp/stage-c-continuation
```

Each dry-run records `provider_requests=0`, `paid_execution=false`, and
`formal_stage_c_trial=false`. It is not a Trial or a formal experiment.

## Future Phase 1

Before any paid invocation, a human must create a Phase 1 authorization bound to
an immutable approved commit, the frozen pricing hash, and the CNY 80 cap. The
executor must use the existing `ProviderRequestBudget` bridge: reserve one next
request, send only after reservation succeeds, preserve raw Usage, settle, and
require `active_reservation=null` before another request or task. It must stop
the phase on a budget block, missing Usage, usage-contract violation, missing
terminal evidence, or request 41 attempt.

The first six registered instance IDs are the exact Goal 4 prefix. No scoring,
selection, prompt, profile, tool, fallback, retry, task payload, repository base,
or evaluator change is permitted while the phase is running.

## Future Phase 2

Phase 2 is inert until a separate human authorization verifies the Phase 1
Artifact ID, archive/report hashes, ledger terminal state, `active_reservation`
null, Phase 1 exact set, Phase 2 exact set, and CNY 250 minus Phase 1 conservative
consumption. It may not infer permission from Phase 1 success, failure, or score.

## Workflows

`stage-c-smoke.yml` and `stage-c-continuation.yml` are dispatch-only Freeze
workflows. Their default `paid_execution=false` path performs only deterministic
validation and a zero-provider dry-run. They contain no credential, no Provider
transport, and no paid fallback path. A later paid workflow must be a separately
reviewed change and checkout an immutable human-approved commit rather than
moving `main`.

## Reporting

Use the templates under `evals/stage_c/`. A partially completed phase reports its
actual terminal/scorable counts and leaves remaining tasks `not_run`. It cannot
emit a complete smoke Claim or a full twenty-task paired Claim. Every report must
state diagnostic-set reuse, historical rather than concurrent control, and
Provider/time drift as attribution limits.

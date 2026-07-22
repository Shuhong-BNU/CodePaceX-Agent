# Stage D Unblocker Charter

## Scope

Stage D is a separate protocol-unblocker effort following Stage C Phase 1's
`PHASE_2_NO_GO`. It addresses the Stage B live-tool deadlock with a typed
`ValidationCheckpoint`, a bounded `RunTest` path, actionable remediation, and
verified PermissionManager propagation. It neither reruns nor changes Stage C.

The only future paid scope frozen here is a strict-serial, two-task protocol
canary: `beetbox__beets-5495` and `beancount__beancount-931`. The canary is not
a Stage C continuation, a six-task Phase 1, a 20-task study, a holdout, or a
leaderboard.

## Runtime Contract

[`stage_d/stage_d_freeze.json`](stage_d/stage_d_freeze.json) binds the
Stage D unblocker merge commit, Stage B experiment profile, Agent loop
entrypoints, source hashes, and the JSON schemas for `ValidationCheckpoint` and
`RunTest`. The runtime contract is deterministic and zero-provider; an
instance ID or repository must never implicitly enable it.

The future canary remains bound to the frozen provider, OpenAI-compatible
protocol, model, evaluator, 40-request ceiling, `validation_mode=stage_b`,
strict serial execution, fallback disabled, retry zero, and one Candidate per
task. It must use a new authorization identity and rolling per-request
reservation before any Provider transport.

## Evidence And Claims

Before any paid run, the Freeze requires a separate authorization identity and
an immutable checkout commitment. The committed Freeze itself authorizes no
paid execution and contains no dispatch path.

[`STAGE_D_CLAIMS_BOUNDARY.md`](STAGE_D_CLAIMS_BOUNDARY.md) limits any completed
canary to task-level protocol and evaluator evidence. It cannot modify Stage C
0/6 evidence, imply a Stage C Phase 2 result, or make a Stage D six-task or
20-task claim. A six-task Stage D phase requires fresh user authorization after
the canary report.

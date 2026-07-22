# Stage C Charter

## Scope

Stage C is a descriptive paired rerun of the accepted Goal 4 twenty-instance
matrix with `validation_mode=stage_b`. Goal 4 is the historical control. This is
not a holdout, leaderboard, pass@k, model comparison, significance test, or
general capability claim.

All twenty instances are pre-registered in
[`stage_c/stage_c_matrix.json`](stage_c/stage_c_matrix.json). Phase 1 is the
unaltered six-task Goal 4 prefix; Phase 2 is the remaining fourteen tasks in the
same relative order. Phase 2 requires an independent user authorization and a
verified Phase 1 Artifact; no result can automatically start Phase 2.

## Immutable Baseline

The generated baseline snapshot binds final run `29830820618`, Artifact ID
`8496125148`, archive SHA-256
`8b9309a9ee03b068bf96e69afd50ecc2c18e4a70046dc1ae99359310dc70c6c8`,
final-report SHA-256
`a404d82ec17c93471b842a4139e6d3f6350c672e8edf5744b57e16820e1c1a38`,
Goal 4 freeze/recovery commit `75a1eca465913e1c5be81e58eba89bc4d1cd8853`,
and source matrix SHA-256
`9ff16e850b92a6eb0bd1338cb85253a605fdfb0e0aa77180488382eca353972a`.

The snapshot is generated from the published Evidence Index. It does not rewrite
historical Artifacts, Claims, Usage, charges, settlements, or ledgers.

## Treatment Contract

The Stage C profile is explicit and hash-bound: deferred tools, `recovery_v1`,
`session_allow`, single Agent mode, and `validation_mode=stage_b`. The freeze
records its profile hash and runtime-contract hash. It keeps the Goal 4 Provider,
model, OpenAI-compatible protocol, evaluator commit, 40-request ceiling,
fallback disabled, automatic retry zero, strict serial order, and one formal
candidate per instance.

Only a separately approved immutable commit may be checked out by a future paid
workflow. This Freeze contains no such approval and no live execution path.

## Rolling Budget Contract

An authorization cap is the maximum conservative cost the user permits for a
phase. It is not a promise that every task can consume its 40-request theoretical
maximum. Before each transport request, the existing paid gate reserves exactly
one request at the frozen token limits; after raw Provider Usage is saved it
records the charge, settles the request, and clears `active_reservation`.

- Phase 1 cap: CNY 80.
- Cumulative Stage C cap: CNY 250.
- Phase 2 cap: CNY 250 minus Phase 1 combined conservative consumption.
- The frozen pricing and token limits compute CNY 1.830912 as the one-request
  maximum reservation.

If the next request cannot be reserved, the gate records `budget_blocked` before
transport, stops the current task and phase, and does not create a charge, Usage,
or automatic higher-budget request. A theoretical all-requests maximum above an
authorization cap is recorded as risk information, not a Freeze blocker.

## Evidence And Claims

Every completed task requires terminal evidence, prediction or explicit no-patch
terminal state, trace/stdout, validation events and summary, raw Provider Usage,
charge/settlement, no active reservation, Artifact location, evaluator report,
secret scan, and request count at or below 40. The forty-first request is
rejected before reservation or transport.

`budget_blocked`, infrastructure failure, and `not_run` are not scorable evaluator
outcomes. Unexecuted tasks remain `not_run`; they are never converted to
`unresolved` or zero-filled. A six-task smoke Claim requires all six scorable
outcomes. A full paired Claim requires all twenty distinct scorable outcomes.

## Boundaries

This Freeze is zero-provider. It does not initialize a Provider client, run a
Stage C Trial, rerun Goal 4, call an official evaluator on a new patch, dispatch
a paid workflow, read a gold patch, expose Goal 4 outcomes/failure taxonomy to an
Agent, or change `codepacex/**` runtime behavior. Diagnostic-set reuse and
historical-control Provider/time drift are mandatory limitations of every later
report.

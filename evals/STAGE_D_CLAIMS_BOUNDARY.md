# Stage D Claims Boundary

## Permitted Canary Report

If and only if both frozen canary tasks reach terminal evidence, a Stage D
report may state each task's requests, tool distribution, edit/test evidence,
checkpoint state, Candidate/diff consistency, evaluator outcome, cost, and
`active_reservation=null`. It may compare these process measures descriptively
with Goal 4 and Stage C.

Resolved count is a descriptive performance measure, not the proof that the
protocol is unlocked. The actual GO criteria are non-empty Candidate/diff
consistency, at least one edit and controlled target test per task, no shared
checkpoint deadlock, and closed reservations.

## Prohibited Claims

The canary must not change or append to Stage C Phase 1's 0 resolved / 6
unresolved evidence. It cannot be called a Stage C retry, pass@2, second
Candidate, six-task Phase 1, 20-task paired study, holdout, leaderboard, or
general model-capability result.

An incomplete canary is reported as attempted/not-run or the precise terminal
infrastructure state. It must not be converted to unresolved, zero-filled, or
used to authorize the Stage D six-task phase. Any six-task authorization is a
new user decision after the canary report.

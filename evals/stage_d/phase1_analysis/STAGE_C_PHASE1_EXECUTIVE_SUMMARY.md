# Stage C Phase 1 Executive Summary

## Decision

`PHASE_2_NO_GO`.

Phase 1 reached six scorable terminals, but all six are unresolved and every submitted prediction has an empty `model_patch`. This is not evidence that the six underlying issues are uniformly unsolvable. It is direct evidence that the enabled Stage B protocol was not usable by the live Agent in this workflow.

The final Artifact is `8529432956` from run `29918131993`, archive SHA-256 `66f8aee8d38964b3e6a74b4d3b01498fb206a7e76b5e31e2873c699ead344d0e`. It binds commit `f82a1b6b89fde612b9db61d684a7549ae3fc5ffd`, the frozen task bundle SHA-256 `71a31d577daea9e8653de59b95464ecde3e7f2d680dd00f38d617a08ca2695ab`, and a closed reservation state.

## Observed Result

| Metric | Value |
| --- | ---: |
| Scorable terminals | 6/6 |
| Resolved / unresolved | 0 / 6 |
| Empty predictions | 6/6 |
| Provider requests | 251 |
| Settlements | 252 (one CNY 0 timeout cancellation) |
| Verified / conservative consumption | CNY 53.417964 |
| Authorization cap | CNY 80 |
| Active reservation | null |
| Phase 2 | not started |

The third task retains two distinct records: an immutable non-scorable 11-request `ConnectTimeout` attempt (CNY 1.361364) and one permitted 40-request `infrastructure_replacement_attempt` (CNY 9.719028). They are not treated as one Agent session or as a model retry.

## Primary Finding

The Agent found relevant code on every task and attempted writes on every task. No Stage C `EditFile` or `WriteFile` call actually executed. The Stage B reproduction gate blocked writes and compound Bash commands until a structured reproduction declaration and full inventory existed. The live Agent repeatedly supplied prose instead of the required observed `tool_call_id`, an invalid exception reason, or incomplete inventory fields. Every request-20/30/36 checkpoint remained pending. The ceiling then ended all six sessions.

This is a protocol/tool-contract integration failure, not a Candidate export failure: the final workspace had no executable edit calls, each prediction is empty, and every evaluator reported an empty patch. The two historical Goal 4 resolved controls (`beetbox__beets-5495` and `beancount__beancount-931`) regressed through the same zero-edit path.

## Consequence

Running the remaining fourteen tasks would mostly repeat a demonstrated failure mode while spending the remaining Stage C authorization. Do not start Phase 2. First repair and zero-provider validate the Stage B live-tool protocol; then obtain a fresh human decision on whether a new, explicitly distinct Phase 1 experiment is justified.

Detailed evidence and recommendations are in `STAGE_C_PHASE1_ROOT_CAUSE_REPORT.md`.

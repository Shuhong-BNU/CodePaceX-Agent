# Evaluation V2 Control Canary

The Control Canary is a new two-task Evaluation V2 experiment. Its fixed order
is `beetbox__beets-5495`, then `beancount__beancount-931`. It neither reuses a
historical Candidate nor changes historical ledger or Artifact evidence.

## Final V2.1 Evidence

The one paid Control Canary completed in Actions run `30031616048` with internal
Run ID `v2-control-canary-fc5dbd1-20260723T172932Z`. Artifact `8573855537`
(`sha256:d67537436c144129413048dbbf2ce5ac6f4b80ed25d3f8c492744ed98f33b71d`)
records 46 requests, 571,320 input tokens, 15,469 output tokens, CNY `7.412724`,
46 settlements, and a closed ledger with `active_reservation=null`.

Both historical Goal 4 resolved controls again produced a non-empty Candidate,
matching workspace diff SHA, one official evaluator report, and `resolved`:
`beetbox__beets-5495` used 32 requests/CNY `5.498304`; `beancount__beancount-931`
used 14/CNY `1.914420`. The V2.2 gate was `V2_2_DIAGNOSTIC_PILOT_GO`. This is
evidence that the V2 chain reproduces those two controls, not a full-matrix
success-rate estimate. The complete identities are indexed in
[`EVALUATION_HISTORY.md`](../EVALUATION_HISTORY.md) and
[`EVALUATION_ARTIFACT_INDEX.md`](../EVALUATION_ARTIFACT_INDEX.md).

Each task is materialized at its pinned repository commit, bootstrapped in an
isolated venv with the repository's own editable installation plus pytest, and
then runs a bounded task-related pytest target. A failed project baseline is
recorded, but dependency, collection, argv, path, and command failures are
environment blockers.

The Freeze records the fresh runtime, pricing, task order, bootstrap, evaluator,
permission, export, report-selection, and stop contracts. The theoretical
exposure is CNY 1.830912/request, CNY 73.236480/task, and CNY 146.472960/two
tasks. Goal 4 selected control costs total CNY 4.307916; the CNY 15.000000
recommendation is a rounded three-times historical hard cap, not authorization.

The generic ledger's `C` enum is retained solely because the historical
`paid_gate.py` source is Freeze-bound by the closed Stage D.1 experiment. Every
V2 authorization, ledger, reservation ID, run ID, and Artifact root is new;
this is not a Stage C continuation and does not reuse a Stage C ledger.

The only runnable workflow path is a zero-provider preflight and two cancelled
one-request reservations. The future serial paid runner has no built-in Provider
executor, requires separately authorized injection, and stops before task two
when task one has a runner, evaluator, provider, or reservation failure.

## Release Readiness

The Control Canary workflow also runs a deterministic two-task shadow path. It
creates fresh authorization, allocation, ledger, rolling reservations,
Candidates, and evaluator-shaped reports, but uses no Provider transport or
secret. Its first task is deliberately unresolved and healthy, so the second
task proves the serial continuation rule; the second task is resolved. The
shadow Artifact contains `shadow-canary-summary.json`,
`canary-result-summary.json`, and a concise Markdown receipt.

To inspect a frozen release locally after the normal zero-provider preflight:

```bash
python -m evals.evaluation_v2.control_canary release-check \
  --root . --freeze ARTIFACT/freeze \
  --preflight-summary ARTIFACT/preflight/preflight-summary.json \
  --output ARTIFACT/release-check.json
```

`READY_FOR_PAID_CANARY` requires `HEAD == origin/main`, a clean worktree, a
valid Freeze, healthy two-task preflight, and no active reservation. The output
also supplies the exact future workflow inputs. A user must provide a new
authorization acknowledgement and fresh run ID before dispatching exactly one
`paid_execution=true` workflow; this command never dispatches it.

For a completed paid or shadow Artifact, compile the receipt with:

```bash
python -m evals.evaluation_v2.control_canary summary --artifact-root ARTIFACT
```

An unresolved first task may continue only with a non-empty Candidate, matching
diff SHA, completed evaluator/report selection, closed ledger, and no Provider,
runner, task-environment, or evaluator infrastructure failure. Missing
Candidate, evaluator failure, provider transport failure, active reservation,
or budget block stops task two. Never retry automatically, enable fallback,
increase the CNY 15 cap, or automatically enter V2.2. The summary's V2.2 Gate
is GO only for two Candidates, two scorable reports, no infrastructure failure,
a closed ledger, and a positive capability signal.

# Evaluation V2 Control Canary

The Control Canary is a new two-task Evaluation V2 experiment. Its fixed order
is `beetbox__beets-5495`, then `beancount__beancount-931`. It neither reuses a
historical Candidate nor changes historical ledger or Artifact evidence.

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

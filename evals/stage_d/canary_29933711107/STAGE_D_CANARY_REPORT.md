# Stage D Canary Report

## Contract

The paid Canary used one new authorization identity and an isolated Stage D
evidence root. It was limited to two frozen tasks, strict serial execution,
40 requests per task, no fallback, no automatic retry, one Candidate per task,
and rolling per-request reservation under a CNY 30 cap.

The ledger records 40 request charges and 40 settlements for the first task;
the final `active_reservation` is `null`. There was no budget block, no second
Canary dispatch, and no Stage C evidence modification.

## Task Results

| Order | Instance | Terminal status | Requests | Candidate | Evaluator |
| --- | --- | --- | ---: | --- | --- |
| 1 | `beetbox__beets-5495` | `infrastructure_error / candidate_empty` | 40 | empty | not run |
| 2 | `beancount__beancount-931` | `not_run` | 0 | not applicable | not run |

First-task accounting: 538,264 input tokens, 12,538 output tokens, 4,027
reasoning tokens, and CNY `6.910536` verified cost.

## Protocol Observations

- `RunTest` executed once and returned a dependency-related collection failure.
- A `record_reproduction_exception` declaration succeeded.
- Eight trace-verified `declare_contract_inventory` calls supplied JSON strings;
  all were rejected because the schema required a dictionary.
- Nine `EditFile` calls and one `WriteFile` call were denied because no complete
  contract inventory had been recorded.
- The 20, 30, and 36 checkpoints remained pending; no acknowledgement succeeded.
- No workspace diff, Candidate patch, or evaluator outcome exists for task one.

## Comparison

The two tasks are historical Goal 4 resolved controls. Stage C showed a broader
Stage B live-tool protocol deadlock. Stage D demonstrated that the controlled
test/reproduction path is reachable, but the inventory parameter boundary still
prevents the required edit-test-export loop. This is descriptive process
evidence only; it is not a new benchmark Claim.

## Follow-up Boundary

Stage D.1 is a separate identity. It must preserve this Artifact and its ledger,
complete zero-provider regression coverage, and receive a new authorization
before one fresh single-task Canary is run.

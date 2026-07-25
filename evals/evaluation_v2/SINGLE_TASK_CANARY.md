# Evaluation V2 single-task paid canary

This entry is restricted to `aws-cloudformation__cfn-lint-3749`.  It is not
the two-task Control Canary, a Phase A batch, or a Full-20 replay.  There is no
workflow input that can select a different task.

The committed derived Freeze binds the approved post-merge Full-20 readiness
identity, the fixed task projection and environment contract, the single-task
runner/workflow source hashes, the Bailian OpenAI-compatible Provider, model
`qwen3.7-max-2026-06-08`, `8192` completion limit, `6144` thinking budget,
40-request ceiling, 50 Agent iterations, disabled fallback/retry, and strict
serial execution.

The spendable ceiling is the frozen theoretical one-task maximum of CNY
`73.236480`.  A CNY `0.000001` authorization margin is nonspendable safety
reserve only; it cannot be allocated or charged.  Every request receives a
fresh rolling reservation and must settle before another request can start.

With `paid_execution=false`, the workflow has no Secret environment and
performs only a zero-provider rehearsal.  It records a normal fake-Usage path
and the exact `completion_tokens=8197`, `reasoning_tokens=6144`,
`text_tokens=8197` contract-violation path.  Both ledgers close with
`active_reservation=null`, while external Provider requests, Usage and charge
remain zero.

The future `paid_execution=true` job is intentionally dormant until separate
authorization supplies the exact derived Freeze hash, authorization cap,
acknowledgement and new Internal Run ID.  A Usage contract violation settles
the original reported Usage, closes the reservation, preserves a non-empty
Candidate when available, sets `evaluator=not_run`, and terminates as
`provider_usage_contract_violation`.  It never retries, continues, starts a
second task, or expands to Full-20.

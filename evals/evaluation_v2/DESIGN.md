# Evaluation V2 Base Lane

Evaluation V2 is a new zero-provider experiment identity. It does not alter or
reopen Stage C, Stage D, or Stage D.1 evidence.

The Base Lane materializes `beetbox__beets-5495` at its frozen base commit,
without applying or exposing its gold patch. A deterministic local `LLMClient`
drives `Agent.run_to_completion()` through the normal `ToolRegistry`,
`PermissionChecker`, `ReadFile`, `Grep`, `RunTest`, and `EditFile` paths. The
edit is a harmless source comment derived from the actual preceding ReadFile
result. Both validations execute the task's frozen `FAIL_TO_PASS` pytest target.
The edit is deliberately non-gold and is expected to be unresolved.

The runner exports the workspace Git diff as the Candidate and binds both
Candidate and diff SHA-256 to the same bytes. The official SWE-bench-Live
evaluator then receives that Candidate. Detailed reports are selected ahead of
an otherwise valid aggregate summary by the existing fail-closed collector;
unexpected extra detailed reports are an error.

Provider transport is absent by construction. The V2 ledger records exactly one
`CNY 0` replay reservation and closes it without Usage, charge, settlement, or
secret access. Validation is telemetry in this lane, not a Stage B gate.

Guided Lane remains design-only. It may compare a fixed guidance artifact with
the same task lifecycle in a later, separately authorized phase; it is not part
of this workflow or its claims.

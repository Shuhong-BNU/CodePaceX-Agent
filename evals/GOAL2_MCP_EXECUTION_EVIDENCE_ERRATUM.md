# Goal 2 MCP execution-evidence erratum

This post-review erratum records an evidence interpretation boundary; it does
not alter a frozen Trial, Usage record, Token count, charge, settlement, ledger
entry, cohort index, or Claim artifact.

## Finding

The pre-review MCP grader accepted a matching `tool_use` multiset and answer
without requiring a call-ID-correlated, non-error `tool_result`. A denied or
failed fixture call can therefore have been counted as a task success by that
grader. The corrected grader now requires an exact fixture-call plan and one
successful correlated result for every expected call.

## Historical evidence status

The frozen MCP source artifacts required to retrospectively apply that rule
are not present in this worktree:

- `evals/.runs/goal2/`
- `evals/.runs/goal2-control/mcp-formal-cohort-index.json`
- `evals/.runs/goal2-control/mcp-claims-evidence.json`

Accordingly, the historical counts of 300 terminal Trials, 299
Usage-complete Trials, and 149 Usage/Token matched pairs remain preserved as
accounting and pairing facts, but are **not** evidence of successful MCP
fixture execution. This worktree cannot classify historical Trials as
successful execution, attempt-only, permission-denied, tool-error, or missing
result evidence.

The MCP capability and task-success interpretation is therefore
`evidence_insufficient` until the immutable source traces can be read and
audited with the corrected correlation rule. No Trial may be rerun to fill this
gap, and no missing result is inferred from a final answer, `tool_use`,
permission allow, Usage, Token, or settlement record.


# Goal 2 Handoff

This is the operational handoff for the closed Goal 2 study. Read
[`GOAL2_FINAL_REPORT.md`](GOAL2_FINAL_REPORT.md) first for measurements,
claims, Artifact hashes, and publication limits.

## Frozen outcome

- Branch: `codex/goal2-benchmark-evidence`; PR #16 is Open + Draft only.
- Formal MCP and Permission matrices are complete. Do not rerun any terminal
  Trial, including `mcp_one_08/1`, Retention `summary_only/session-01`, or
  Permission `sandbox_auto_allow/perm_write_workspace/4`.
- Retention is legally closed as partial. Multi-Agent historical gate evidence
  is insufficient after the runtime-artifact scope erratum and has no formal
  Provider Trial. Formal SWE remains infrastructure-blocked; the three
  formal eight-hour sessions remain deferred.
- Ledger checkpoint: CNY `92.579316` spent; `active_reservation=null`; CNY 90
  safety reserve untouched.

## Evidence locations

All raw/control artifacts are Git-ignored, local files under:

- `evals/.runs/goal2/` — formal Run Artifacts.
- `evals/.runs/goal2-control/` — ledger, authorization/allocation, cohort
  index, resume provenance, reconciliations, Claims inputs/outputs, and
  scanner evidence.

The final report records SHA-256 values for the current ledger, cohort, and
Claims files. Recompute Claims without modifying frozen Artifacts:

```bash
uv run --offline python -m evals.goal2_claims generate \
  --runs-dir evals/.runs/goal2 --exclude-multi \
  --cohort-index evals/.runs/goal2-control/mcp-formal-cohort-index.json \
  --output /private/tmp/goal2-handoff/claims.goal2.yaml
uv run --offline python -m evals.goal2_claims compile \
  --runs-dir evals/.runs/goal2 \
  --claims /private/tmp/goal2-handoff/claims.goal2.yaml \
  --cohort-index evals/.runs/goal2-control/mcp-formal-cohort-index.json \
  --output /private/tmp/goal2-handoff/claims.goal2.compiled.yaml
```

## Non-negotiable boundaries

- Never infer or fabricate Provider Usage, Token counts, request charges, or
  Provider bills for an evidence gap. Conservative settlement is budget
  accounting only.
- Do not spend the CNY 90 reserve, transfer category budgets, rerun terminal
  Trials, or make PR #16 Ready/Merged under this Goal.
- Any future study must have a separately authorized matrix, budget decision,
  and evidence plan. It is not a continuation of the frozen Goal 2 matrix.

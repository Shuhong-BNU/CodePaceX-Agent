# Goal 2 Final Evidence Report

This report records the final Goal 2 evidence boundary. It is intentionally
not a claim that every planned study produced a comparable effect estimate.
Formal artifacts are local and Git-ignored under `evals/.runs/goal2/` and
`evals/.runs/goal2-control/`.

For a safe continuation boundary and exact non-rerun rules, see
[`GOAL2_HANDOFF.md`](GOAL2_HANDOFF.md).

Post-review MCP execution-evidence boundary: see
[`GOAL2_MCP_EXECUTION_EVIDENCE_ERRATUM.md`](GOAL2_MCP_EXECUTION_EVIDENCE_ERRATUM.md).
The preserved 299 Usage-complete Trials and 149 Usage/Token pairs are not a
claim of successfully executed MCP fixture calls while the frozen source traces
are unavailable for call-ID-correlated result auditing.

## Frozen identities and budget

- Claims evidence-control commit: `142e4ce`; the full ID is recorded in Git
  history. The paid MCP agent execution commit remains distinct below.
- MCP agent execution commit: `b09b1849d74d8873ecf287fc2af6e63712adbe62`;
  budget control was recorded separately and is not represented as the Agent
  execution commit.
- Model for paid formal requests: `qwen3.7-max-2026-06-08`, with retry `0`
  and fallback disabled.
- Ledger checkpoint: CNY `92.579316` spent, `1225` request charges, `1114`
  settlements, and no active reservation. The CNY `90` safety reserve was not
  used.
- PR #16 remains Open and Draft. It must not be marked ready or merged as part
  of this Goal.

## Study status

| Study | Result | Evidence boundary |
| --- | --- | --- |
| MCP tool loading | Terminal/accounting matrix preserved; execution evidence insufficient | 299 Usage-complete Trials and 149 valid Token pairs remain accounting facts. They are not verified successful fixture executions because the frozen source traces are unavailable for the post-review `tool_result` correlation audit. `mcp_one_08/1` remains one terminal infrastructure error, is included in attempted count and settled cost, and is excluded from Usage-derived Token measures. |
| Retention | Auditable partial | `summary_only` session 01 is a terminal infrastructure error with unknown final Provider Usage; it was conservatively settled and is not rerun. `recovery_v1` formal sessions were not run, so no profile comparison is claimed. |
| Permission | Complete terminal matrix | `default` 44 success/6 task failure; `session_allow` 38/12; `explicit_rules` 42/8; `sandbox_auto_allow` 41 success/8 task failure/1 infrastructure error. Results are limited to Darwin arm64. |
| Multi-Agent | Insufficient data | The zero-model grader gate returned NO-GO. No formal Multi-Agent Provider Trial was sent and no effect is claimed. |
| Formal SWE | Infrastructure-blocked | No formal resolved-rate Claim is produced. |
| Three 8-hour sessions | Deferred | No formal long-session Claim is produced; the two-hour diagnostic Pilot is not a substitute. |

## Preserved MCP accounting measurements

The hash-pinned Trial cohort index is `mcp-formal-cohort-index.json`. Its
canonical content SHA-256 / evidence identity is
`7a53933596b20d2933840e01f363800db7b981cc5ef66bd401ec0092e0ea4594`.
As implemented by `load_mcp_cohort()` and `canonical_hash()` in
`evals/mcp_cohort.py`, this identity is computed after removing the top-level,
self-referential `sha256` field and canonicalizing the remaining JSON content.
It is distinct from the full-file byte SHA-256 `fedda1d55f758addf2b2ade46849ec7afbf708270577e7b08138c20e19233da8`
listed in the Artifact table below. Both values are reproducible and serve
different integrity purposes; their difference does not indicate Artifact
drift.
The compiled cohort evidence previously verified the following accounting and
Usage/Token measurements. It does not establish successful fixture execution;
see the post-review erratum above.

| Measurement | Value |
| --- | ---: |
| eager input / output / cache Tokens | 2,277,984 / 36,255 / 1,689,344 |
| deferred input / output / cache Tokens | 1,594,316 / 39,603 / 744,960 |
| matched-pair input reduction median / p95 | 16.013311% / 57.331863% (n=149) |
| eager / deferred settled cost | CNY 28.640988 / CNY 22.488144 |
| eager task-success rate | 0.333333 (n=150) |

The source runtime records did not persist `tools_bytes`, so both MCP schema
byte Claims remain `insufficient-data`. Deferred task-success rate is also
`insufficient-data`: the retained infrastructure-error Trial has no durable
score numerator/denominator. Those omissions are not filled with inferred
values.

## Claims and artifact index

- `mcp-claims-evidence.json`: Trial-level recomputation from the frozen cohort.
- `claims.goal2.yaml`: declarative Claim input.
- `claims.goal2.compiled.yaml`: compiler output; it includes source Run IDs,
  cohort/provenance hashes, sample sizes, limitations and the Multi-Agent
  NO-GO Claim.
- The compiler reports 23 verified Claims and 10 `insufficient-data` Claims.
  The latter are evidence boundaries, not negative results.

Current local Artifact file SHA-256 values are:

| Artifact | SHA-256 |
| --- | --- |
| `budget-ledger.json` | `9c583fb3ed09d7125812d1a3b72afe0d64b7b33cbb78a7a2ca65a39df07d24a4` |
| `mcp-formal-cohort-index.json` | `fedda1d55f758addf2b2ade46849ec7afbf708270577e7b08138c20e19233da8` |
| `mcp-claims-evidence.json` | `22aafd8be11481b757b93ceb5a66b5ef6fae477de276ae4c05e5a9a3f060f1fe` |
| `claims.goal2.yaml` | `2517f90dec7dba29bb2edbc67bb774f04896fd17b71a1f5904e390b222d618b3` |
| `claims.goal2.compiled.yaml` | `027f9f98e0edfe3ff34643879b0f3cd9ed956737fc74880c62d91d0327352a01` |

The compilation path is:

```bash
uv run --offline python -m evals.goal2_claims generate \
  --runs-dir evals/.runs/goal2 --exclude-multi \
  --cohort-index evals/.runs/goal2-control/mcp-formal-cohort-index.json \
  --output evals/.runs/goal2-control/claims.goal2.yaml
uv run --offline python -m evals.goal2_claims compile \
  --runs-dir evals/.runs/goal2 \
  --claims evals/.runs/goal2-control/claims.goal2.yaml \
  --cohort-index evals/.runs/goal2-control/mcp-formal-cohort-index.json \
  --output evals/.runs/goal2-control/claims.goal2.compiled.yaml
```

The Goal-specific compiler keeps the generic Run-level compiler fail-closed.
Only MCP uses its separately hash-pinned, Trial-level selection because the
formal deferred cohort contains one known infrastructure-error Trial. It
re-reads every selected terminal event, Usage record, settlement amount and
provenance hash before it verifies an MCP measurement.

## What must not be claimed

- No causality, significance, production-MCP behavior, cross-platform
  permission generalization, Multi-Agent comparison, formal SWE score, or
  eight-hour durability result.
- No Token or cost-efficiency result for Trials with unknown Provider Usage.
- No Provider bill is implied by a `conservative_reserved_amount` settlement.
  Such a settlement is budget accounting only, preserves unknown Tokens, and
  retains the original evidence gap.

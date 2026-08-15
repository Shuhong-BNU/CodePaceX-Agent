# Formal Experiments

This page is a navigation table, not a duplicate of every report or raw
Artifact. Read the linked report for the exact denominator, identity, hashes
and non-claim boundary.

| Study / stage | Current state | Formal report or contract | Raw evidence / Artifact entry |
| --- | --- | --- | --- |
| Lightweight six-task Eval | Complete: `6/6 PASS` | [evals README](../../evals/README.md) | `evals/tasks/`, `evals/fixtures/`, local `evals/.runs/` (ignored) |
| Goal 2 MCP | Accounting complete; execution evidence insufficient | [GOAL2 final report](../../evals/GOAL2_FINAL_REPORT.md), [MCP erratum](../../evals/GOAL2_MCP_EXECUTION_EVIDENCE_ERRATUM.md) | Goal2 report's hash-pinned cohort and Claims references |
| Goal 2 Permission | Complete terminal matrix, Darwin arm64 scope | [GOAL2 final report](../../evals/GOAL2_FINAL_REPORT.md) | Goal2 report and runbook |
| Goal 2 Retention / Multi-Agent / formal SWE | Auditable partial / evidence insufficient / infrastructure-blocked | [GOAL2 final report](../../evals/GOAL2_FINAL_REPORT.md), [scope erratum](../../evals/GOAL2_MULTI_AGENT_SCOPE_EVIDENCE_ERRATUM.md) | Formal boundaries are preserved; no inferred score |
| Goal 3 SWE Pilot | `3/3` scorable, `1 resolved / 2 unresolved` | [Evaluation history](../../evals/EVALUATION_HISTORY.md) | `evals/goal3/` and official-control records |
| Goal 4 | `GOAL4_ACCEPTED`, `20/20` scorable, `4 resolved / 16 unresolved` | [Final report](../../evals/GOAL4_FINAL_REPORT.md), [evidence index](../../evals/GOAL4_EVIDENCE_INDEX.md) | `evals/claims.goal4.json`, `evals/goal4/`, final Artifact identity in report |
| Capability V3.0 | Two-run closeout: `20 terminal`, `19 scorable`, `5 resolved`, `1 infrastructure_error` | [Final report](../../evals/CAPABILITY_V3_GOAL4_FINAL_REPORT.md), [postmortem](../../evals/CAPABILITY_V3_ACTIVATION_POSTMORTEM.md) | `evals/evaluation_v2/activation_v31/`, V3 comparison CSV and report identities |
| Evaluation V2 | Zero-provider Base Lane and replay contracts; no paid score | [Design](../../evals/evaluation_v2/DESIGN.md), [Full replay](../../evals/evaluation_v2/FULL_20_REPLAY.md) | `evals/evaluation_v2/*payloads/`, manifests and schemas |
| Stage B | Implemented; offline validation only | [Charter](../../evals/STAGE_B_CHARTER.md), [report](../../evals/STAGE_B_REPORT.md) | `evals/stage_c/` replay fixtures and Stage B tests |
| Stage C | Zero-provider freeze; no Trial | [Freeze report](../../evals/STAGE_C_FREEZE_REPORT.md), [runbook](../../evals/STAGE_C_RUNBOOK.md) | `evals/stage_c/`, freeze and task-bundle manifests |
| Stage D | Zero-provider protocol-canary freeze | [Freeze report](../../evals/STAGE_D_FREEZE_REPORT.md), [claims boundary](../../evals/STAGE_D_CLAIMS_BOUNDARY.md) | `evals/stage_d/` and `evals/stage_d1/` |
| P3-A | Frozen eight-run paired inputs and zero-provider rehearsal; no paid result | [P3-A manifest](../../evals/evaluation_v2/p3a_paired_pilot/8-run-manifest.json) | `evals/evaluation_v2/p3a_paired_pilot/` including rehearsal ledgers and schemas |
| P3-B | Fail-closed entrypoint/readiness evidence; A4 failed before Provider transport; A5 not run | [P3-B manifest](../../evals/evaluation_v2/p3b_post_merge_rebind/8-run-manifest.json) | `evals/evaluation_v2/p3b_post_merge_rebind/` including dispatch guard, ledger and zero-provider rehearsal |

## P3-B public engineering notes

The retained governance notes record implementation, root-cause and zero-
provider evidence. Their dated status labels are historical records, not a
promise that the old proposed dispatch is still current:

- [first paid-attempt audit](CodePaceX_P3B首次付费执行失败审计摘要_v1.1.0_20260731_1453.md)
- [R1 acceptance](CodePaceX_P3BR1验收结果_v1.0.0_20260731_1557.md)
- [R2 post-merge zero-provider verification](CodePaceX_P3BR2_PostMerge零Provider核验报告_v1.0.0_20260731_1557.md)
- [R4 input-contract hardening](CodePaceX_P3BR4_Paid输入合同零Provider审计与加固_v1.0.0_20260731_1935.md)
- [canonical identity and bundle SHA root cause](CodePaceX_P3B_Canonical身份生成与Bundle字节SHA根因和合同_v1.0.0_20260731_2230.md)
- [A4 failure and A5 condition](CodePaceX_P3B_A4失败根因修复与A5条件合同_v1.0.0_20260801.md)

The older execution contracts and Codex prompts are retained locally under
`_local/plans/` and `_local/prompts/`; they are not current public instructions.

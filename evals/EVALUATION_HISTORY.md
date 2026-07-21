# Evaluation history

## Terms

- **Study**: one evaluation research program with a defined question and evidence boundary.
- **Instance**: one distinct task definition. Repeating it does not create a new Instance.
- **Trial**: one actual execution of an Instance under a registered condition.
- **Attempt**: an execution attempt, including infrastructure retries of the same Trial.
- **Session**: the unit of a long-session or retention evaluation.
- **Control**: a zero-Provider evaluator or runtime control; it is not a paid Trial.
- **CI test**: software verification. Pytest counts, dry-runs, preflights, and secret scans are not formal experiments.

Units in this ledger are intentionally not additive. In particular, MCP Trials, SWE Instances, Sessions, Controls, and CI tests must not be summed into a single "total experiments" number. Infrastructure retries increase Attempts, not distinct Instances.

## Study ledger

| ID | Goal | Study | Unit | Planned | Distinct instances | Attempts | Scorable | Provider | Model | Cost | Status | Primary result | Evidence boundary |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- |
| BV1-6TASK | Baseline v1 | Lightweight six-task Agent Eval | Instance | 6 | 6 | 6 | 6 | unknown | unknown | unknown | complete | 6/6 PASS; 0 FAIL/ERROR/WARNING | Result is preserved in the eval README; original run ID, Artifact, model, cost, and evaluated commit are not preserved in this repository. |
| G2-MCP | Goal 2 | MCP eager/deferred tool loading | Trial | 300 | 30 controlled tasks | 300 terminal | unknown | Bailian | `qwen3.7-max-2026-06-08` | CNY 51.129132 settled across eager/deferred | accounting complete; execution evidence insufficient | 299 Usage-complete Trials; 149 valid Usage/Token pairs | These are accounting and pairing facts, not 299 verified successful MCP fixture executions. Frozen source traces are unavailable for call-ID-correlated result auditing. |
| G2-PERM | Goal 2 | Permission strategies | Trial | 200 | 10 controlled tasks | 200 terminal | 199 | Bailian | `qwen3.7-max-2026-06-08` | unknown by study | complete terminal matrix | default 44/6; session_allow 38/12; explicit_rules 42/8; sandbox_auto_allow 41 success/8 task failure/1 infrastructure error | Darwin arm64 only; the one infrastructure error is not scorable. |
| G2-RET | Goal 2 | Context retention profiles | Session | 20 | 10 session seeds | 1 | 0 | Bailian | `qwen3.7-max-2026-06-08` | unknown; conservatively settled | auditable partial | `summary_only` session 01 ended in infrastructure error | Final Provider Usage is unknown; `recovery_v1` formal Sessions were not run and no profile comparison is claimed. |
| G2-MULTI | Goal 2 | Single vs multi-agent | Trial | 50 | 5 controlled tasks | 0 formal Provider Attempts | 0 | none sent | not applicable | CNY 0 formal | evidence_insufficient | No formal Multi-Agent Provider Trial | Historical zero-model NO-GO cannot be regraded because the frozen control Artifact is unavailable and had a known runtime-log scope mismatch. |
| G2-SWE-DIAG | Goal 2 | SWE three-task diagnostic Pilot | Instance | 3 | 3 | 3 | 3 | Bailian | `qwen3.7-max-2026-06-08` | unknown by study | completed diagnostic | 0/3 resolved after evaluator recovery | This Pilot is not the Goal 2 formal matrix and is not mixed into a formal resolved-rate Claim. Source Artifact identity is not preserved in the repository. |
| G2-SWE-FORMAL | Goal 2 | Formal SWE-bench-Live | Instance | 20 | 20 planned | 0 | 0 | none sent | not applicable | CNY 0 formal | infrastructure-blocked | No formal resolved-rate result | The complete formal empty-equivalent control had non-task PASS_TO_PASS failures; no formal 20-task run was started. |
| G2-LONG-PILOT | Goal 2 | Two-hour long-session diagnostic | Session | 1 | 1 | 1 | 1 | Bailian | `qwen3.7-max-2026-06-08` | CNY 0.342108 estimated | completed diagnostic | 8/8 cycles, planned restart/recovery, 4 hash-chained checkpoints | A two-hour diagnostic is not an eight-hour formal durability result. Raw Artifact is not preserved in this worktree. |
| G2-LONG-FORMAL | Goal 2 | Three eight-hour long Sessions | Session | 3 | 3 planned | 0 | 0 | none sent | not applicable | CNY 0 | deferred | No formal long-session Claim | All three eight-hour Sessions are deferred. |
| G2-HOOK | Goal 2 | Deterministic Hook interception | deterministic case | 100 | 100 cases | 100 | 100 | none | not applicable | CNY 0 | complete | 100/100 deterministic cases | Zero-model and zero-network local safety study; it is not a Provider experiment or a Session. |
| G3-CONTROL | Goal 3 | Official SWE empty/gold controls | Control | 2 | 1 official Instance | 2 | 2 control outcomes | none | none | CNY 0 | complete | Empty unresolved; gold resolved | Zero-Provider evaluator controls on native Linux x86_64. They validate the evaluator path and are not paid benchmark Instances. |
| G3-SWE-PILOT | Goal 3 | Three-task paid SWE Pilot | Instance | 3 | 3 | 3 | 3 | Bailian | `qwen3.7-max-2026-06-08` | CNY 9.078540 | completed Pilot | 1 resolved / 2 unresolved | Native Linux x86_64, official evaluator complete. This is a Pilot, not a formal 20-task result or pass@k. |
| G4-SWE-FORMAL | Goal 4 | Formal 20-task SWE-bench-Live subset | Instance | 20 | 20 | 25 paid Attempts | 20 | Bailian | `qwen3.7-max-2026-06-08` | CNY 165.044424 verified actual; CNY 170.537160 conservative budget consumption | `GOAL4_ACCEPTED` | 4 resolved / 16 unresolved | Pre-registered Python-only Lite subset, not the full Lite leaderboard or pass@k. Five infrastructure retries are included in Attempts and are not additional Instances. |
| G4-INFRA-RECOVERY | Goal 4 | Infrastructure recovery Attempts | Attempt | not planned as Instances | 0 additional | 5 | 0 additional | Bailian | `qwen3.7-max-2026-06-08` | CNY 34.158732 historical failed-attempt verified Provider cost | recovered and closed | Final 20 Instances all became scorable | This row is a decomposition of the 25 Attempts and its cost is already included in Goal 4 accounting; do not add it again. |
| G5-LONG-CANDIDATE | Goal 5 candidate | Formal long-session follow-up | Session | 3 candidate Sessions | 3 planned | 0 | 0 | not authorized | not selected | CNY 0 | planned/deferred | No result | Candidate only. No Stage B/C, Provider comparison, or eight-hour Session is authorized by this ledger. |

Success/task-failure pairs in the Permission row are terminal Trial outcomes, not pytest counts. Goal 4 selected terminal cost (CNY `130.885692`), historical failed-attempt cost (CNY `34.158732`), uncertain exposure (CNY `5.492736`), and verified actual Provider cost (CNY `165.044424`) remain separate accounting measures.

## Same-unit summaries

These summaries preserve units and exclusions; they are not a grand total.

| Unit | Preserved summary |
| --- | --- |
| Distinct SWE Instances | Goal 3 Pilot + Goal 4 formal establish 23 distinct evaluated SWE Instances because Goal 4 explicitly excludes Goal 3 IDs. Goal 2 diagnostic has 3 Instances, but their IDs/overlap are not preserved here; the cross-study distinct total is therefore `unknown` (between 23 and 26). The 20 planned but blocked Goal 2 formal Instances are excluded. |
| Paid SWE Attempts | 3 Goal 2 diagnostic + 3 Goal 3 Pilot + 25 Goal 4 Attempts = 31 paid SWE Attempts. Goal 4's five infrastructure retries remain Attempts of existing Instances. |
| MCP Trials | 300 terminal Trials; 299 Usage-complete; 149 Usage/Token pairs. Successful fixture execution remains `evidence_insufficient`. |
| Permission Trials | 200 terminal Trials, of which 199 are scorable and one is an infrastructure error. |
| Sessions | 1 Retention Session attempted (unscorable), 1 two-hour long-session diagnostic completed, and 3 eight-hour formal Sessions deferred. Goal 5 candidate Sessions have not started. |
| Zero-Provider Controls | 2 Goal 3 official evaluator Controls. Goal 2 Hook's 100 deterministic cases are reported separately because a deterministic case is not a Control. The unavailable Multi-Agent preflight is not counted as verified. |

## Non-experiment verification

CI pytest runs, local pytest runs, Claims validation, secret scans, `git diff --check`, workflow preflights, configuration validation, dry-runs, and dataset/materialization checks are software or evidence verification. They must not be added to any formal Instance, Trial, Attempt, Session, or Control count above.

Artifact identities, hashes, retention, and audit status are tracked in [EVALUATION_ARTIFACT_INDEX.md](EVALUATION_ARTIFACT_INDEX.md).

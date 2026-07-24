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
| V2.1-CONTROL-CANARY | Evaluation V2.1 | Two historical resolved Goal 4 controls | Instance | 2 | 2 | 2 | 2 | Bailian | `qwen3.7-max-2026-06-08` | CNY 7.412724 actual | complete control canary | 2/2 Candidate, scorable, and resolved | This validates the corrected V2 real execution chain on historical resolved controls only. It is not a 20-task result and must not be generalized to the full Goal 4 matrix. |
| V2-FULL20-DISPATCH-REGRESSION | Evaluation V2 | Full-20 paid replay dispatch coverage failure | Attempt | 20 planned | 20 rows | 1 aborted system run | 3 diagnostic rows only | Bailian | `qwen3.7-max-2026-06-08` | CNY 21.910536 actual | historical engineering evidence | 3/20 Agent/Provider execution coverage; 17 post-dispatch host-runtime import failures misclassified as `agent_no_candidate` | `FULL_20_SYSTEM_RUN_COMPLETE`; `MODEL_EXECUTION_COVERAGE_3_OF_20`; `INVALID_FOR_FULL_20_MODEL_SCORE_COMPARISON`; `PAID_DISPATCH_COVERAGE_REGRESSION`. It is excluded from every formal V2 20-task score. |
| V2-FULL20-DISK-PREFLIGHT | Evaluation V2 | Full-20 pre-transport disk exhaustion | Attempt | 20 planned | 0 paid task rows | 1 preflight-only run | 0 | none sent | `qwen3.7-max-2026-06-08` not started | CNY 0 | historical engineering evidence | 19/20 environments ready; InstructLab editable install exhausted runner disk before Agent startup | Actions `30095930961`, Artifact `8597955317`; Provider requests/usage/charge `0/0/0`; excluded from every formal V2 score. |
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

## Evaluation V2.1 Control Canary evidence

The completed paid Control Canary is bound to Actions run `30031616048`, internal
Run ID `v2-control-canary-fc5dbd1-20260723T172932Z`, Artifact ID `8573855537`,
and Artifact digest `sha256:d67537436c144129413048dbbf2ce5ac6f4b80ed25d3f8c492744ed98f33b71d`.
It used Freeze `28fbadb0fe0b2f3df24f7cb061a55da1c669afe36d3eece2e9e2b40446a117d5`,
runtime `eb15c230c2033373ae7f0768a51f8743c9fd61acbe78b610b6c38bea0e7d015b`,
system instruction `f43af3f8b81f58b2970571afb86f69641caf0da49c191cffe4026cf732abd36c`,
pricing `a09eb6e6955b9fb68d3e011771c948f7a14b7bbca5316a2433cab099d0b643d3`,
and payload manifest `fc65e40463d44937f851e23f3753359089413c21e5a5c0e7d7c09f79880c5410`.

`beetbox__beets-5495` resolved with 32 requests, CNY `5.498304`, Candidate/diff
SHA `7e9309fafab4a9a15c81d82c93722c5460f78953a8472118ab783f5fc41516a1`, and
official report SHA `6f1b08105b850b3af88e1e95a35d38177ac0f93f2d953f8a108a84e370af9edd`.
`beancount__beancount-931` resolved with 14 requests, CNY `1.914420`,
Candidate/diff SHA `47304e229ec105a1a39237df649cfb61a4e33f3306498a13d35e31ab0bc9a28e`,
and official report SHA `ec42a2912a8b494a5aaf29952bd8eed82916fbdfa6b029726eac34860b7fb274`.
Totals were 46 requests, 571,320 input tokens, 15,469 output tokens, CNY
`7.412724`, 46 settlements, and `active_reservation=null`. The resulting V2.2
gate was `V2_2_DIAGNOSTIC_PILOT_GO`; it did not start V2.2 automatically.

## Evaluation V2 full-20 dispatch regression evidence

Actions run `30076531565`, internal Run ID
`v2-full20-paid-20260724t07441784879084z-44939c21`, and Artifact `8591549476`
are immutable engineering evidence, not a formal Evaluation V2 score. Under
Freeze `dc617a2b4a07f81f5548375cb548c2beb925fb3b5c31a212bb41002c58a78715`
and Runtime `3d77584fa9730e92a1fb0cce0ee7f23e4dd99d29173d2d6ea3621c1cafd32a0e`,
three tasks executed the Agent and Provider (95 requests, CNY `21.910536`). The
third task's Agent Bash command ran `pip install -e .` against the host runtime,
downgrading `openai`; the next 17 Agent subprocesses failed importing
`AsyncOpenAI` before client construction. The shared stderr SHA was
`a865771dba9a11e91af9d74d8a5b06e071a0ce263a10838652c5a50ec7bc2684`.

The former empty-patch-first result path recorded those 17 dispatch failures as
`Agent/Provider/Runner=completed` plus `agent_no_candidate`. Consequently,
`0/20 scorable` and `0/20 resolved` are not model capability results. The run
is retained as `FULL_20_SYSTEM_RUN_COMPLETE`, `MODEL_EXECUTION_COVERAGE_3_OF_20`,
`INVALID_FOR_FULL_20_MODEL_SCORE_COMPARISON`, and
`PAID_DISPATCH_COVERAGE_REGRESSION`; its cost is isolated historical debugging
cost and cannot be combined with a future Runtime's results.

Actions run `30095930961` is a separate pre-transport engineering failure. Its
full-20 preflight reached 19/20 ready, then the InstructLab task's bare editable
install selected the complete PyTorch CUDA dependency stack and failed with
`Errno 28` before the Agent or paid runner started. Artifact `8597955317` has
digest `sha256:d521dcc1596ce9cd2883c1dbe52f1d6d0f4399f352b8f115dae865acc5594c20`.
It contains preflight evidence only: Provider requests, usage, and charge are
all zero, and there are no paid task results. The repository's own InstructLab
`tox.ini` defines the semantically equivalent unit-test environment as the CPU
extra plus the official PyTorch CPU index; the next Runtime uses that canonical
bootstrap and a fail-closed disk budget.

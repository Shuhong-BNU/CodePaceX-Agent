# V3_CORE Goal 4 Full-20 Final Report

Status: `COMPLETED_AND_ARCHIVED`

## Experiment Identity

| Field | Value |
| --- | --- |
| Experiment | `capability-v3-core-goal4-full-20` |
| Treatment | `V3_CORE` only, strictly serial |
| Actions Run / paid job | `32222241494` / `95974770532` |
| Internal Run ID | `v3-core-full20-paid-20260819t055458z-da345551` |
| Evaluated main | `9babb0a9a8e64985b9d22deed6d2115bd033b743` |
| Freeze SHA-256 | `64b766776ce47a933c009b58a8358d03c66a75a86353c366f8578452eed11d7f` |
| Allocation hash | `2cb6e45b1987f837393ceb3ff6bd364bea4bbd43be2a75301396bb190908b3f8` |
| Task-list / task-runs hashes | `1fbec3456386ad2a67035368a80c9406bfd6b1f67d205ced1de8d1e0b4664482` / `3e7068bb906b0aa4be1d7c5b09a397021baeb8e165d995a12f89e856a8484da8` |
| Runtime / pricing hashes | `6922dbd6b79995a8cf682138a7747692c78b09ee635eab37625709505e9998b0` / `a09eb6e6955b9fb68d3e011771c948f7a14b7bbca5316a2433cab099d0b643d3` |
| Artifact | `v3-core-full20-paid-v3-core-full20-paid-20260819t055458z-da345551-32222241494` (ID `9359493695`) |
| Artifact digest | `sha256:aee53248be15a2ecb3afc778bfeb41f7cfc1cc9bb5c6127070b809dcd5c34b21` |
| Provider / protocol / model | `bailian-qwen37-max` / `openai-compat` / `qwen3.7-max-2026-06-08` |
| Region / workspace | `cn-beijing` / `ws-e0dfiat7lu57en5y` |
| Evaluator | SWE-bench-Live `ad79b850f15e33992e96f03f6e97f05ddf9aa0be`, `SWE-bench-Live/SWE-bench-Live`, split `lite`, namespace `starryzhang` |

## Artifact Audit

Artifact audit passed. The downloaded archive digest matches the recorded
Artifact digest and its Freeze matches the authorized Freeze SHA. The frozen
task list and task runs each contain 20 unique canonical Goal 4 tasks; every
run is `V3_CORE`. The paid summary contains 20 terminal results, and its
allocation, runtime, pricing, task-list, and task-runs identities match the
Freeze. The ledger records 491 requests and settlements, is closed, and has
`active_reservation=null` with zero budget blocks.

## Authorization Boundary

This record covers the one explicitly authorized V3_CORE-only full-20 execution.
The workflow completed successfully. `controlled-pilot-paid-execution`,
`v3-core-tail-completion-paid-execution`, and generic `paid-execution` were
all skipped. No retry, second dispatch, failed-task rerun, tail continuation,
model change, provider change, or budget increase occurred.

## Final Results

| Measure | Result |
| --- | ---: |
| Tasks started / terminal / with Provider requests | 20 / 20 / 20 |
| Candidate / scorable / not scorable | 19 / 19 / 1 |
| Resolved / scorable unresolved | 5 / 14 |
| Raw fixed-20 resolved rate | 5/20 (25%) |
| Provider requests / settlements | 491 / 491 |
| Input / output / reasoning tokens | 11,434,096 / 214,242 / 116,222 |
| Frozen reference cost / ledger spent | CNY 144.921864 / CNY 144.921864 |
| Budget blocks / active reservation | 0 / `null` |

The ledger is closed. All 20 task-run entries record a live executor, Provider
client initialization, a model response, and a closed active reservation.
`cyclotruc__gitingest-134` is the sole not-scorable task because it produced no
Candidate; its evaluator was not run.

## Per-Task Comparison

The Goal 4 values below are the selected scorable terminal facts from the
immutable historical Artifact summarized in [GOAL4_EVIDENCE_INDEX.md](GOAL4_EVIDENCE_INDEX.md).
Historical per-task cost is selected terminal Trial cost; it does not sum to
Goal 4's CNY 165.044424 verified actual cost because that total also includes
historical failed/recovery Attempt cost. Current values are from Artifact
`9359493695`.

| Instance | Goal 4 outcome | Goal 4 req / CNY | V3_CORE outcome | V3 req / CNY | Candidate | Outcome transition | Req delta | Cost delta |
| --- | --- | ---: | --- | ---: | --- | --- | ---: | ---: |
| aws-cloudformation__cfn-lint-3749 | unresolved | 29 / 6.152004 | unresolved (ceiling) | 40 / 10.056648 | yes | unresolved -> unresolved | +11 | +3.904644 |
| aws-cloudformation__cfn-lint-3764 | unresolved | 15 / 1.468248 | unresolved | 14 / 1.958292 | yes | unresolved -> unresolved | -1 | +0.490044 |
| beetbox__beets-5457 | unresolved | 38 / 13.291632 | unresolved | 31 / 9.403104 | yes | unresolved -> unresolved | -7 | -3.888528 |
| beetbox__beets-5495 | resolved | 11 / 1.010196 | resolved | 32 / 5.062488 | yes | resolved -> resolved | +21 | +4.052292 |
| deepset-ai__haystack-8489 | unresolved | 24 / 5.881188 | resolved | 34 / 14.669328 | yes | unresolved -> resolved | +10 | +8.788140 |
| beancount__beancount-931 | resolved | 21 / 3.297720 | resolved | 17 / 3.143904 | yes | resolved -> resolved | -4 | -0.153816 |
| beeware__briefcase-2075 | resolved | 12 / 1.230300 | resolved | 17 / 2.302116 | yes | resolved -> resolved | +5 | +1.071816 |
| beeware__briefcase-2085 | resolved | 16 / 2.435436 | resolved | 20 / 3.622992 | yes | resolved -> resolved | +4 | +1.187556 |
| bridgecrewio__checkov-6893 | unresolved | 23 / 4.013244 | unresolved | 35 / 7.216272 | yes | unresolved -> unresolved | +12 | +3.203028 |
| bridgecrewio__checkov-6895 | unresolved | 17 / 1.726740 | unresolved | 11 / 1.014132 | yes | unresolved -> unresolved | -6 | -0.712608 |
| conan-io__conan-17092 | unresolved | 40 / 22.084068 | unresolved | 34 / 13.094388 | yes | unresolved -> unresolved | -6 | -8.989680 |
| conan-io__conan-17102 | unresolved | 40 / 13.789632 | unresolved (ceiling) | 40 / 15.741264 | yes | unresolved -> unresolved | 0 | +1.951632 |
| cyclotruc__gitingest-115 | unresolved | 12 / 1.734444 | unresolved | 11 / 1.275048 | yes | unresolved -> unresolved | -1 | -0.459396 |
| cyclotruc__gitingest-134 | unresolved | 14 / 2.452788 | not scorable | 8 / 1.148184 | no | historical scorable -> current not-scorable | -6 | -1.304604 |
| deepset-ai__haystack-8525 | unresolved | 18 / 3.193740 | unresolved | 10 / 1.528632 | yes | unresolved -> unresolved | -8 | -1.665108 |
| delgan__loguru-1297 | unresolved | 12 / 1.475676 | unresolved | 13 / 2.434236 | yes | unresolved -> unresolved | +1 | +0.958560 |
| delgan__loguru-1306 | unresolved | 12 / 1.927308 | unresolved | 7 / 0.858504 | yes | unresolved -> unresolved | -5 | -1.068804 |
| dynaconf__dynaconf-1225 | unresolved | 40 / 15.279192 | unresolved (ceiling) | 40 / 22.007808 | yes | unresolved -> unresolved | 0 | +6.728616 |
| dynaconf__dynaconf-1249 | unresolved | 35 / 16.647576 | unresolved (ceiling) | 40 / 15.474684 | yes | unresolved -> unresolved | +5 | -1.172892 |
| instructlab__instructlab-2540 | unresolved | 37 / 11.794560 | unresolved | 37 / 12.909840 | yes | unresolved -> unresolved | 0 | +1.115280 |

## V3 Activation Evidence

The formal Artifact contains 20 V3 run configurations and completions. Its raw
controller event logs record: `AdviceGenerated` 495, `AdviceInjected` 495,
`AdvicePresentInRequest` 495, `ToolEvidenceObserved` 392,
`EvidenceCollected` 20, `ImpactSliceBuilt` 92,
`CandidateSnapshotCreated` 92, `CandidateSelectionEvaluated` 19,
`CandidateRestored` 19, `TestSliceRecommended` 92, `FinalizationStarted` 20,
and `BudgetPhaseChanged` 495. Conditional mechanisms are reported as observed
events rather than requirements for every task.

## Historical Comparison and Claim Boundary

| Measure | Historical Goal 4 | Current V3_CORE | Difference |
| --- | ---: | ---: | ---: |
| Resolved fixed-20 tasks | 4/20 (20%) | 5/20 (25%) | +1, +5 percentage points |
| Requests | 537 | 491 | -46 (-8.566108%) |
| Verified/reference cost | CNY 165.044424 | CNY 144.921864 | -CNY 20.122560 (-12.192208%) |

The raw fixed-20 resolved count is one higher and the raw relative increase is
25%. This is a **historical comparison only**, not a contemporaneous paired
causal A/B result. The account/workspace differs from historical Goal 4, and
the historical matrix includes distinct execution timing and recovery attempts.
The request and cost differences are observed accounting differences and are
not attributed to V3_CORE.

## Remaining Failures

The request-ceiling terminal tasks are
`aws-cloudformation__cfn-lint-3749`, `conan-io__conan-17102`,
`dynaconf__dynaconf-1225`, and `dynaconf__dynaconf-1249`. They are archived as
task facts only; no retry or rerun is authorized by this report.

`cyclotruc__gitingest-134` is current not-scorable: eight Provider requests,
no Candidate, and evaluator `not_run`. It is not counted as resolved or
scorable unresolved.

## Next Research Questions

1. What Artifact-supported factors explain each request-ceiling terminal?
2. Why did `cyclotruc__gitingest-134` fail to export a Candidate despite eight
   Provider requests?
3. Which observed V3 mechanisms correlate with task outcomes under a future,
   separately authorized controlled design?

These questions do not authorize a retry, new paid run, continuation, or Agent
change.

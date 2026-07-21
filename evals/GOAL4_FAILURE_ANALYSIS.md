# Goal 4 unresolved failure analysis

## Status and evidence boundary

This is a zero-provider post-hoc analysis of the 16 unresolved instances in the accepted Goal 4 run. It is not a new formal experiment, does not rerun a Trial or evaluator, and does not change the accepted result of 4 resolved / 16 unresolved out of 20 scorable instances.

The immutable fact source is Artifact `goal4-request-ceiling-recovery-evidence-29830820618` (ID `8496125148`, archive SHA-256 `8b9309a9ee03b068bf96e69afd50ecc2c18e4a70046dc1ae99359310dc70c6c8`). Repository-level identities and accounting are cross-checked against [GOAL4_FINAL_REPORT.md](GOAL4_FINAL_REPORT.md), [GOAL4_EVIDENCE_INDEX.md](GOAL4_EVIDENCE_INDEX.md), and [claims.goal4.json](claims.goal4.json). Per-instance attribution uses only the selected terminal Trial's prediction, non-empty patch, stdout trace, Usage/accounting record, and official evaluator report. Historical infrastructure retries are not new benchmark instances.

The official evaluator report supplies the observed failure surface: remaining `FAIL_TO_PASS` failures and `PASS_TO_PASS` failures. The primary and secondary attributions below are causal interpretations of the preserved trace, not new evaluator labels. Where the two Checkov Trials share the same two unrelated `PASS_TO_PASS` failures, the regression signal is retained but causal confidence is reduced for `bridgecrewio__checkov-6895`. No instance lacks its selected terminal trace, patch, or evaluator report; therefore no whole row is marked `evidence_insufficient`. Missing or non-executed validation steps remain explicit.

## Method

1. Reconcile the 16 unresolved IDs, bucket, batch, requests, and selected terminal cost with the Evidence Index.
2. Count modified files from `diff --git` entries and verify every selected prediction has a non-empty patch.
3. Read the trace in order to determine whether the reported behavior was reproduced before the first edit, whether an issue-targeted test or assertion ran, and whether broader relevant local tests ran.
4. Read the official evaluator report without redefining resolved/unresolved. Any `PASS_TO_PASS.failure` entry is recorded as an observed regression signal.
5. Assign exactly one primary attribution from the frozen taxonomy. Reaching 40 requests is only primary when the trace shows convergence interrupted by the ceiling.

The machine-readable record, including the complete Artifact-internal evidence locations, is [goal4_failure_taxonomy.csv](goal4_failure_taxonomy.csv).

## Per-instance summaries

| Instance | Bucket / batch | Requests / cost | Patch files | Reproduced before edit | Target / related tests | Evaluator failure | Regression | Ceiling | Primary / secondary | Confidence |
| --- | --- | ---: | ---: | --- | --- | --- | --- | --- | --- | --- |
| `aws-cloudformation__cfn-lint-3749` | one_file / A | 29 / 6.152004 | 1 | yes | yes / yes | F2P 0/3 | no | no | root-cause localization / reasoning-implementation | high |
| `aws-cloudformation__cfn-lint-3764` | one_file / A | 15 / 1.468248 | 1 | no | yes / yes | F2P 1/1; P2P 4 failed | yes | no | regression / reproduction missing | high |
| `beetbox__beets-5457` | two_to_four_files / A | 38 / 13.291632 | 1 | partial: HTTP 403 | yes / yes | F2P 0/2 | no | no | root-cause localization / incomplete patch | high |
| `deepset-ai__haystack-8489` | five_plus_files / A | 24 / 5.881188 | 6 | no | yes / yes | F2P 4/0; P2P 5 failed | yes | no | regression / reproduction missing | high |
| `bridgecrewio__checkov-6893` | one_file / B | 23 / 4.013244 | 2 | no | yes / no | F2P 0/1; P2P 2 failed | yes | no | incomplete patch / test strategy | high |
| `bridgecrewio__checkov-6895` | one_file / B | 17 / 1.726740 | 3 | no | yes / no | F2P 1/0; P2P 2 failed | yes | no | regression / test strategy | medium |
| `conan-io__conan-17092` | two_to_four_files / B | 40 / 22.084068 | 4 | no | yes / yes | F2P 1/5; P2P 2 failed | yes | yes | incomplete patch / test strategy | high |
| `conan-io__conan-17102` | two_to_four_files / B | 40 / 13.789632 | 2 | no | no / no | F2P 0/1 | no | yes | incomplete patch / test strategy | high |
| `cyclotruc__gitingest-115` | one_file / B | 12 / 1.734444 | 1 | no | yes / yes | F2P 0/1 | no | no | incomplete patch / reproduction missing | high |
| `cyclotruc__gitingest-134` | one_file / B | 14 / 2.452788 | 2 | no | yes / yes | F2P 0/2; P2P 1 failed | yes | no | regression / incomplete patch | high |
| `deepset-ai__haystack-8525` | two_to_four_files / B | 18 / 3.193740 | 2 | no | yes / yes | F2P 0/2 | no | no | incomplete patch / root-cause localization | high |
| `delgan__loguru-1297` | two_to_four_files / B | 12 / 1.475676 | 1 | no | yes / yes | F2P 2/2 | no | no | root-cause localization / reproduction missing | high |
| `delgan__loguru-1306` | one_file / B | 12 / 1.927308 | 1 | no | yes / yes | F2P 5/5 | no | no | incomplete patch / reproduction missing | high |
| `dynaconf__dynaconf-1225` | five_plus_files / B | 40 / 15.279192 | 2 | no | no / yes | F2P 0/5; P2P 25 failed | yes | yes | request ceiling / cross-file propagation | high |
| `dynaconf__dynaconf-1249` | five_plus_files / B | 35 / 16.647576 | 3 | no | yes / yes | F2P 0/1 | no | no | incomplete patch / cross-file propagation | high |
| `instructlab__instructlab-2540` | five_plus_files / B | 37 / 11.794560 | 3 | no | yes / yes | F2P 0/3 | no | no | cross-file propagation / incomplete patch | high |

`F2P passed/failed` and `P2P failed` counts above come directly from the official evaluator reports. All 16 patches are non-empty. `target` includes an exact issue-specific test or executable assertion; `related` means an additional relevant local slice beyond that target.

### Attribution notes

- `cfn-lint-3749` reproduced the bug before editing, but the patch guarded non-string keys rather than repairing AccountId-backed intrinsic resolution; all three target failures remained.
- `cfn-lint-3764` edited before reproducing both empty-collection paths. One target passed, one remained, and four P2P tests failed after the control-flow change.
- `beets-5457` observed an HTTP 403 and irrelevant search output, but treated the URL shape as the root cause. The preserved parser fixtures still failed both expected cases.
- `haystack-8489` passed all four F2P tests but broke five Datadog P2P tests. The cross-tracer API change lacked a pre-edit concurrent-span reproduction and compatibility guard.
- `checkov-6893` changed both policy and expected output without first establishing graph-DSL behavior; the exact YAML policy test still failed. `checkov-6895` passed its F2P test, but the official report retained two P2P failures also seen in the adjacent Checkov Trial, so patch causality is only medium confidence.
- `conan-17092` covered several C++26 surfaces but missed five toolchain/flag expectations and caused two P2P failures. Its 40th request ended during dependency/test setup, but the evidence shows an incomplete matrix rather than a cleanly converged fix. `conan-17102` also reached 40, yet ran no test at all; its ceiling is an observed condition, not the primary cause.
- `gitingest-115` validated a hand-built mixed-case example only after editing and still failed the evaluator's mixed-case contract. `gitingest-134` added its own tests, but the official host-agnostic matrix exposed two target failures and one regression.
- `haystack-8525` equated a CSV row with a newline and changed an existing expectation; both official row-splitting cases failed. `loguru-1297` simulated an invalid offset after editing rather than the actual `localtime()` exception paths; two exception variants remained. `loguru-1306` implemented only environment-variable presence and missed five FORCE_COLOR/NO_COLOR precedence cases.
- `dynaconf-1225` is the only primary `request_ceiling_exhausted` case. The trace found the upstream multi-file change and began porting it, but the 40-request stop left only 2 of the 17 gold-scope files represented, with 5 F2P and 25 P2P failures. `dynaconf-1249` exercised a custom happy path but missed the existing decorated-hooks contract.
- `instructlab-2540` propagated temperature through three files, but the 25-file gold scope included additional configuration/default surfaces; all three configuration F2P tests failed.

## Primary attribution frequency

Only primary attribution is counted here; secondary labels are not double-counted.

| Primary attribution | Count | Share of 16 |
| --- | ---: | ---: |
| `incomplete_patch` | 7 | 43.75% |
| `regression_introduced` | 4 | 25.00% |
| `root_cause_localization_failure` | 3 | 18.75% |
| `cross_file_propagation_missed` | 1 | 6.25% |
| `request_ceiling_exhausted` | 1 | 6.25% |
| All other allowed primary categories | 0 | 0% |

## Bucket differences

| Bucket | Unresolved | Primary attribution distribution | Reproduced before edit | Regression signal | Reached 40 requests |
| --- | ---: | --- | ---: | ---: | ---: |
| one_file | 7 | incomplete 3; regression 3; localization 1 | 1/7 | 4/7 | 0/7 |
| two_to_four_files | 5 | incomplete 3; localization 2 | 0/5 full, 1/5 partial | 1/5 | 2/5 |
| five_plus_files | 4 | regression 1; incomplete 1; cross-file 1; ceiling 1 | 0/4 | 2/4 | 1/4 |

One-file failures were not uniformly easy: six of seven edited before a preserved reproduction, and three produced regression-class outcomes. Two-to-four-file failures concentrated on incomplete implementation even when a local target slice ran. Five-plus-file tasks showed the broadest modes and no resolved instance in Goal 4; cross-interface compatibility, propagation, and request budgeting matter more as scope grows.

## Requests and failure type

| Selected terminal requests | Instances | Primary attribution distribution |
| --- | ---: | --- |
| 12-19 | 7 | incomplete 3; regression 3; localization 1 |
| 20-39 | 6 | localization 2; incomplete 2; regression 1; cross-file 1 |
| 40 | 3 | incomplete 2; request ceiling 1 |

Request count alone does not explain failure. Seven Trials failed below 20 requests, mostly through incomplete patches or regressions. At exactly 40, `conan-17092` still had a partial behavior matrix and `conan-17102` had not executed a test; only `dynaconf-1225` showed a discovered upstream solution being actively ported when the ceiling stopped progress.

## Highest-priority capability gaps

1. **Patch completeness with contract inventory.** Seven primary incomplete patches plus the cross-file case show that the Agent needs an explicit pre-edit inventory of behavior surfaces, callers, configuration/default artifacts, and exact target tests. The completion gate should reject a final answer while a named target is unexecuted or still failing.
2. **Regression-aware validation.** Four primary regression outcomes, including two with all F2P tests passing, show that focused success is insufficient. The Agent should select a stable, related P2P slice from touched interfaces and compare failures against the pre-edit baseline before finalizing.

Reproduction-driven localization supports both priorities: only one Trial had a full pre-edit reproduction, and one had a partial network observation. That gap should be corrected inside both capabilities rather than treated as a third independent workstream.

## Recommended Stage B scope

Stage B should remain a code-and-offline-replay scope until reviewed. Implement: (a) a reproduction-before-edit checkpoint with an explicit exception reason, (b) a contract inventory generated from touched symbols/config surfaces, (c) a target-test completion gate, (d) pre/post comparison for a bounded regression slice, and (e) request-budget checkpoints at 20/30/36 that force scope reconciliation. Validate these mechanisms with deterministic fixtures and preserved traces only. Do not start a Provider comparison, alter evaluator semantics, or rerun any Goal 4 instance as part of Stage B.

## Recommended Stage C tasks

These five existing unresolved tasks form a diagnostic panel; this is a recommendation only, not authorization to run them.

| Instance | Why selected |
| --- | --- |
| `aws-cloudformation__cfn-lint-3749` | one-file localization despite a successful pre-edit reproduction |
| `bridgecrewio__checkov-6895` | F2P success with a P2P regression signal; tests regression-aware finalization |
| `conan-io__conan-17092` | multi-surface incomplete implementation at 40 requests without treating the ceiling as automatic cause |
| `dynaconf__dynaconf-1225` | genuine ceiling-interrupted, high-scope upstream port |
| `instructlab__instructlab-2540` | configuration propagation across a 25-file gold scope |

Any future Stage C authorization should preserve the original instance definitions and official evaluator, report distinct instances and attempts separately, and exclude infrastructure retries from the benchmark denominator.

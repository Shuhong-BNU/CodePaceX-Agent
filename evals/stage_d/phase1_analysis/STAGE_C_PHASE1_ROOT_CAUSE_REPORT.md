# Stage C Phase 1 Root Cause Report

## Scope and Method

This is a zero-provider post-hoc analysis. It read the final Phase 1 Artifact, preserved first-task and continuation Artifacts, their JSONL traces, permissions, validation telemetry, predictions, evaluator reports, Usage and ledger records, plus the Goal 4 final Artifact and repository documentation. It did not dispatch a workflow, invoke a Provider, rerun an evaluator, read a gold patch, or modify a historical result.

The six Stage C outcomes are formal empty-patch unresolved terminals. The official evaluator reports `empty_patch_instances`; the Phase runner records those terminals as `unresolved`, which is the frozen Stage C accounting rule.

## Direct Evidence

1. All six Stage C predictions contain `model_patch: ""`; all six evaluator reports identify the corresponding instance as an empty patch.
2. Permission telemetry records 80 denied calls across six tasks: 52 Bash, 20 EditFile, and 8 WriteFile. No Stage C EditFile or WriteFile executed.
3. Every task emitted the request 20, 30, and 36 checkpoint as `pending`. No task acknowledged any checkpoint successfully.
4. No task recorded a valid reproduction declaration or complete contract inventory. The common rejection was `declaration must reference an observed tool result`; the reference must be an exact tool-call id, but the exposed tool description provides only a free-form `payload` object.
5. The classifier marks compound test commands (`cd ... && pytest ...`, output pipes, redirection) as `unknown_side_effect` or implementation writes. The reproduction gate blocks those commands before they can create admissible test evidence. The only executed Stage C Bash commands were read-only grep, ls, or wc commands.
6. The Agent did locate relevant code: first relevant search/read occurred in request 1 for all six tasks. First edit attempts occurred at requests 9, 6, 37, 3, 11, and 8 respectively and were denied.
7. Goal 4 had executable write/test paths. Its two resolved controls executed edits and tests without denial: `beets-5495` had two edits at request 5 and `beancount-931` had edits at requests 6, 7, 9, and 19. Stage C had none.

## Why All Six Were Unresolved

The shared terminal path was: locate code -> attempt test/reproduction or edit -> validation/permission denial -> malformed ValidationCheckpoint declaration -> more reads or declarations -> pending checkpoints -> request ceiling. The Agent did not reach an actual edit-test-export loop. Thus the empty candidate is faithful to the workspace, but the workspace was prevented from becoming a meaningful solution attempt.

The evidence does not show a model-generated valid patch being discarded, reverted, or lost by export. It does show a model/tool-schema mismatch made the deterministic guard effectively impossible to satisfy in live use.

## Stage B Mechanisms In Practice

| Mechanism | Triggered? | Observed benefit | Observed cost |
| --- | --- | --- | --- |
| Reproduction-before-edit | Yes, all six | Prevented ungrounded writes | Blocked all writes and most test/reproducer commands before valid evidence existed |
| Contract inventory | Attempted, never complete | None demonstrated | Repeated malformed declarations consumed turns |
| Target-test completion | Never armed with valid obligations | None demonstrated | Target tests could not become fresh evidence |
| Regression comparison | One partial declaration only | None demonstrated | No valid baseline/post comparison occurred |
| Checkpoints 20/30/36 | Emitted for all six | Telemetry proves lack of convergence | Pending checkpoints added obligations without a successful recovery path |
| Completion gate | Not reached as finalization | Correctly did not certify success | Ceiling terminated sessions first |

## Competition Between Explanations

| Hypothesis | Assessment | Confidence | Evidence |
| --- | --- | --- | --- |
| A. Model cannot locate or modify the issues | Not primary | high | Relevant code was located on request 1; attempted edits followed |
| B. Reproduction gate is too strict in live use | Supported | high | Every implementation write was blocked while reproduction was unsatisfied |
| C. Inventory/validation loops consume budget | Supported | high | 11-21 checkpoint calls per task; no complete inventory |
| D. Checkpoints record but do not cause convergence | Supported | high | All 18 checkpoints remained pending |
| E. Completion gate lost valid work | Not supported | high | No valid edit existed and completion was not reached |
| F. Permission/sandbox blocked necessary work | Supported, contributing | high | 80 denials; zero executed edit/write calls |
| G. Candidate export lost a non-empty diff | Contradicted | high | No edit/write executed; prediction and evaluator agree |
| H. No final code modification existed | Supported | high | Telemetry and empty predictions agree |
| I. Prompt or telemetry overload distracted the Agent | Supported | medium-high | Repeated schema-invalid declarations and prose instead of exact references |
| J. 40 requests were insufficient | Secondary only | medium | The ceiling ended every task, but the blocked protocol consumed the budget |
| K. Environment/protocol difference caused control regressions | Supported | high | Stage B adds this gate/tool contract; Goal 4 did not have this path |
| L. Control regression is mostly random | Not primary | high | Both controls share the same zero-edit, gate-blocked path |

## Goal 4 Comparison

The four historical unresolved tasks were not improved: Goal 4 produced non-empty patches after 15-38 requests; Stage C produced no patch after 40 requests. The two historical resolved tasks regressed from edited/tested workspaces to empty patches. This is a negative paired diagnostic result, not a change to Goal 4's accepted 4 resolved / 16 unresolved result.

## Per-task Telemetry

`Read` below combines ReadFile, Glob, Grep, and ToolSearch. `Checkpoint` is
ValidationCheckpoint. These are executed calls, not attempted calls. No task
executed an EditFile, WriteFile, or a test command; no task had a fresh target
test or a regression comparison.

| Task | Executed Bash / Read / Checkpoint / Edit / Write | Denies | Valid reproduction / exception | Inventory revisions | Last ten request actions |
| --- | --- | ---: | --- | ---: | --- |
| cfn-lint-3749 | 0 / 16 / 11 / 0 / 0 | 17 | no / no | 0 | 31 Read; 32 Edit; 33 Checkpoint; 34 Edit; 35-36 Checkpoint; 37 search; 39-40 Checkpoint |
| cfn-lint-3764 | 0 / 11 / 17 / 0 / 0 | 14 | no / no | 0 | 31 Bash; 32-33 Checkpoint; 34 Edit; 35 Checkpoint; 36 Bash; 37-39 Checkpoint; 40 search |
| beets-5457 replacement | 2 / 34 / 3 / 0 / 0 | 8 | no / no | 0 | 31 Read; 32-35 search; 36 Read; 37 Edit; 38 Read; 39 Bash; 40 Checkpoint |
| beets-5495 | 0 / 9 / 21 / 0 / 0 | 13 | no / no | 0 | 31 Bash; 32 Checkpoint; 33 Bash; 34-36 Checkpoint; 37 Read; 38 two Edits; 39-40 Checkpoint |
| haystack-8489 | 2 / 21 / 13 / 0 / 0 | 15 | no / no | 0 | 31 Checkpoint; 32 Bash; 33 Checkpoint; 34 search; 35 Checkpoint; 36 Write; 37 Bash; 38 Checkpoint; 39 Bash; 40 Checkpoint |
| beancount-931 | 0 / 17 / 16 / 0 / 0 | 13 | no / no | 0 | 31 Read; 32-35 Checkpoint; 36 Bash; 37 Checkpoint; 38 Read; 39-40 Checkpoint |

For all six rows, checkpoint 20, 30, and 36 were emitted with state `pending`.
Their subsequent actions were further malformed declarations, searches, and
blocked tools; there was no valid acknowledgement or request-36 outcome choice.
The completion gate therefore was not the terminal reason: no no-tool final
answer reached it. The Agent ended through the 40-request ceiling.

## Ranked Root Causes and Stage D Proposal

| Priority | Root cause and minimal remedy | Evidence | Required tests | Contract effect |
| --- | --- | --- | --- | --- |
| P0 | Give ValidationCheckpoint typed fields for evidence reference, valid exception reasons, full inventory template, and an immediate-previous-tool binding helper. Return required fields and valid choices on rejection. | All six failed the reference/schema contract | Live-like fixture: reproduce -> declare -> edit -> fresh target/regression test -> completion | Runtime change; requires new freeze/review |
| P0 | Recognize safe `cd <workspace> && pytest ...` and output-capped test invocations as tests, or supply a side-effect-free test wrapper. | Test commands became unknown side effects | Compound-command, redirect, and write false-negative tests | Runtime change; requires new freeze/review |
| P1 | Make checkpoint remediation machine-readable and reduce unrelated required details before acknowledgement. | 18 pending checkpoints, no recovery | Preserved trace at ordinals 20/30/36 | Runtime behavior change |
| P1 | Verify `session_allow` produces the intended noninteractive policy separately from Stage B. | 80 permission denials | Child initialization with permitted edit and test | Environment change; re-freeze needed |
| P2 | Add first-read/edit/blocked-histogram/final-diff report fields. | Current telemetry needs manual joins | Recorder compatibility tests | Additive evidence only |

## Phase 2 Decision

`PHASE_2_NO_GO`. The evidence proves a common no-edit protocol failure and two historical resolved-control regressions. Continuing would mainly spend budget to reproduce this failure. A repaired runtime must not retroactively change these six records; any rerun would be a newly authorized experiment.

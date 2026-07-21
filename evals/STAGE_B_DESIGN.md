# Stage B Agent validation gates design

Status: Phase 1 audit and proposed design. No functional implementation exists on
this branch.

## 1. Evidence basis

The accepted Goal 4 evidence remains unchanged:

- final status: `GOAL4_ACCEPTED`;
- registered / attempted / completed / scorable: 20 / 20 / 20 / 20;
- resolved / unresolved: 4 / 16;
- selected terminal Provider requests: 537;
- per-instance request ceiling: 40; and
- immutable Artifact ID: `8496125148`.

[GOAL4_FAILURE_ANALYSIS.md](GOAL4_FAILURE_ANALYSIS.md) attributes the 16
unresolved instances primarily to seven incomplete patches, four regressions,
three localization failures, one missed cross-file propagation, and one genuine
request-ceiling exhaustion. Only one preserved Trial had a full pre-edit
reproduction. The two highest-priority capability gaps are patch completeness
with contract inventory and regression-aware validation.

This design uses those findings as requirements. It does not regrade an instance,
read a gold patch, or create a new formal result.

## 2. Runtime audit

### 2.1 Agent Loop entry points

The core interactive/event-driven entry is `Agent.run()` in
`codepacex/agent.py:731`, which delegates to `Agent._run()` at line 741. The main
loop begins at line 775. The TUI consumes it in `codepacex/app.py:1500`, and the
non-interactive CLI consumes it in `codepacex/__main__.py:416`.

There is a second loop, `Agent.run_to_completion()` at
`codepacex/agent.py:1780`. Background Agents, foreground sub-Agents, Team members,
and post-Team CLI continuation use this path. A Stage B implementation must cover
both loops; changing only the TUI or CLI adapter is bypassable.

### 2.2 Unified pre-tool control point

All current Agent tool paths call `Agent._assess_tool()` at
`codepacex/agent.py:1354` before `Tool.execute()`:

| Execution path | Dispatch | Actual execute |
| --- | --- | --- |
| streaming read-only | `_execute_single_tool_direct()` | `agent.py:1525` |
| parallel read-only | `_execute_batch_parallel()` | delegates to direct path |
| interactive sequential | `_execute_tool()` | `agent.py:1646` |
| non-interactive | `_execute_tool_noninteractive()` | `agent.py:2029` |

`_assess_tool()` is therefore the best existing common interception point. The
Stage B controller should run there after tool existence/enabled checks and before
Hooks or permission policy can authorize execution. Its denial must be an
unbypassable validation constraint, not a user-persistable permission decision.

The code still has three functions that call `Tool.execute()` directly. Tests
must assert that none can execute a side effect without a controller decision.

### 2.3 Bash side-effect classification

`Bash` declares `category = "command"` in `codepacex/tools/bash.py:94` and
executes through `asyncio.create_subprocess_shell()` at line 107. The current
`DangerousCommandDetector` in `codepacex/permissions/dangerous.py:134` detects a
small set of destructive operations. `is_safe_command()` at line 76 recognizes a
deliberately narrow read-only allowlist. These are security decisions, not a
complete read/write/test taxonomy.

Consequently, current code cannot deterministically distinguish a reproducer,
test command, build command, or arbitrary side effect. Stage B needs a separate
`BashCommandClassifier` with outcomes:

- `read_only`;
- `test`;
- `write`;
- `external_or_persistent`; and
- `opaque`.

The classifier should parse shell segments conservatively, retain the original
command, and return normalized command/test fingerprints. `opaque` is
write-capable for gate purposes. It must not weaken `DangerousCommandDetector` or
OS sandbox policy.

### 2.4 Test commands and results in the trace

The CLI emits every tool request as `tool_use`, including Bash arguments, at
`codepacex/__main__.py:426-434`. It emits output, error status, and elapsed time as
`tool_result` at lines 436-448. Thus a test command and its textual result already
exist in the raw stream-json trace.

`RunRecorder.capture_event()` in `evals/benchmark.py:372` currently derives
structured Usage, permission, compression, and runtime streams. It does not
derive structured test evidence. Goal 4 writes the complete child stdout as a
task Artifact (`evals/goal4_swe.py:753`) and then ingests selected event types via
`evals.pilot._ingest_trace()`.

Stage B should add structured validation events without removing raw stdout:

- tool ID and Agent ID;
- command fingerprint and classification;
- start/end ordering;
- exit code and timeout status;
- parsed framework and test IDs when a deterministic adapter recognizes them;
- result fingerprint; and
- the gate obligations satisfied or invalidated by that result.

`ToolResult.is_error` alone is insufficient for pre/post comparison. The proposed
implementation should add non-secret result metadata, including the raw process
exit code, without parsing arbitrary model prose.

### 2.5 Provider request counter and ceiling

`Agent._runtime_request_index` is process-local and is incremented for runtime
manifest events in `codepacex/agent.py:506-511`. Usage is emitted after each
response in `_run()` at lines 1049-1060 and in `run_to_completion()` at lines
1896-1914.

For paid experiments, the authoritative counter is the request ledger, not the
Agent iteration or local runtime index. `ProviderRequestBudget.reserve_before_request()`
in `evals/paid_gate.py:1133` counts existing request charges and blocks an
attempted request above the frozen limit. Goal 4 passes 40 as
`maximum_provider_requests_per_trial` in `evals/goal4_swe.py:739-745` and also
sets `--max-iterations 40` at lines 746-750. Neither limit may be increased.

The 20/30/36 checkpoints should be raised after the corresponding completed
request and before a side effect or final answer from that response is accepted.
For Multi-Agent execution, the ordinal must come from a shared Trial/session
counter. A child Agent's local request index can reset to one and is not safe as
the only source.

### 2.6 Final-answer decision

In `_run()`, a response with no tool calls is accepted at
`codepacex/agent.py:1110`; `LoopComplete` is emitted at line 1132. In
`run_to_completion()`, the equivalent branch is at line 1930 and returns the last
text at line 1984. The CLI emits its terminal `result` only after `LoopComplete`
at `codepacex/__main__.py:515-530`.

A Stage B finalization guard must run at both no-tool-call branches before
terminal state, file-history snapshot, `session_end`, `LoopComplete`, or return.
When blocked, it should emit a structured `validation_blocked` event, add a
deterministic remediation reminder, and continue if request budget remains.
Already streamed text is non-terminal draft output; UIs must not label it as a
completed answer.

### 2.7 Child Agent, Team, and Coordinator bypass analysis

Current child creation constructs new `Agent` objects in several places:

- ordinary/forked sub-Agents: `codepacex/tools/agent_tool.py:356`;
- Team members: `codepacex/tools/agent_tool.py:575`;
- isolated worktree Agents: `codepacex/tools/agent_tool.py:774`;
- forked Skills: `codepacex/skills/executor.py:103`; and
- top-level CLI, TUI, and remote assemblers.

The Agent tool propagates the experiment profile and builds child permission
checkers, but it does not propagate any Stage B validation state because that
state does not yet exist. Forked Skills are more exposed: they construct a new
Agent with `permission_checker=None` at `codepacex/skills/executor.py:109`.
Because `_assess_tool()` defaults to allow when no checker exists, permission
state alone cannot implement Stage B.

In-process Team members call `run_to_completion()` through `TaskManager`.
Terminal-pane teammates may run in another process and worktree. The Coordinator
is Prompt-defined and can delegate edits; it is not an enforcement boundary.

Proposed rule: every Agent gets a required `ValidationController` reference. The
disabled controller is a no-op. Enabled in-process children share a run-scoped
state object; worktree children receive a derived workspace scope; separate
process teammates use a run-scoped, lock-protected state file or equivalent IPC.
The root cannot finalize while a child scope that can affect its deliverable has
unresolved obligations. Coordinator and bypass permission modes do not override
validation decisions.

### 2.8 Artifact, JSONL, and report extension points

`RunRecorder.event()` writes the canonical `events.jsonl` stream
(`evals/benchmark.py:309`). `capture_event()` is the structured derivation point.
Optional stream names are closed at `evals/benchmark.py:33`, Artifact names at
line 36, and task Artifact kinds at line 37. `RunRecorder.finalize()` writes
`result.json` and `report.md` at lines 578-638.

The proposed additive schema is:

- canonical events in `events.jsonl`: `validation_declaration`,
  `validation_observation`, `validation_checkpoint`, `validation_blocked`, and
  `validation_finalized`;
- optional `validation-events.jsonl`, derived by `capture_event()`;
- optional `validation-summary.json`, containing counts and terminal obligation
  state; and
- a bounded validation section in `report.md` generated from the summary.

Historical readers must tolerate absence of these files. Stage B must never
rewrite an old Run directory. Trace replay writes to a new local output directory
with source provenance and labels itself `offline_replay`, not a Trial.

## 3. Proposed architecture

### 3.1 Typed controller and state

Introduce a core module with no dependency on `evals`:

```text
ValidationProfile
  enabled, schema_version, mode, checkpoint_ordinals

ValidationController
  assess_tool(agent_id, workspace_id, tool, arguments)
  observe_tool_start(...)
  observe_tool_result(...)
  observe_request_completed(global_ordinal, ...)
  assess_finalization(agent_id, workspace_id)
  emit_event(...)

ValidationSessionState
  workspaces
  declarations
  observations
  obligations
  request_checkpoints
  child_scopes
```

State transitions are append-only and event-derived. Mutating a declaration
creates a new version; it does not edit prior history. Loss of enabled state is a
blocking infrastructure error.

### 3.2 Structured checkpoint tool

Add an enabled-profile-only system tool, tentatively `ValidationCheckpoint`, with
typed actions:

- `record_reproduction`;
- `declare_contract_inventory`;
- `amend_contract_inventory`;
- `declare_target_tests`;
- `declare_regression_slice`; and
- `ack_request_checkpoint`.

The tool records declarations and references observed tool IDs. It performs no
filesystem or network action. It must not accept gold-patch content. Free-form
assistant text cannot substitute for this tool.

## 4. Mechanism designs

### 4.1 Reproduction-before-edit gate

State starts as `reproduction_required`. Before the first side-effecting tool,
the controller requires either:

1. a `record_reproduction` declaration referencing an observed pre-edit command
   and result; or
2. a typed exception with a reason code such as `environment_unavailable`,
   `non_executable_specification`, or `reproduction_is_destructive`.

Deterministic code verifies temporal order, tool identity, workspace identity,
command fingerprint, and that no prior write occurred. It verifies that an
exception reason is from the closed set and records the limitation. Prompt logic
chooses a meaningful reproducer and explains why its observed behavior matches
the issue. The controller cannot prove that semantic match from prose alone.

`WriteFile`, `EditFile`, mutating Bash, opaque command tools, write-capable MCP,
Skill installation, and child worktree integration are side effects. Plan-file
writes in Plan Mode are explicitly exempt until implementation begins.

### 4.2 Contract inventory

Before the first implementation edit, the Agent declares:

- behavior clauses;
- affected symbols or configuration keys;
- known callers/consumers;
- expected files or file patterns;
- target tests; and
- a bounded regression slice.

Code enforces schema, non-empty target behavior, path normalization, declaration
versioning, and links between target tests and observed commands. Every touched
path must be covered by an inventory entry or an amendment recorded before a
subsequent edit. A changed symbol/config surface invalidates relevant prior test
completion.

Prompt assistance may discover callers and propose scope. Deterministic code
must not claim the inventory is semantically complete; it enforces that the Agent
made, maintained, and validated an explicit contract.

### 4.3 Target-test completion gate

Each declared target test has a normalized fingerprint and state:
`declared -> observed_running -> passed|failed|invalidated`.

A target test is `passed` only when:

- the matching Bash tool actually executed;
- the execution occurred after the latest relevant edit;
- exit code is zero;
- a recognized adapter does not report target failures; and
- the result belongs to the same workspace and validation scope.

A timeout, permission denial, missing executable, unmatched custom assertion, or
free-form statement is not a pass. Finalization is blocked while any required
target is missing, failed, or invalidated. Prompt text may recommend commands but
cannot mark them complete.

### 4.4 Pre/post regression comparison

The inventory declares a bounded regression command before implementation. The
same normalized command must run before the first relevant edit and after the
last relevant edit. Framework adapters produce stable outcome sets such as
`passed`, `failed`, `skipped`, and collection errors. Phase 2 should support
pytest first; unknown frameworks remain `opaque` until an adapter exists.

The deterministic comparison fails when the post set introduces a failure or
collection error not present in the baseline, loses required passing tests, or
cannot be compared. Existing baseline failures remain visible and are not called
new regressions. A typed, auditable exception may be allowed only for an
unavailable baseline and must block any claim of regression-free completion.

### 4.5 Request checkpoints at 20/30/36

Request progress is a shared Trial/session fact. At completed request ordinals 20,
30, and 36, the controller creates a pending checkpoint before accepting a side
effect or finalization from that response:

| Ordinal | Required deterministic acknowledgement |
| ---: | --- |
| 20 | reconcile reproduction, inventory, touched paths, and failing targets |
| 30 | freeze remaining scope; prioritize target and regression execution |
| 36 | stop scope expansion; provide a four-request completion plan |

Acknowledgement uses structured fields and current controller facts. Prompt
assistance summarizes options. Code blocks further writes and finalization until
the checkpoint is acknowledged. Read-only diagnosis remains available unless it
would trigger another Provider request beyond the existing budget.

These checkpoints never reserve requests, raise the ceiling, or convert a ceiling
hit into a task failure label. Request 41 remains blocked by the existing paid
gate. Offline tests simulate ordinals without contacting a Provider.

## 5. Deterministic enforcement versus Prompt assistance

| Concern | Deterministic code | Prompt assistance |
| --- | --- | --- |
| Temporal order | Tool/request/event sequence | Explain why order is useful |
| Side-effect classification | Conservative command/tool classifier | Suggest safer commands |
| Reproduction record | Require observed pre-edit tool/result reference | Choose and interpret reproducer |
| Inventory | Validate schema, versions, paths, touched-scope coverage | Discover behavior surfaces and callers |
| Target completion | Match actual post-edit command and passing result | Choose targeted tests |
| Regression | Compare parsed pre/post outcome sets | Select a representative bounded slice |
| Checkpoints | Raise at exact global ordinals and require acknowledgement | Reconcile scope and propose next steps |
| Finalization | Block terminal state with pending obligations | Draft remediation or final summary |
| Multi-Agent | Share state and aggregate child obligations | Delegate discovery/implementation |

Prompt instructions are defense in depth. Any requirement that changes whether a
write executes or an answer becomes terminal belongs in deterministic code.

## 6. Compatibility analysis

### Ordinary non-SWE tasks

Default-disabled behavior must be byte-for-byte compatible at the event-schema
boundary. When explicitly enabled for a non-code task, the profile should support
`validation_kind = none|code_change`; documentation-only or read-only work can
select `none`. Automatic task classification by an LLM must not silently enable or
disable enforcement.

### Plan Mode

Plan Mode currently permits only selected tools and its designated plan file.
Plan-file writes should not count as implementation edits. The plan may collect a
draft inventory, but enforcement begins when implementation mode is entered. The
`ExitPlanMode` early loop completion path at `codepacex/agent.py:1320-1323` must
not be mistaken for task finalization.

### Skills

Inline Skills reuse the parent Agent and naturally share the controller. Forked
Skills create a new Agent and currently omit the permission checker, so they must
explicitly inherit the validation controller and profile. Tool filtering may
remove `ValidationCheckpoint`; when validation is enabled it must be treated as a
system tool and retained, like other required system tools.

### MCP

`MCPToolWrapper` sets `category = "command"` at
`codepacex/mcp/tool_wrapper.py:77`. MCP schemas do not currently expose a trusted
read-only declaration. Stage B must treat MCP calls as opaque side effects unless
a server/tool definition is covered by an explicit deterministic capability
policy. Deferred loading through ToolSearch changes schema availability, not gate
coverage.

### Multi-Agent, Team, and Coordinator

In-process Agents share one thread-safe session state and use per-workspace
scopes. Isolated worktrees inherit the task obligations but track edits/tests in
their own workspace; integration into the parent invalidates parent post-edit
tests. Pane-backed teammates require file-backed or IPC state with locking.

The root finalization guard aggregates active child scopes. A child cannot clear
another child's obligation. Coordinator prompts and `bypassPermissions` affect
permission UX only; they cannot bypass Stage B. Background completion callbacks
must flush validation events before a child is marked complete.

## 7. Suggested implementation files

No files in this list are modified in Phase 1.

| File | Proposed change |
| --- | --- |
| `codepacex/validation.py` | Typed profile, state machine, decisions, events, shared state adapters |
| `codepacex/validation_commands.py` | Conservative Bash/test classification and fingerprints |
| `codepacex/tools/validation_checkpoint.py` | Structured declaration/checkpoint system tool |
| `codepacex/tools/base.py` | Add bounded non-secret execution metadata to `ToolResult` |
| `codepacex/tools/bash.py` | Preserve exit code/timeout metadata; call classifier inputs only |
| `codepacex/agent.py` | Common pre-tool and finalization guards; request observations |
| `codepacex/__main__.py` | Assemble opt-in controller and emit validation events |
| `codepacex/app.py` / `codepacex/remote.py` | Assemble disabled/enabled controller and render blocked-final state |
| `codepacex/tools/agent_tool.py` | Propagate shared/derived controller to every child path |
| `codepacex/skills/executor.py` | Propagate controller to forked Skills |
| `codepacex/experiments.py` | Versioned, hash-bound validation profile activation |
| `evals/benchmark.py` | Additive validation JSONL/summary/report support |
| `evals/pilot.py` | Ingest validation events from preserved/synthetic stream-json |
| `evals/stage_b_replay.py` | Zero-provider preserved-trace replay with provenance |
| `tests/test_validation.py` | State-machine and fail-closed unit tests |
| `tests/test_validation_commands.py` | Shell/test classification corpus |
| `tests/test_agent_validation.py` | Fake-client coverage of all execution/finalization paths |
| `tests/test_stage_b_replay.py` | Deterministic replay and schema compatibility tests |

No workflow file is needed for Stage B deterministic development.

## 8. Zero-provider test plan

1. Unit-test every state transition with a fake clock and fixed IDs.
2. Parameterize command classification across shell separators, wrappers,
   redirects, environment prefixes, test runners, opaque commands, and malformed
   quoting; ambiguous input must be side-effecting.
3. Use fake Tools to prove all direct, parallel, sequential, and non-interactive
   paths call the controller exactly once before execution.
4. Use a scripted fake `LLMClient` to prove blocked final text does not emit
   `LoopComplete` or return until obligations are satisfied.
5. Verify a write invalidates prior target/regression results and that only a
   matching post-edit execution restores completion.
6. Simulate request ordinals 19/20/21, 29/30/31, 35/36/37, and 40/41. Assert the
   checkpoints do not alter the ceiling.
7. Exercise inline Skill, forked Skill, foreground/background sub-Agent,
   in-process Team, isolated worktree, and Coordinator paths with fake clients.
8. Exercise MCP tools declared read-only, write-capable, and unknown. Unknown
   must fail closed.
9. Replay sanitized preserved traces into a new temporary output directory and
   assert source files remain unchanged and Provider request count is zero.
10. Verify old Artifacts without validation files still load and generate their
    existing report shape.

Full pytest, official evaluator execution, Provider calls, and Goal 4 reruns are
not part of this plan's Phase 1 verification.

## 9. Risks

| Risk | Consequence | Mitigation |
| --- | --- | --- |
| Multiple execute paths diverge | Gate bypass | One controller call in `_assess_tool`; path matrix tests |
| Streaming exposes draft final text | User mistakes blocked draft for completion | Structured blocked event and explicit UI state; terminalize only after guard |
| Shell parser false negative | Edit occurs without reproduction | Conservative `opaque` classification and adversarial corpus |
| Shell parser false positive | Ordinary command blocked | Explicit opt-in profile and structured exception, never silent waiver |
| Agent declares weak reproduction | Formal gate passes without semantic evidence | Preserve declaration/observation distinction; prompt review and replay audit |
| Test output parser drift | False regression result | Versioned adapters; unknown output is incomparable |
| Child state reset | Multi-Agent bypass | Required controller propagation and aggregate root finalization |
| Cross-process races | Lost checkpoint or edits | Append-only locked state and monotonic sequence numbers |
| Worktree integration invalidates evidence | Parent finalizes on child-only tests | Integration event invalidates parent post-edit obligations |
| Request ordinal uses local counter | Checkpoints missed by child requests | Shared Trial/session source; never rely only on local request index |
| Artifact schema breaks history | Old runs become unreadable | Additive optional files and compatibility fixtures |
| Gate consumes request budget | Less room to finish | Raise after completed requests and allow same-response structured block; no hidden calls |
| Gold data leaks into design | Invalid capability claim | No gold-patch input or oracle; scan fixtures and replay sources |

## 10. Phased commit plan

This is a recommendation only; Phase 1 creates no commit.

1. **Add deterministic validation state and command classification**
   - core types, state transitions, classifier, and unit tests;
   - disabled by default and not connected to Agent execution.
2. **Enforce pre-edit and finalization gates in the Agent loops**
   - checkpoint tool, all tool execution paths, both final-answer paths, fake
     client tests, and request checkpoint simulation.
3. **Propagate gates across Skills, MCP, and Multi-Agent runtimes**
   - child/worktree/process state propagation and bypass matrix tests.
4. **Add validation telemetry and offline trace replay**
   - additive recorder schema, compatibility readers, replay provenance, and
     deterministic report fixtures.
5. **Document reviewed activation and operational boundaries**
   - update user/developer documentation only after the preceding behavior and
     compatibility checks are accepted.

Each implementation commit should be independently zero-provider, should avoid
workflow changes, and should leave Stage C unauthorized.

## 11. Implementation reconciliation

The Phase 1 design was verified against the implementation before coding. The
following mapping is the B1 acceptance table; every location is additive and the
disabled profile returns the prior Agent behavior.

| Capability | Code insertion point | State and events | Tests |
| --- | --- | --- | --- |
| Reproduction gate | `Agent._assess_tool()` | `ValidationController`, `validation_blocked` | `tests/test_validation.py`, `tests/test_agent_validation.py` |
| Contract inventory | `ValidationCheckpoint` system tool | versioned inventory and target/regression declarations | `tests/test_validation.py` |
| Target test gate | `_snapshot_for_recovery()` after actual execution; both completion paths | observed tool result and obligation state | `tests/test_validation.py` |
| Regression comparison | Bash result metadata plus controller comparison | pre/post parsed result sets | `tests/test_validation.py`, replay fixtures |
| Request checkpoints | `_index_runtime_event()` | shared controller ordinal and checkpoint events | `tests/test_validation.py` |

Two small design adjustments were made while preserving scope:

- The suggested `validation_commands.py` is kept inside `codepacex/validation.py`
  because the conservative classifier and its state transitions share typed
  fingerprints. This reduces an otherwise artificial module boundary; no
  workflow-engine abstraction was added.
- Pane-backed teammates receive an explicit session ID and state-directory
  identity through narrow `CODEPACEX_VALIDATION_*` variables. This implements
  the design's cross-process recovery requirement without changing ordinary
  process environment behavior. The variables are absent while validation is
  disabled.

`ExperimentProfile.validation_mode` is an explicit, hash-bound activation
field. It appears in both the profile hash and runtime contract hash, so a future
Stage C freeze can record it in the manifest and cannot silently resume under a
different validation profile. Existing profiles default to `disabled`; no
historical Goal 4 manifest is changed or reinterpreted.

# Stage B implementation report

## Status

Implemented on `codex/stage-b-agent-validation-gates` from base commit
`6a21ad7f411b1ef3e903514fc3bd8dea55efdab6`. This report covers the Stage B
validation harness only. It is not a Goal 4 rerun, a Stage C trial, or a formal
Provider experiment.

## Scope and evidence boundary

- Provider requests made by this implementation: `0`.
- Goal 4 Trials rerun: `0`; Stage C tasks run: `0`.
- Historical Goal 4 Artifacts, Claims, Usage, charge, settlement and ledgers:
  unchanged.
- Goal 4 remains `GOAL4_ACCEPTED`, 20/20 scorable, 4 resolved / 16 unresolved.
- No gold patch was read, copied, derived, or used as an oracle.
- Offline replay is marked `replay_only=true`, `provider_requests=0`, and
  `formal_experiment=false`.

## Implementation commits

- `81f7765` `Add Stage B charter and validation design`
- `19d63a3` `Add validation state machine and operation classifier`
- `3f484ff` `Enforce validation gates across agent execution paths`

The remaining telemetry/replay and documentation commit is recorded in the
branch history after this report is finalized.

## Validation mechanisms

| Mechanism | Implementation location | Deterministic behavior |
| --- | --- | --- |
| Reproduction before edit | `Agent._assess_tool()` and `ValidationController.assess_tool()` | blocks implementation writes and opaque side effects until observed failure evidence or a closed-set exception exists |
| Contract inventory | `ValidationCheckpoint` and `ValidationController.declare()` | validates every required inventory field, revisions, target tests and regression slice |
| Target test completion | `Agent._snapshot_for_recovery()` and controller observations | accepts only matching, post-edit test execution with a passing exit status |
| Regression comparison | Bash exit metadata and controller result comparison | retains pre-edit baseline, detects new failures/collection errors and rejects incomparable results |
| Request checkpoints | `Agent._index_runtime_event()` | shared session ordinals create mandatory 20/30/36 structured acknowledgements |
| Completion gate | `Agent._run()` and `Agent.run_to_completion()` | rejects completed claims with pending obligations and ends repeated invalid attempts as `UNRESOLVED` |

## Activation and compatibility

`ExperimentProfile.validation_mode` is `disabled` by default and is included in
the canonical profile hash and runtime contract hash. `stage_b` is the explicit
future activation value. It is never inferred from a repository, task name, or
SWE instance ID. Disabled sessions create no validation state file, add no
obligations, and retain the existing tool and completion behavior.

Plan Mode can write only its designated plan artifact without arming the code
change gate. Any other write remains an implementation write. Unknown Bash and
MCP operations are conservative side effects. Existing security permissions,
including `bypassPermissions`, remain separate and cannot override validation.

## Cross-Agent coverage

Inline Skills retain their parent Agent controller. Forked Skills, ordinary and
worktree sub-Agents, in-process Team members, and Coordinator-created workers
receive the same controller. Pane-backed teammates receive an explicit session
identity and locked state-directory path through narrow internal environment
variables. The state log is append-only, snapshots are atomic, and Unix file
locking serializes local process updates. A child cannot reset reproduction,
checkpoints, or parent obligations by creating a new Agent instance.

## Telemetry and replay

Enabled runs emit append-only `validation` events. `RunRecorder` writes optional
`validation-events.jsonl` and `validation-summary.json`, then adds a bounded
validation section to new `report.md` files. Old Runs without these optional
files remain readable and are never rewritten.

`evals.stage_b_replay` reads a sanitized trace or deterministic fixture and
writes only to a new output directory. It does not initialize a Provider client,
execute a patch, call an evaluator, or count as a Trial.

## Verification

All commands used the project dependency environment in offline mode on macOS.

| Command | Result |
| --- | --- |
| `uv run --offline pytest -q tests/test_validation.py tests/test_validation_commands.py tests/test_agent_validation.py tests/test_stage_b_replay.py` | 23 passed |
| focused compatibility suite for Agent, Skills, MCP, sub-Agent and recorder | 246 passed |
| `uv run --offline pytest -q` | 1230 passed, 2 skipped, 30.94s |
| `uv run --offline pytest -q tests/test_secret_scan.py` | 3 passed |
| `python -m evals.stage_b_replay ...` | completed with `provider_requests=0` and `formal_experiment=false` |
| `git diff --check` | passed |

GitHub Actions run `29857496366` completed successfully for the implementation
head: both `macos` and `ubuntu` passed the main test suite, no-model Pilot
validation, credential-shaped source scan, and their respective sandbox checks.
No paid workflow, official evaluator, Goal 4 Trial, or Stage C Pilot was run
during this verification.

## Known limitations

- Static shell classification is conservative, not a complete shell semantics
  proof.
- MCP has no trusted capability metadata and therefore defaults to opaque.
- An inventory is auditable evidence of explicit coverage, not a proof of
  semantic completeness.
- Test-result parsing supports deterministic registered output only; unknown
  output is not silently considered regression-free.
- A bounded regression slice is not a whole-repository guarantee.
- Passing offline fixtures does not prove a real Provider Agent will improve;
  Stage C requires a separate freeze, workflow registration, budget
  authorization, and explicit user approval.

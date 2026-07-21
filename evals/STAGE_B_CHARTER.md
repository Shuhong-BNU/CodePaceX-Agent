# Stage B Agent validation gates charter

Status: Stage B implementation charter. The implementation remains opt-in and
zero-provider; it does not authorize a Goal 4 rerun or Stage C execution.

## Baseline

Stage B starts from repository commit
`6a21ad7f411b1ef3e903514fc3bd8dea55efdab6`. Goal 4 remains
`GOAL4_ACCEPTED`, with 20/20 scorable instances and 4 resolved / 16 unresolved.
The accepted result, its Claims, and its immutable Artifact are historical facts;
Stage B does not reopen or reinterpret them.

The motivating evidence is the post-hoc attribution in
[GOAL4_FAILURE_ANALYSIS.md](GOAL4_FAILURE_ANALYSIS.md) and
[goal4_failure_taxonomy.csv](goal4_failure_taxonomy.csv): incomplete patches,
regressions, and localization failures dominate the 16 unresolved instances.
Request count alone is not a sufficient failure explanation.

## Objective

Design and, only after separate approval, implement deterministic runtime gates
that make the Agent establish and retain evidence for:

1. reproduction before the first edit;
2. an explicit contract inventory;
3. completion of declared target tests;
4. bounded pre/post regression comparison; and
5. request-budget reviews after requests 20, 30, and 36.

The gates should prevent an Agent from silently editing or finalizing while a
required validation obligation is unresolved. Prompts may help the Agent choose
useful evidence, but prompts are not enforcement.

## Non-negotiable boundaries

- Stage B is zero-provider until a later, explicit authorization says otherwise.
- Stage B does not rerun any Goal 4 Trial, official evaluator, Pilot, or formal
  experiment.
- Stage B does not modify historical results, Artifacts, Claims, ledgers,
  evaluator output, or the accepted 4/20 result.
- Stage B does not read, derive, expose, or hard-code a gold patch. Gold patch
  content is not an implementation input, fixture input, or completion oracle.
- Stage B does not increase the 40-request Goal 4 ceiling or weaken the existing
  budget gate. Checkpoints at 20/30/36 operate inside that ceiling.
- Stage B validation is limited to deterministic fixtures and preserved trace
  replay. Preserved evidence is read-only.
- Stage B adds no paid workflow and requires no API key.
- Stage B does not start Stage C or Goal 5.

## Evidence policy

Permitted inputs are repository source, repository tests, synthetic deterministic
fixtures, and sanitized preserved traces whose provenance is recorded. A replay
may assert how a proposed state machine would classify an existing event sequence;
it must not contact a Provider, rebuild a historical result, or claim a new
benchmark outcome.

Stage B output must distinguish:

- observed runtime facts, such as a tool ID, command, exit status, timestamp, or
  request ordinal;
- Agent declarations, such as an intended target test or contract surface; and
- deterministic gate decisions derived from those facts and declarations.

An Agent declaration is never promoted to an observed fact merely because it is
well formed.

## Activation and compatibility boundary

Validation gates must be explicitly enabled by a versioned runtime profile or an
equivalent typed configuration. They are off by default for ordinary CodePaceX
sessions. Absence of the profile must preserve current behavior and current
Artifact schemas.

When enabled, the same deterministic controller must cover interactive execution,
non-interactive execution, Skills, MCP tools, sub-Agents, Team members, and the
Coordinator. A new Agent instance must not create a fresh state that can bypass
parent obligations.

Plan Mode may create or update its designated plan file without arming the
reproduction gate. Exiting Plan Mode and performing an implementation side effect
must use the normal Stage B gates.

## Fail-closed rules

When Stage B is enabled:

- an unknown or unparseable command is treated as potentially side-effecting;
- a missing checkpoint record cannot be inferred from free-form model text;
- a target test is complete only after a matching observed execution following
  the relevant edit and a deterministic passing result;
- a final response is not accepted while required obligations are pending; and
- loss or corruption of shared validation state blocks side effects and
  finalization rather than resetting the state.

The gate may return a structured remediation message and allow another Agent turn.
It must not fabricate test success, silently waive an obligation, or consume an
extra Provider request outside the existing request ceiling.

## Phase boundaries

### Phase 1: audit and design

Phase 1 produced this charter and [STAGE_B_DESIGN.md](STAGE_B_DESIGN.md). It
made no runtime change and created no new experimental evidence.

### Phase 2: deterministic core

Phase 2 implements typed state, command/test classification, tool/finalization
interception, structured telemetry, and unit tests using fake clients and
deterministic tools. Its changes remain default-disabled.

### Phase 3: propagation and replay

Phase 3 propagates the controller through Skills, sub-Agents, Team worktrees and
pane-backed processes, and validates preserved traces through an offline replay
adapter. It does not launch any child Provider session during verification.

### Stage C

Stage C remains out of scope. No Provider comparison or rerun is implied by
passing Stage B deterministic tests.

## Review gates

Before any implementation commit, reviewers must approve:

- the activation boundary;
- the side-effect and test-command classifiers;
- the shared state and child-Agent propagation model;
- the final-answer blocking semantics;
- the event and Artifact schema changes; and
- the deterministic fixture and replay plan.

Implementation is not complete until every supported execution path is shown not
to bypass the controller and the default-disabled compatibility tests pass.

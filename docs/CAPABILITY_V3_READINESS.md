# Capability V3 Zero-Provider Readiness

## Scope

This report records implementation readiness only. It does not claim any
resolution-rate or model-capability improvement and does not authorize a Pilot.

## Base And Branch

- Base: `e740201f2a9b94f3a6511de433ba400608837898` (`origin/main` refreshed on 2026-07-27)
- Branch: `codex/capability-v3-implementation`
- Frozen defaults: V3 disabled, three hypotheses, six contract dimensions,
  twelve matrix cases/tests, 20% reserve (minimum six), restore floor three,
  no full-suite fallback, risk-triggered baseline only, fail-open enabled.

## Design Mapping

| Frozen design | Implementation |
| --- | --- |
| V3-A evidence, hypotheses, OracleGuard | `codepacex/capability_v3/controller.py` deterministic AST/text evidence, bounded hypotheses, advisory risks |
| V3-B impact, matrix, reproducer | `controller.py` Python AST impact slice, mandatory F2P/prior failures, evidence-gated bounded matrix, reversible reproducer state |
| V3-C candidate and budget finalization | `models.py` immutable snapshots and `controller.py` deterministic C1/C2/C3, 40-request 8/3 transitions, restore-or-empty finalization |
| V3-D differential validation | `models.py` comparable identities/failure records and `controller.py` unknown/incomparable-aware attribution |
| Agent-loop adaptation | `codepacex/agent.py` opt-in telemetry/budget observation; no V3 output enters Permission or Validation deny paths |

## Tests And Fixture Metrics

Commands run in the isolated local Python 3.12 lockfile environment:

```text
uv run pytest tests/test_capability_v3.py tests/test_execution_telemetry.py tests/test_agent.py tests/test_agent_validation.py tests/test_validation.py -q
45 passed
```

The V3 fixture covers default/config, caller/test evidence, unknowns,
hypothesis cap and tool-evidence rejection, unanchored API and fixture risk,
mandatory F2P/prior-failure recommendation, evidence-gated matrix dimensions,
all finalization thresholds, C1/C2/C3 promotion, no-candidate empty export,
baseline-unavailable and incomparable attribution, artifact events, V2-disabled
compatibility, and Agent-side fail-open behavior.

Within the deterministic impact fixture, known impacted-test recall is 2/2
(100%). Candidate hard-stop restore is 1/1 (100%) when a stable candidate
exists; WIP export is 0/1. Invalid/missing-oracle states are retained as
non-strong evidence and incomparable baseline is always classified as
`incomparable` or `unknown`.

Full local suite result:

```text
1364 passed, 1 skipped
```

The pending Evaluation V2 full-replay and derived single-task freeze files were
regenerated to include the new V3 modules in their runtime source fingerprints.
This changes only deterministic runtime hashes; Goal 4 task data, accepted
baseline rows, evaluator contract, pricing, and paid authorization status are
unchanged.

## Replay And Limitations

`CapabilityV3Controller.replay()` accepts append-only V3 events and artifacts
preserve `events.jsonl` plus the derived state summary. Local preserved traces
exist only for earlier synthetic CodePaceX fixtures; a complete safe Goal 4
trace set is not available in this checkout. Therefore, real Goal 4 recall,
budget-hit, and `checkov-6895` replay metrics are unavailable and are not
claimed here.

## Recommended Diagnostic Pilot Panel

Use the frozen-design panel only after separate authorization:

- `aws-cloudformation__cfn-lint-3749`
- `dynaconf__dynaconf-1249`
- `deepset-ai__haystack-8489`
- `delgan__loguru-1306`
- `conan-io__conan-17102`
- `bridgecrewio__checkov-6895`

Estimate Pilot cost as: per-task frozen request ceiling multiplied by the
frozen per-request input/output pricing cap, plus separately recorded bounded
test runtime. The estimate must be recalculated from the Pilot's frozen
pricing snapshot; this implementation does not execute it.

## Safety Declaration

```text
Provider calls: 0
Paid workflows: 0
Secret content read: false
Gold/hidden tests accessed: false
Goal 4 baseline modified: false
```

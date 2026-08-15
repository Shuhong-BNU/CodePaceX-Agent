# Capability V3.0 activation postmortem

> **Document version**: v1.0.0
> **Recorded**: 2026-07-30T18:59:53+08:00
> **Status**: Current / formal closeout
> **Supersedes**: no prior V3 activation postmortem in this repository
> **Scope**: Artifact-derived activation facts for the V3.0 two-run completion; not a new experiment.

## Finding

Capability V3.0 is most accurately an **observable/recoverable baseline**. It
demonstrated Candidate preservation, budget finalization, and task-scoped
failure isolation in a paid run. It did not demonstrate that repository
evidence was converted into decisions, patches, and validation behavior.

| Mechanism signal across 20 task records | Observed count |
| --- | ---: |
| Evidence target symbols, direct callers, implementations | 0 / 0 / 0 |
| Tests/fixtures, defaults/config, history evidence | 0 / 0 / 0 |
| Hypotheses, contract matrices, differential validation records | 0 / 0 / 0 |
| Candidate snapshots | 84 |
| C2/C3 Candidate snapshots | 0 |
| Impact-test recommendations | 186 |
| Recommendations in venv/site-packages | 112 (60.2%) |

Every emitted Candidate snapshot was C1. The `checkov-6893`
infrastructure record exported no Candidate and did not run the evaluator; it
does not make the other 19 task records less terminal, but it is not scorable.

## What actually worked

- Candidate snapshots were preserved and nonempty Candidates were exported for
  every final-matrix task except the infrastructure terminal.
- Budget accounting closed after each segment, with `active_reservation=null`.
- A task-scoped transport failure did not stop the tail's remaining tasks.
- Request-ceiling terminals remained explicit, scorable outcomes and were sent
  through official evaluation when a Candidate existed.

These facts establish lifecycle observability and recoverability. They do not
establish that the Agent saw or used advice derived from repository evidence.

## What did not activate

The Artifact counters record no target symbols, callers, implementations,
tests/config/history evidence, hypotheses, contract matrices, or differential
validation records. A configuration flag or an instantiated controller is not
proof of an effective data path. In particular, the Artifact does not provide
an `Evidence -> Decision -> Patch -> Validation` chain for any task.

Impact-test suggestions are also not reliable enough to treat as behavioral
evidence: 112 of 186 suggested paths point into virtual environments or
site-packages. That contamination means the recommendation set does not yet
represent project-test selection quality.

## Consequence

The one additional resolved task must not be attributed to the dormant
Evidence/Hypothesis/Matrix/Differential mechanisms. It is compatible with a
better patch outcome, but not evidence of mechanism causality. The proper next
work remains the documented activation-fidelity path:

```text
Evidence -> Decision -> Patch -> Validation
```

P1 must make real repository evidence and bounded advice available in the
Agent's decision path, exclude environment paths from impact selection, and
record an auditable advice-to-request chain. P2 must verify those artifacts
with zero Provider calls before any future paid work. This P0 closeout does not
begin P1 or P2.

## Evidence

- [V3 activation CSV](../docs/governance/CodePaceX_V3_机制激活审计_20260730.csv)
- [Goal 4 to V3 audit](../docs/governance/CodePaceX_Goal4_vs_V3_结果与机制激活审计_20260730.md)
- [V3 final report](CAPABILITY_V3_GOAL4_FINAL_REPORT.md)
- [Stable governance index](../docs/governance/INDEX.md)

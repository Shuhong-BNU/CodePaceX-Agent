# Capability V3.0 Goal 4 final report

> **Document version**: v1.0.0
> **Recorded**: 2026-07-30T18:59:53+08:00
> **Status**: Current / formal closeout
> **Supersedes**: no prior Capability V3.0 final report in this repository
> **Study identity**: `STUDY-20260730-V3-REPLAY`
> **Decision**: Path A - retain 19/20 scorable; do not immediately retry `bridgecrewio__checkov-6893`.

## two-run infrastructure-recovery completion

This is a **two-run infrastructure-recovery completion**, not a single workflow
that completed 20 tasks and not a new full replay. The head run obtained the
first four scorable terminal results, then the original `haystack-8489`
transport failure was retained and safely settled. The tail run used a new
task-run identity for that recovery and then executed the remaining fifteen
tasks. The first four tasks were not rerun in the tail.

Final V3.0 result: **20 terminal; 19 scorable; 5 resolved; 14 unresolved; 1
infrastructure_error**.

| Segment | Actions run | Internal run | Bound main | Artifact | Digest |
| --- | --- | --- | --- | --- | --- |
| Head | `30503096853` | `v3corefull2020260730t000700z11093` | `9e09db1b1577441b107b890629e1a67cc7a77f25` | `8744897594` | `sha256:01324ab8b366b41c8c320e50b27cda407bc4daefb87d42e566f72b6801a50075` |
| Tail | `30510508446` | `v3coretail-20260730t030652-6683269c-03b5-4970-a050-de51da2b31f7` | `33b64e7644a86a784480c806a5beda28659d3f4b` | `8749299095` | `sha256:c09b6dcab74582ea4158ecdc2ad660c6236a9d67dbb431c66c34ab09154421bf` |

The tail freeze was verified as `7944fe028581b45586edf4e70e96082b7622e6fd867bf64b9af44b3e3971dfbd`,
with runtime hash `313d47bb5862264499fef77a7b86eb79a7767b60832061e717ee91901647b7ae`
and parent allocation hash
`e3e7c83a4412e5ad7a7d2c3930358597c14862c91aa4310dad25755178f9a9b5`.

## Result and comparison boundary

| Metric | Goal 4 formal baseline | Capability V3.0 |
| --- | ---: | ---: |
| Terminal task records | 20 | 20 |
| Scorable | 20 | 19 |
| Resolved | 4 | 5 |
| Unresolved / scorable request ceiling | 16 | 14 |
| Infrastructure error | 0 | 1 |
| Provider requests | 537 | 429 |
| Ledger cost | CNY `165.044424` verified actual | CNY `132.932760` total ledger consumption |

Goal 4's formal baseline is Actions run `29830820618`, Artifact `8496125148`;
the earlier partial/finalizer `29803967008` / `8486382695` is not the baseline.
V3 has one `unresolved -> resolved` change (`deepset-ai__haystack-8489`), zero
`resolved -> unresolved` changes, eighteen unchanged task outcomes, and one
`unresolved -> infrastructure_error` record (`bridgecrewio__checkov-6893`).
All four historical resolved tasks remain resolved.

The first head `haystack-8489` record (`agent_dispatch_missing`, zero Provider
requests, CNY `1.830912` conservatively settled) remains historical transport
evidence only. Its tail recovery result is the final matrix result. The sum of
the 20 final task rows is CNY `131.101848`; the total two-run ledger adds that
historical head settlement, yielding CNY `132.932760`.

## Per-task matrix

The checked-in structured source is
[CodePaceX_Goal4_vs_V3_20题逐题结果_20260730.csv](../docs/governance/CodePaceX_Goal4_vs_V3_20题逐题结果_20260730.csv).
`request_ceiling_reached` is a scorable unresolved terminal status.

| Task | Goal 4 | V3 terminal | V3 requests | V3 cost (CNY) | Candidate | Evaluator | Change |
| --- | --- | --- | ---: | ---: | --- | --- | --- |
| aws-cloudformation__cfn-lint-3749 | unresolved | unresolved | 32 | 8.361216 | exported_nonempty | completed | unchanged |
| aws-cloudformation__cfn-lint-3764 | unresolved | unresolved | 14 | 1.531884 | exported_nonempty | completed | unchanged |
| beetbox__beets-5457 | unresolved | request_ceiling_reached | 40 | 11.305716 | exported_nonempty | completed | unchanged |
| beetbox__beets-5495 | resolved | resolved | 12 | 1.035180 | exported_nonempty | completed | unchanged |
| deepset-ai__haystack-8489 | unresolved | resolved | 26 | 7.538508 | exported_nonempty | completed | unresolved -> resolved |
| beancount__beancount-931 | resolved | resolved | 21 | 2.531460 | exported_nonempty | completed | unchanged |
| beeware__briefcase-2075 | resolved | resolved | 14 | 1.100784 | exported_nonempty | completed | unchanged |
| beeware__briefcase-2085 | resolved | resolved | 9 | 1.794120 | exported_nonempty | completed | unchanged |
| bridgecrewio__checkov-6893 | unresolved | infrastructure_error | 10 | 2.705268 | not_exported | not_run | unresolved -> infrastructure |
| bridgecrewio__checkov-6895 | unresolved | unresolved | 11 | 1.116732 | exported_nonempty | completed | unchanged |
| conan-io__conan-17092 | unresolved | unresolved | 15 | 5.619372 | exported_nonempty | completed | unchanged |
| conan-io__conan-17102 | unresolved | request_ceiling_reached | 40 | 14.834520 | exported_nonempty | completed | unchanged |
| cyclotruc__gitingest-115 | unresolved | unresolved | 14 | 1.845036 | exported_nonempty | completed | unchanged |
| cyclotruc__gitingest-134 | unresolved | unresolved | 21 | 4.799172 | exported_nonempty | completed | unchanged |
| deepset-ai__haystack-8525 | unresolved | unresolved | 15 | 2.968704 | exported_nonempty | completed | unchanged |
| delgan__loguru-1297 | unresolved | unresolved | 7 | 0.640572 | exported_nonempty | completed | unchanged |
| delgan__loguru-1306 | unresolved | unresolved | 10 | 1.298352 | exported_nonempty | completed | unchanged |
| dynaconf__dynaconf-1225 | unresolved | request_ceiling_reached | 40 | 32.160840 | exported_nonempty | completed | unchanged |
| dynaconf__dynaconf-1249 | unresolved | unresolved | 38 | 16.329588 | exported_nonempty | completed | unchanged |
| instructlab__instructlab-2540 | unresolved | request_ceiling_reached | 40 | 11.584824 | exported_nonempty | completed | unchanged |

## Accounting and execution controls

| Evidence | Head | Tail / combined conclusion |
| --- | --- | --- |
| Provider requests | 98; `usage=1761999` in the head summary (unit not labelled as an aggregate token total) | 331 final-matrix requests; 429 total final-matrix requests |
| Token / usage totals | No aggregate input/output/reasoning token total is established in the supplied closeout evidence. | Not reported as a fabricated total; raw Artifact request entries remain the source of record. |
| Cost | CNY `24.064908`, including the historical `haystack-8489` settlement | Final matrix CNY `131.101848`; two-run total CNY `132.932760` |
| Ledger terminal state | `ledger_closed=true`; `active_reservation=null` | Tail closeout evidence records `ledger_closed=true`; `active_reservation=null` |
| Contract | `V3_CORE`, serial execution, maximum 40 Provider requests, retry `0`, fallback `false` | Same frozen execution contract; tail hard cap CNY `225.935092` |

No automatic retry was performed. The sole recovery used a new, frozen tail
identity for `haystack-8489`; it does not alter the original head record. The
tail's other paid entry points (`controlled-pilot-paid-execution`,
`paid-execution`, and `v3-core-full20-paid-execution`) were skipped. No second
tail paid dispatch, rerun, or continuation was used.

## Interpretation limits and decision

V3's additional resolved terminal is an observed result, not causal proof that
Evidence, Hypothesis, ContractMatrix, or DifferentialValidation caused it. The
same twenty historical tasks were used for diagnosis and design, so they are
not a fresh holdout; the 19/20 scorable denominator must remain visible.
This report makes no leaderboard, pass@k, statistical-significance, or broad
generalization claim.

Path A is retained: do not rerun `checkov-6893` under V3.0. The next gate is
P1/P2 activation fidelity, which remains unstarted by this closeout.

## Related evidence

- [Activation postmortem](CAPABILITY_V3_ACTIVATION_POSTMORTEM.md)
- [Goal 4 Evidence Index](GOAL4_EVIDENCE_INDEX.md)
- [Evaluation History](EVALUATION_HISTORY.md)
- [Evaluation Artifact Index](EVALUATION_ARTIFACT_INDEX.md)
- [Stable governance index](../docs/governance/INDEX.md)

# Evaluation V2 Goal 4 Full 20-Task Replay

This is a frozen readiness contract for one future Evaluation V2 replay of the
same twenty Goal 4 instances. It is not a paid result, does not apply a gold
patch, and does not make a Provider request.

The Agent-visible source is `full_replay_payloads/tasks.jsonl`: exactly seven
fields per task (`instance_id`, `repo`, `base_commit`, `problem_statement`,
`platform`, `version`, `environment_setup_commit`). The module verifies each
canonical Agent-visible payload hash against the published Goal 4 execution
payload hash; the original Goal 4 full-payload hash is preserved only as a hash,
never as a loaded field.

The logical paired matrix remains in Goal 4 order. Future execution starts with
the diagnostic six in this fixed order:

1. `aws-cloudformation__cfn-lint-3749`
2. `aws-cloudformation__cfn-lint-3764`
3. `bridgecrewio__checkov-6893`
4. `conan-io__conan-17092`
5. `dynaconf__dynaconf-1225`
6. `instructlab__instructlab-2540`

They are all historical unresolved tasks selected from the published taxonomy:
two `incomplete_patch`, one each `regression_introduced`,
`root_cause_localization_failure`, `cross_file_propagation_missed`, and
`request_ceiling_exhausted`; together they cover one-file, two-to-four-file,
and five-plus-file buckets. The remaining fourteen run in their original Goal 4
relative order only when Phase A accounting and infrastructure are healthy.

The paired claim is explicitly `Goal 4 system-level Harness vs Evaluation V2
system-level Harness`. It fixes instances, repo/base commits, statements,
evaluator revision, Provider/model/pricing, 40 Provider requests per task,
50 Agent iterations, strict serial order, `fallback=false`, `retry=0`, and
fresh workspace/authorization/allocation/ledger. Corrected V2 budget bridge,
Base Lane, and runner/reporting changes are documented treatment differences;
this is not a single-variable causal claim.

The recommendation is CNY `80.000000` for Phase A, CNY `170.000000` incremental
for Phase B, and CNY `250.000000` total. The conservative theoretical maximum
remains CNY `73.236480` per task and CNY `1464.729600` across twenty, so the
recommended cap is an admission cap rather than a completion guarantee.

`evaluation-v2-full-20-replay.yml` runs the zero-Provider 20-task preflight,
then a deterministic 6+14 shadow. Capability outcomes (`resolved`, `unresolved`,
`agent_no_candidate`, `validation_failed`) do not stop Phase B. Provider
transport, runner/evaluator failures, duplicate execution, an open reservation,
or insufficient next reservation do stop it. The paid job is disabled unless a
future user supplies exact Freeze, CNY `250.000000`, acknowledgement, fresh Run
ID, and `paid_execution=true` on `main`.

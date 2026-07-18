# Goal 2 Multi-Agent change-scope evidence erratum

This post-review erratum corrects an evidence interpretation boundary. It does
not modify a frozen Trial, Artifact, Claim output, Usage, Token count, charge,
settlement, or ledger entry, and it does not authorize a Provider run.

## Finding

The historical zero-model Multi-Agent preflight injected
`.codepacex/debug.log` into the fixture workspace. Its old changed-file parser
included that known CodePaceX runtime log in `exact_change_scope`, even though
the expected source files, test result, delegation check, runtime-mode check,
and conflict check were otherwise passing. Python bytecode and pytest cache
artifacts have the same failure mode.

The corrected grader retains `raw_changed_paths`, records explicitly classified
`ignored_runtime_artifacts` with reasons, and compares only
`graded_changed_paths` with the frozen expected-file contract. It does not
ignore the `.codepacex` directory, ordinary source/configuration files, unknown
hidden files, or arbitrary untracked files.

## Historical evidence status

The frozen Multi-Agent source runs and zero-model gate artifact are unavailable
in this worktree:

- `evals/.runs/goal2/multi-formal-single`
- `evals/.runs/goal2/multi-formal-multi`
- `evals/.runs/goal2-control/multi-formal-grader-preflight`

They are not recreated. The historical `NO-GO` cannot be regraded from its
immutable evidence, so it is `evidence_insufficient`, not an independently
verified block on Multi-Agent capability. No formal Multi-Agent Provider Trial
was run, and this erratum neither infers a comparative result nor permits a
rerun to fill the gap.


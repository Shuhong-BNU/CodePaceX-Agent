# CodePaceX Lightweight Agent Eval

This directory contains a small deterministic eval harness for CodePaceX.
It runs fixed coding-agent tasks against copied fixtures, captures the
`stream-json` trace, computes the Agent file diff before graders run, and
emits Markdown plus JSON artifacts.

## Usage

Run one task:

```bash
./.venv/bin/python evals/run_eval.py --task codepacex_001_config_bugfix --keep-failed
```

Run the suite:

```bash
./.venv/bin/python evals/run_eval.py --keep-failed
```

Artifacts are written under `evals/.runs/` and are intentionally ignored by
Git. Baselines can be copied manually into `evals/baselines/` when useful.
The current full-suite run is a pre-baseline infrastructure shakeout, not
Baseline v1.

## Layout

```text
evals/
  fixtures/       # Small copied workspaces used by tasks
  tasks/          # YAML task definitions
  graders.py      # Deterministic command, file-state, and safety graders
  run_eval.py     # Runner, trace parser, metrics, reports
  .runs/          # Local run artifacts, ignored by Git
```

The runner executes the current checkout with:

```text
{sys.executable} -m codepacex
```

and prepends the repository root to `PYTHONPATH`, so it does not call a stale
installed `codepacex` binary.

## Result Status

- `PASS`: the Agent trial produced an outcome and all required outcome graders
  passed.
- `FAIL`: the Agent trial started, infrastructure did not explain the failure,
  and a grader failed or the Agent hit a real runtime/timeout failure.
- `ERROR`: the trial could not produce a valid scored outcome because startup,
  config, provider, network, transport, timeout-before-start, or runner
  infrastructure failed.

`ERROR` tasks do not enter the task success-rate denominator. Provider,
network, or transport errors are recognized conservatively. If such an error
occurs after all required outcome graders have already passed, the task remains
`PASS` and records `warning_type: infra_error_after_success`.

## Boundaries

- Graders are deterministic: command, file-state, and safety checks only.
- Trace metrics are diagnostic; tool errors do not automatically fail a task.
- The first version is a developer-environment regression eval. User-level
  CodePaceX config, global instructions, hooks, and permission rules may affect
  model behavior, so the report records relevant source hashes.
- No LLM judge, SWE-bench adapter, dashboard, pass@k, or automatic provider
  retry is included in this MVP.

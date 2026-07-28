# Capability V3 Artifact-fidelity contract

The Controlled Pilot CLI uses `Agent.run()` for `-p --output-format stream-json`.
For `V3_CORE`, that lifecycle starts and finalizes the existing controller in a
`finally` block. The controller writes its raw evidence directly to the final
task Artifact path:

```text
controller state_dir
  = task_root/capability-v3
  -> summary.json, events.jsonl, final.patch
  -> paid run root/runs/<ordinal>-V3_CORE/tasks/<instance_id>/capability-v3/
  -> uploaded paid Artifact root
```

The task workspace is separate from `task_root`; cleanup only removes its
temporary virtualenv and cache after the task Artifact has been written.
Before a V3 Candidate is exported, the executor requires all three raw files,
a nonempty parseable event chain, matching `V3RunConfigured` task/treatment
identity, and a byte-identical `candidate.patch`/`final.patch`. A missing or
mismatched raw Artifact is a treatment-fidelity failure, never a fallback to
the workspace diff. `V2_CONTROL` has no V3 Artifact requirement.

The zero-Provider Controlled Pilot rehearsal materializes the same nested
paths for all six V3 runs and records six fidelity records in its summary.

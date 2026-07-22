# Stage D Freeze Report

The Stage D protocol-canary Freeze is a zero-provider deterministic snapshot.
It was generated from the merged Stage D unblocker commit
`400475531dc2f44ed5661e9153fff27dc3d1cc8d`.

- Freeze: [`stage_d/stage_d_freeze.json`](stage_d/stage_d_freeze.json)
- Freeze SHA-256: `f7ac9b8a4c19859df59f17538ca3fcb37bd98522cc0a1791c383a6b829f2bf16`
- Runtime contract SHA-256: `f0071b1e9a1cb226b6cba64c66765e4851fb2f05cb33c5ee10bdb729ec5c35aa`
- Profile: `validation_mode=stage_b`, deferred tools, `recovery_v1`,
  `session_allow`, single Agent.
- Frozen canary tasks: `beetbox__beets-5495`, `beancount__beancount-931`.
- Provider requests, Stage C Trials, paid workflow dispatches: `0`.

The Freeze carries no authorization identity and its `paid_execution_authorized`
field is false. A later canary requires a fresh authorization identity, immutable
checkout binding, and rolling per-request reservation before transport.

This is separate from the preserved Stage C 0/6 evidence and its
`PHASE_2_NO_GO` decision. It cannot create a Stage C Phase 2, six-task, or
20-task Claim. The exact reporting limits are in
[`STAGE_D_CLAIMS_BOUNDARY.md`](STAGE_D_CLAIMS_BOUNDARY.md).

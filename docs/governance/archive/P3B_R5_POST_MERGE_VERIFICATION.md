# P3-B R5 Post-Merge Verification

> Historical verification recorded 2026-07-31 20:35 (UTC+8).
>
> Scope: post-merge zero-provider verification of P3-B R4. This is not a
> current-status document, a paid result, or a paid-authorization record.

## Provenance

This note preserves the formal evidence that was added only in historical,
unmerged PR #83. The PR #82 merge identity and GitHub Actions run below were
cross-checked while consolidating the governance documentation. It does not
restore PR #83's AI handoff, versioned index, or historical snapshot.

## Locked merge identity

- PR #82 was merged with a normal merge commit, without squash, rebase or force
  push.
- Locked head: `b280a1abc7bca9d2ff6dfc7236a76f757a9fde8f`.
- Locked base: `4a5b66787f1d7ab0619b86eb3c9fb7f0f7b6cd72`.
- Merge commit / formal main at verification: `844d1098f2f62119947afe1b2dda33d04d50cc6c`.
- Superseded PR #81 remained closed and unmerged.

The review recorded no change to the frozen model, prompt, Provider, evaluator,
pricing, task/pair order, budget, or P4. It also did not create or expose a real
acknowledgement, dispatch token, internal run ID, or Secret value.

## Post-merge verification

### Main CI

- GitHub Actions run `30630775458`: `success`.
- Ubuntu job `91156341297`: `success`.
- macOS job `91156340888`: `success`.

### Exact-main validate-only

The clean detached checkout at formal main ran the committed
`python -m evals.evaluation_v2.p3b_paid_executor --validate-inputs-only` path
with a test-only, non-executable, non-reusable canonical bundle.

- `HEAD == expected_main_sha == 844d1098f2f62119947afe1b2dda33d04d50cc6c`.
- `paid-input-preflight.json` SHA-256:
  `c7c662935521a0c840a074ca0c8e67ea7dfe47b2b9d48b018fda301134eb5964`.
- No workspace, ledger, reservation, or Provider client was created.
- `provider_reached=false`; Secret value read was `false`; Provider requests /
  Usage / charge were `0 / 0 / CNY 0`; `active_reservation=null`.

### Zero-provider readiness

The P3-B workflow does not run from a merge to `main`; no paid workflow was
manually dispatched. The same committed readiness and freeze path instead
verified:

- `tests/test_p3b_post_merge_rebind.py`: `7 passed`.
- Production-adapter preflight: `8/8` frozen task-runs and `4/4` unique
  instances passed.
- Provider transport was hard-disabled for every record and the paid job was
  skipped.
- Provider requests / Usage / charge were `0 / 0 / CNY 0`, Secret value read
  was `false`, and `active_reservation=null`.

Historical run `30620506129` remains the A2 fail-closed record: `0/8`
task-runs, no Artifact, and Provider requests / Usage / charge of
`0 / 0 / CNY 0`. R5 did not overwrite or regrade it.

## Bound identities

| Input | SHA-256 / identity |
| --- | --- |
| Workflow content | `0a48f655f8a6cb12347e5c794bbc6b321284c258f3b27f1672d0e0db0860eab6` |
| Paid executor content | `5eca4015570f9f9744bd4fbe4d4a6ba667baa0c29731bd47a83864396585bb46` |
| Paid gate content | `8399579370ec7cfee1a2eb1b72638068665b100fe3b09bcc4135642731f942dc` |
| Freeze base commit | `2794e27220d3fada3bd0fdd3a1a14ff50e3a6034` |
| Freeze SHA-256 | `4c1e4468b2685c198a1eeed03e607963d3514daaa17a6068fa7b4c832d9054bd` |
| Freeze canonical SHA-256 | `f4d20dce7246f4dc825cd540abf80c983302e77e0b58707497bd412e37f0ad48` |
| Readiness SHA-256 | `d5e5c0c685617fc73f0bb29200a01f63388ea87ab00a8642b1464f1c081b2484` |
| Allocation hash | `58e3e967736fe4335a8882fa21b69fd755946da28aa04d3f88babb8cb2c25fff` |
| Authorization hash | `b9dfe904ad3b4728b00f2109459e99ac6464e1cd8311b74ee3ef3a882b310d51` |

## Boundary

R5 is historical zero-provider post-merge evidence only. It did not authorize a
paid rerun or establish paid success. P4 was blocked at that time. The current
P3-B conclusion remains in [CURRENT_STATUS.md](../CURRENT_STATUS.md): A4 failed
before Provider transport, A5 has not run, and P4 remains gated.

# Stage C Freeze Report

## Status

This is a zero-provider Stage C Freeze, not a Stage C Trial. The frozen branch
starts from Stage B merge commit `a6b401082e220665bc29d681ebdc9fca1c08ac82`.
The freeze-contract commit is `10301dd`; the dispatch-only workflow registration
commit is `e834c6a`. A paid evaluated commit and an authorization hash are
intentionally absent until a human separately approves each phase.

## Frozen Identities

- Goal 4 final run: `29830820618`
- Goal 4 Artifact ID: `8496125148`
- Goal 4 archive SHA-256:
  `8b9309a9ee03b068bf96e69afd50ecc2c18e4a70046dc1ae99359310dc70c6c8`
- Goal 4 final-report SHA-256:
  `a404d82ec17c93471b842a4139e6d3f6350c672e8edf5744b57e16820e1c1a38`
- Goal 4 freeze/recovery commit: `75a1eca465913e1c5be81e58eba89bc4d1cd8853`
- Source Goal 4 matrix SHA-256:
  `9ff16e850b92a6eb0bd1338cb85253a605fdfb0e0aa77180488382eca353972a`
- Stage C matrix SHA-256:
  `5ce92a24bf71d93221e931083c6dc2df4d03d0afc92138b8c02c70959dbbca62`
- Stage C baseline snapshot SHA-256:
  `caa0c1e4105dd0106b76999ae60c293ef65e5f1e5ae5a6603a579abb4bbdc8c2`
- Stage C profile SHA-256:
  `b371effcd3eec54ea1d1c79cf3cb28d5f680f14ed892ef36e54aa75d387a6beb`
- Stage C runtime-contract SHA-256:
  `453a8502c497387a47d6e93e91477e234cc4444f1822d06152d54087676bc7b1`
- Pricing SHA-256:
  `a09eb6e6955b9fb68d3e011771c948f7a14b7bbca5316a2433cab099d0b643d3`
- Official evaluator commit: `ad79b850f15e33992e96f03f6e97f05ddf9aa0be`

## Budget Contract

The frozen CNY 12/M input and CNY 36/M output snapshot computes CNY `1.830912`
for one maximum request at 128,000 input and 8,192 output tokens. The existing
paid gate reserves and settles one request at a time; it does not reserve a
40-request task or an entire phase. Phase 1 is capped at CNY 80. Cumulative Stage
C is capped at CNY 250, and Phase 2 may use only CNY 250 minus Phase 1 combined
conservative consumption. The theoretical 40-request path remains a documented
risk, not a completion guarantee.

## Verification

- Stage C contracts/workflows/paid-gate targeted tests: `52 passed`.
- Full offline pytest: passed.
- Secret scan tests: `3 passed`; tracked-source scan passed.
- Markdown link test, workflow YAML parsing, and `git diff --check`: passed.
- Freeze validation and both dry-runs report `provider_requests=0`,
  `paid_execution=false`, and `formal_stage_c_trial=false`.

## Boundaries

- Provider requests: `0`
- Stage C Trials: `0`
- Goal 4 reruns: `0`
- Paid workflows dispatched: `0`
- Historical evidence modified: `false`
- Gold patches read: `false`

The Phase 1 and Phase 2 workflows remain Draft-PR registration only. They cannot
run a paid path in this Freeze; a future paid workflow must bind a manually
approved immutable commit, separate authorization identity, and Phase 1 Artifact
identity before any Provider transport is permitted.

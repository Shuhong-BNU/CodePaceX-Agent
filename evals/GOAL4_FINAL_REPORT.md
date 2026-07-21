# Goal 4 Final Evidence Report

Status: `GOAL4_ACCEPTED`

## Artifact Identity

- Final GitHub Actions run: `29830820618`
- Artifact: `goal4-request-ceiling-recovery-evidence-29830820618` (ID `8496125148`)
- Artifact archive SHA-256: `8b9309a9ee03b068bf96e69afd50ecc2c18e4a70046dc1ae99359310dc70c6c8`
- Artifact final-report SHA-256: `a404d82ec17c93471b842a4139e6d3f6350c672e8edf5744b57e16820e1c1a38`
- Freeze / recovery code commit: `75a1eca465913e1c5be81e58eba89bc4d1cd8853`
- Matrix SHA-256: `9ff16e850b92a6eb0bd1338cb85253a605fdfb0e0aa77180488382eca353972a`
- Claims: `valid=true`, `verified=true`

The archive above is the immutable final fact source. This repository record does not include the Artifact payload.

## Scope

This is a pre-registered 20-task Python-only SWE-bench-Live Lite subset.
Goal 3 Pilot tasks are excluded. This is not a full Lite result, leaderboard result, or pass@k.

## Results

- Registered / attempted / completed / scorable: 20 / 20 / 20 / 20
- Resolved / unresolved: 4 / 16
- Infrastructure / Provider / Agent errors: 0 / 0 / 0
- Total requests: 537
- Charges / settlements: 537 / 540
- Provider Usage / conservative settlements: 537 / 3
- Input / completion / reasoning Tokens: 13134748 / 206318 / 99175
- Verified actual Provider cost: CNY 165.044424
- Uncertain maximum exposure: CNY 5.492736
- Combined conservative budget consumption: CNY 170.537160
- Selected terminal Trial cost: CNY 130.885692
- Historical failed-attempt verified Provider cost: CNY 34.158732
- Paid Trial attempts / infrastructure retries: 25 / 5
- active_reservation=null

## Stratified Results

| Bucket | Registered | Resolved | Unresolved | Requests | Selected terminal cost |
| --- | ---: | ---: | ---: | ---: | ---: |
| one_file | 8 | 1 | 7 | 143 | CNY 22.772496 |
| two_to_four_files | 8 | 3 | 5 | 187 | CNY 58.510680 |
| five_plus_files | 4 | 0 | 4 | 136 | CNY 49.602516 |

The full per-instance outcome and selected terminal Trial accounting are in [GOAL4_EVIDENCE_INDEX.md](GOAL4_EVIDENCE_INDEX.md).

## Claim Boundary

Only the frozen matrix, model identity, official evaluator, actual costs, and observed resolved count are claimed. This is neither a complete SWE-bench-Live Lite leaderboard result nor pass@k. It makes no model-comparison, statistical-significance, generalization, or production-success claim.

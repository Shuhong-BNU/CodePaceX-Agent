# Stage D Canary Evidence Index

| Evidence | Identifier / SHA-256 | Role |
| --- | --- | --- |
| GitHub Actions run | `29933711107` | One authorized paid Canary execution |
| Canary Artifact | `8535392517` | Immutable uploaded terminal evidence |
| Artifact digest | `1129c64d9aa1153c8b21fe85f9030627683257d4d4e6aa14064d486002fe28a3` | Artifact integrity |
| Terminal ledger | `4278853c3704347d64251b5cc83793a45dd08620b6c0f62c5ac449d85ca50613` | Usage, charge, settlement, reservation evidence |
| Canary report | `caa3d2ce4b7f10bf857460d0139de0d0f2e90067ad1e01015b6bd78b9229d725` | Terminal task statuses |
| Request timeline | `stage_d_canary_request_timeline.csv` | One row per settled Provider request |
| Artifact-derived fixture | `tests/fixtures/stage_d_canary_contract_inventory_payloads.json` | Sanitized JSON-string inventory payload shapes |

The fixture stores eight direct trace payloads. It contains no API keys, raw
Provider prompts, gold patches, or evaluator inputs. The difference from the
earlier manual count of nine is documented in the executive summary.

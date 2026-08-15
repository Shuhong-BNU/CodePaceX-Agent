# Evidence Governance

This is the stable public version of the repository's evidence-governance
rules. It keeps the durable parts of the former versioned governance document
and removes AI handoff, resume and one-off execution choreography from the
public contract.

## Evidence hierarchy

1. Raw task-run records, evaluator reports, ledgers and immutable Artifact
   archives are the source of record.
2. Freeze manifests, task contracts, authorization records, pricing and runtime
   identities define the variables under which a result is interpretable.
3. Formal reports and the evaluation history/artifact indexes summarize those
   records without changing them.
4. Governance summaries provide navigation and claim boundaries; they never
   replace raw evidence.

## Required identity fields

Every formal Study/Run/Claim should retain, where applicable:

- study, run, task and treatment identity;
- evaluated Git commit and evaluator revision;
- model, Provider, endpoint and pricing identity;
- frozen prompt/task payload and its hash;
- freeze, allocation, authorization and Artifact identities;
- request, Usage, charge and settlement records;
- terminal status and the exact scorable denominator;
- limitations, missing data and any recovery or infrastructure history.

## Frozen inputs and immutability

- A frozen input is byte-identified before execution. A later recovery gets a
  new Run/Artifact identity; it does not overwrite the failed record.
- Raw evidence paths under `evals/` are stable public interfaces. Documentation
  cleanup must not move or rewrite machine-readable payloads merely for layout.
- A report may add interpretation, but it must not silently edit a historical
  result, ledger, evaluator output, Candidate, or Artifact digest.

## Accounting and cost

- Reserve exactly one Provider request at a time from the frozen pricing and
  limits before transport.
- Preserve raw Provider Usage and charge when available; settle only according
  to the recorded ledger contract.
- If durable Usage is unavailable, use an explicitly labelled conservative
  settlement and retain Tokens as unknown. A reservation is not a Provider bill.
- Close every terminal path with `active_reservation=null` or record the
  explicit unresolved accounting state.

## Provider boundaries

- Zero-provider, dry-run, CI, preflight, secret-presence checks and deterministic
  tests must be labelled as engineering/readiness evidence.
- Paid execution requires an explicit authorization identity, exact main/freeze/
  allocation binding, fail-closed gates, request ceiling, retry/fallback policy,
  and an immutable output path.
- Runtime prompts used by CodePaceX and frozen benchmark prompts are public
  product/evaluation inputs. Prompts written to operate Codex or GPT on the
  repository are local-private and belong under `_local/prompts/` or
  `_local/context/`.

## Claims and reporting

- State the scope before the number: task family, platform, model, evaluator,
  denominator and whether the result is diagnostic, rehearsal, formal or
  historical.
- Report resolved, unresolved, infrastructure, provider, budget-blocked,
  not-run and insufficient-data outcomes separately.
- Do not infer causality, significance, leaderboard rank, pass@k, production
  behavior or cross-platform generalization from these Studies unless a new
  protocol and evidence explicitly support it.
- A claim is publishable only when its source report, raw evidence or Artifact
  identity, code/evaluator identity and limitations are all navigable from
  [INDEX.md](INDEX.md) and [EXPERIMENTS.md](EXPERIMENTS.md).

## Document boundary

- Public core: runtime code, tests, workflows, product prompts, contracts and
  reproducible evaluation inputs.
- Public evidence: formal reports, ledgers, freeze records, task-level output
  and failure postmortems.
- Public archive: a small number of durable historical engineering notes.
- Local-private: AI handoffs, development prompts, resume/interview material,
  private plans and exploratory analysis. These remain ignored under `_local/`.

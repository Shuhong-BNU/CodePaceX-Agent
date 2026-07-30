# CodePaceX current snapshot: P1/P2 V3.1 activation fidelity

> **Document version**: v1.0.0
> **Recorded**: 2026-07-30 (UTC+8)
> **Status**: P1/P2 implementation and zero-provider acceptance complete locally; PR CI pending

```yaml
repository: Shuhong-BNU/CodePaceX-Agent
bound_main: ce15722d2154d95a2dfe5abb659b220c2421f4cb
branch: codex/v3.1-activation-fidelity
agent_release_label: CPX-Agent v3.1.0 — Activation Fidelity
release_label_status: logical implementation label; no Git tag or GitHub Release
p0: Completed via PR #72
p1: locally complete; PR CI pending
p2: locally complete; PR CI pending
paid_scope: none
provider_requests: 0
provider_usage: 0
provider_charge_cny: 0
secret_read: false
p3: not started
checkov_6893: not retried
```

## P1 implemented data path

- Repository evidence strips generic issue wrappers and excludes `.git`, virtual environments, `site-packages`, build output, and cache directories.
- `ReadFile`, `Grep`, and `Glob` outputs contribute bounded digest-backed facts to the existing Evidence packet.
- Compact advice is added directly to both Agent request assembly paths immediately before the transport boundary. Artifacts record `AdviceGenerated`, `AdviceInjected`, and `AdvicePresentInRequest` without retaining raw task transcripts.
- Contract-heavy tasks create at most two evidence-backed hypotheses and a bounded matrix; read-tool evidence can reject the adjacent interpretation.
- Actual `RunTest` calls record baseline/post observations and semantic fixed/new/persistent/incomparable events. Existing baseline failures are not treated as new regressions.
- Candidate promotion follows C1 → C2/C3 from actual test evidence. Python 3.8 PEP 604 syntax is surfaced before promotion.

## P2 evidence

- Six deterministic end-to-end fixtures use the real Agent request construction path and a recording fake transport. They cover Python 3.8 compatibility, default/explicit configuration, sibling backend, exception boundary, baseline failure attribution, and virtualenv/site-packages exclusion.
- [Activation replay matrix](../../evals/evaluation_v2/activation_v31/ACTIVATION_REPLAY.md) is a deterministic preserved-task identity replay of the historical Goal 4 development set: 20/20 `repo@base_commit` plus named-entity anchors, with zero Provider operations. It does not checkout historical task repositories and therefore records zero source-definition anchors rather than making an unsupported source-evidence claim.
- The existing full-20 freeze has only its deterministic runtime-source hash refreshed for this source change. No preflight, shadow, paid execution, workflow dispatch, or historical result rewrite occurred.

P2 satisfies L1/L2/L3 with the fixture transport and persistent artifacts. L4 capability or resolved-rate claims remain outside scope and require a future authorized paired Pilot.

# Controlled Pilot task-run budget binding

The Capability V3 Controlled Pilot uses one `BudgetAuthorization`, one
`BudgetLedger`, and one parent `StageCBudgetAllocation`.  Its twelve
task-runs are deterministic ceiling identities inside that parent allocation;
they are not independent ledgers or pre-funded pools.

Each Provider child receives and validates, before reserve and transport:
`task_run_id`, task-run allocation ID/hash, instance ID, treatment, and the
expected Artifact path.  The parent allocation manifest and the constructed
trial ID must agree.  A missing or mismatched value records a budget block and
fails closed before the Provider call.

Every task-run has a theoretical ceiling of CNY 73.236480.  The twelve
ceilings total CNY 878.837760 and remain a risk-display maximum, not a
pre-allocation.  A formal Phase A Freeze may authorize a lower parent hard
cap; its CNY spendable amount plus the CNY 0.000001 safety reserve is enforced
for every rolling reservation before Provider transport.  Therefore child
ceilings may sum above the parent hard cap without creating twelve funds or
reserving unused capacity.  A reservation, charge, settlement, cancellation,
and block carries the task-run allocation identity when the Controlled Pilot
contract is active.

An explicit Provider HTTP 401 authentication rejection or HTTP 403 workspace
access denial is known to have returned before any model response or Usage. The
runner records the failure type and timestamp, then writes a CNY-zero
cancellation that closes the active reservation. Connection loss, timeout and
other ambiguous post-dispatch failures are not treated as access denials: they
retain the existing conservative-settlement path. A paid workflow uploads its
evidence even on failure, then fails its job when the paid summary is partial
or leaves an active reservation.

Formal Phase A uses the new `ws-e0dfiat7lu57en5y` Beijing workspace endpoint.
Every V2_CONTROL/V3_CORE pair uses that same account and endpoint. Goal 4 keeps
its original workspace endpoint as immutable historical evidence, so it is a
longitudinal cross-account/endpoint reference rather than a replacement for
the current paired control.

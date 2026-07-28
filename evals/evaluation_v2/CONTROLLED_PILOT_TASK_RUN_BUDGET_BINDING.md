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
ceilings total CNY 878.837760, while the parent remains jointly constrained by
the CNY 878.837759 spendable amount and CNY 0.000001 safety reserve.  A
reservation, charge, settlement, cancellation, and block carries the
task-run allocation identity when the Controlled Pilot contract is active.

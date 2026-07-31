# CodePaceX 当前状态快照：P3-B-A2 失败闭环

> 文档版本：v3.4.0  
> 生成时间：2026-07-31 17:40（UTC+8）  
> 状态：当前有效  
> 语言：中文

## 当前结论

P3-B-A2 唯一 paid dispatch 已执行一次并在 paid executor 身份校验阶段 fail-closed。没有 task-run、Provider transport、Artifact、ledger reservation 或费用产生。当前状态不是 paid success，也不满足 P4 入口。

```yaml
status: P3-B_A2_FAIL_CLOSED_PRE_EXECUTION
formal_main_at_dispatch: 4a5b66787f1d7ab0619b86eb3c9fb7f0f7b6cd72
paid_run: 30620506129
paid_job: 91123686219
paid_job_result: failure
internal_run_id: p3b-paid-v2-20260731t091733z-4a5b6678-8976a0b3
artifact_count: 0
task_runs_started: 0
provider_requests: 0
usage: 0
charge_cny: 0
active_reservation: null
secret_value_read: false
p4: blocked
```

## 根因边界

本次 A2 的精确失败原因为 `authorization_acknowledgement_contract_prefix_mismatch`：授权输入使用 `P3B_PAID_AUTHORIZATION_V2:`，而当前正式 executor 合同要求 `P3B_PAID_AUTHORIZATION:`。它与历史机器标签 `blocked_preflight_task_environment_missing` 及已修复的 `production_adapter_argument_shape_mismatch` 分开记录。

## 后续限制

- 不得 retry、rerun、continuation、fallback 或第二次 A2 dispatch。
- 不得复用本次 acknowledgement、dispatch token 或 internal run ID。
- 不得修改代码、任务、预算、freeze、allocation、Provider、endpoint 或 evaluator。
- 只有用户新的明确付费授权才能开启下一次身份核验；在此之前不启动 P4。

# CodePaceX P3-B0 重绑定冻结与预算说明

> **版本**：v1.0.0
> **状态**：PR 待审核
> **性质**：zero-provider post-merge rebind；不是 P3-B 付费授权。

## 正式绑定

P3-B freeze 绑定正式合并后的 main：

```text
2794e27220d3fada3bd0fdd3a1a14ff50e3a6034
```

它继承 P3-A 的四个任务、配对顺序、模型、Prompt、Provider、endpoint、evaluator、Pricing、每 run 40 次请求上限、`retry=0`、`fallback=false`、严格串行和 Agent V3.1 算法。P3-A 的 `bound_main_commit=9e076874...` 仅保留为历史证据，不能成为 P3-B 的付费授权基线。

P3-B freeze 还绑定本模块、唯一 P3-B workflow、paid gate、P3-A 历史冻结以及 Agent 运行时源文件的 SHA-256。freeze 的文件字节 SHA-256 与 canonical object SHA-256 分别记录，不能混用。

## 预算草案

```yaml
parent_cap_proposal: CNY 292.945921
child_cap_each: CNY 36.618240
child_count: 8
spendable_total: CNY 292.945920
safety_reserve: CNY 0.000001
```

闭合式为：`8 * CNY 36.618240 + CNY 0.000001 = CNY 292.945921`。

这是一份 proposal，未授予任何 Provider 调用权限。历史机械理论上限 `CNY 585.891840` 不是推荐的实际授权额度。

## 未来唯一 dispatch

唯一 workflow 为 `.github/workflows/p3b-paired-pilot.yml`，唯一 paid job 为 `p3b-paid-execution`。未来调用必须同时提供：

```text
paid_execution=true
expected_freeze_sha256=<main 上 freeze 文件哈希>
expected_allocation_hash=<main 上 allocation 哈希>
approved_parent_cap_cny=292.945921
authorization_acknowledgement=<新的用户明确授权文本>
dispatch_token=<唯一、不可重用 token>
run_id=<唯一、不可重用 run id>
```

workflow concurrency 与持久化 `DispatchGuard` 同时阻止重放和第二次 dispatch。任何不满足输入、绑定、预算、账本或 request ceiling 的情况均 fail-closed；不会 retry、rerun 或 continuation。

## 零提供商验收

readiness 演练必须经由 manifest、严格串行 runner、treatment flag、Agent request assembly、recording fake transport、raw Artifact、evaluator 接口、ledger 和 4-pair merge。演练的 Provider requests、Usage、charge 均为零，且 `active_reservation=null`。CI 仅检查 Secret presence 的布尔值，不向步骤暴露 Secret 值。

## 未来授权模板

```text
我明确批准一次 P3-B 严格配对 Pilot 付费执行：仅在 main 的 P3-B freeze 文件 SHA-256 为 <freeze_sha256>、allocation SHA-256 为 <allocation_hash>、bound_main_commit 为 2794e27220d3fada3bd0fdd3a1a14ff50e3a6034 时执行。总 parent cap 为 CNY 292.945921，8 个 child cap 各为 CNY 36.618240，safety reserve 为 CNY 0.000001。仅允许 workflow p3b-paired-pilot.yml 的单一 dispatch_token=<token> 和 run_id=<run_id>，strict serial、retry=0、fallback=false、每 run request ceiling=40。不得执行第二次 dispatch、rerun、continuation、P4、Tag 或 Release；出现预算、账本、Provider Usage、Artifact 或基础设施 fail-closed 条件时立即停止。
```

在 PR 合并并获得上面新的明确付费授权之前，P3-B 仍为 blocked。

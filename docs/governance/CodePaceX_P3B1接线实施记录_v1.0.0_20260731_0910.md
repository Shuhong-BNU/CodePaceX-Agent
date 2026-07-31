# CodePaceX P3-B1 接线实施记录

> 版本：v1.0.0
> 时间：2026-07-31 09:10（UTC+8）
> 状态：P3-B1 zero-provider PR 待审核
> 正式基线：`origin/main@3e18174bd110502d8b8baecd67c12027a8b2520a`

## 本次交付

- 新增 `evals.evaluation_v2.p3b_paid_executor`：唯一的 P3-B 付费入口。
- `p3b-paid-execution` 不再无条件 `exit 1`；它只会在 `workflow_dispatch`、`main`、`paid_execution=true`、精确 freeze / allocation hash、`CNY 292.945921`、授权确认、dispatch token、run ID 与 Secret presence 均通过时调用入口。
- 入口逐项核验 main、冻结文件字节哈希、allocation hash、parent cap、授权确认、单次 dispatch 与安全 run ID；任何不匹配均在 Provider 边界前失败。
- 正式路径复用 `full_replay._full_task_executor`、`PaidRunGate`、现有 P3-B0 freeze / Stage-C allocation / paired merge，而未新建平行预算或 Artifact 系统。

## 执行与账本合同

- 8 个冻结 task-run 严格串行；每次进入下一 run 前，当前 ledger 必须无 active reservation。
- 每个 Provider 请求沿用 `PaidRunGate` 的预 reservation、child cap、parent cap、40 次 request ceiling、Usage settlement 与 Usage 缺失保守结算。
- `retry=0`、`fallback=false`、自动 rerun / continuation 禁止。
- 8 run 全部终态且 ledger 关闭后，才写出 4 组 paired merge；缺少 raw Artifact、V2 携带 V3 Artifact、V3 缺少激活 Artifact、缺少 pair 或 open reservation 都 fail-closed。
- 唯一 paid Artifact 包含 freeze、authorization、allocation、ledger、dispatch guard、8-run 原始 Artifact、Candidate、predictions、evaluator report、task result、terminal summary 和 paired results。

## Zero-provider 验证边界

录制 fake executor 通过与正式入口相同的编排、Stage-C child identity、reservation / settlement、Artifact 完整性和 paired merge 路径执行 8 run / 32 次模拟请求。该测试只记录模拟 Usage 与模拟账本费用：外部 Provider requests、真实 Usage、真实 charge 均为 `0`，未读取 Secret 值。

## 决策

P3-B1 仅提供默认关闭的付费执行能力，并不构成 P3-B 付费授权。PR 合并及 post-merge 只读核验完成前，P3-B 仍被阻塞；P4 也仍被阻塞。

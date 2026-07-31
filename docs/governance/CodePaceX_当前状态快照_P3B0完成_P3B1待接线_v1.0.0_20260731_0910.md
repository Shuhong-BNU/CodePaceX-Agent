# CodePaceX 当前状态快照：P3-B0 已完成，P3-B1 真实执行器待接线

> 文档版本：v1.0.0  
> 时间：2026-07-31 09:10（UTC+8）  
> 状态：当前有效  
> 语言：中文  
> 正式 main：`3e18174bd110502d8b8baecd67c12027a8b2520a`

## 当前阶段

```yaml
P0: 已完成
P1: 已完成
P2: 已完成
P3-A: 已完成
P3-B0: 已完成
P3-B1: 待实施
P3-B: 被 P3-B1 阻塞
P4: 被 P3-B 阻塞
```

PR #76 已通过普通 merge commit 合并，CI、P3-B zero-provider readiness 均成功，paid job skipped，Provider / Usage / charge 为 `0 / 0 / CNY 0`。

## 关键发现

合并后的 `.github/workflows/p3b-paired-pilot.yml` 虽有 `p3b-paid-execution` job，但当前仍是故意 fail-closed 的占位门，最终执行 `exit 1`。

`evals/evaluation_v2/p3b_post_merge_rebind.py` 的唯一可执行路径也是 recording-fake zero-provider rehearsal，没有真实 Provider runner、正式 paid settlement、真实 evaluator 和最终 paid Artifact 收口入口。

因此当前不能直接进行 P3-B 付费授权。直接 dispatch 只会得到预期失败，不会执行 8 个真实 task-run。

## P3-B0 已证明

- freeze、8-run、4-pair 和 treatment 顺序可冻结；
- parent cap `CNY 292.945921`；
- 8 个 child cap 各 `CNY 36.618240`；
- second-dispatch 可拒绝；
- strict serial 结构可演练；
- raw Artifact、evaluator interface、ledger 和 paired merge 的形状可验证；
- Provider 前门默认关闭；
- zero-provider CI 可通过。

## P3-B0 未证明

- 真实 Provider dispatch；
- 真实 Usage 与费用结算；
- 真实任务 workspace；
- 真实官方 evaluator；
- 8-run paid terminal Artifact；
- V2/V3 的 L4 行为差异。

## 下一步

实施 P3-B1：真实付费执行器接线 PR。

P3-B1 必须在 zero-provider 条件下新增并验证：

- 正式 8-run paid runner；
- workflow paid job 调用真实 runner，而不是 `exit 1`；
- parent / 8 child reservation 与 settlement；
- Usage 缺失保守结算；
- 真实 task checkout / workspace；
- evaluator；
- Candidate、V3 Artifact、4-pair merge；
- 唯一 paid Artifact；
- paid job 在普通 PR/CI 中继续 skipped。

## 时间预估

| 阶段 | 正常耗时 | 保守耗时 |
|---|---:|---:|
| P3-B1 接线 PR | 2～4 小时 | 4～6 小时 |
| P3-B 真实 8-run | 3～8 小时 | 4～10 小时 |
| P4 fresh holdout | 8～20 小时 | 1～2 个自然日 |

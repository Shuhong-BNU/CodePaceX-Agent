# CodePaceX 后续整体工作方案

> 版本：v1.4.0  
> 时间：2026-07-31 09:10（UTC+8）  
> 状态：当前有效  
> 语言：中文

## 总路线

```text
P0     V3.0 正式收口                     已完成
P1     V3.1 机制实现                     已完成
P2     L1-L3 zero-provider 验收          已完成
P3-A   4×2 配对 Pilot 冻结               已完成
P3-B0  post-merge rebind                 已完成
P3-B1  真实 paid runner 接线             下一步
P3-B   4×2 真实付费配对 Pilot            被阻塞
P4     8×2 fresh holdout                 被阻塞
```

正式 main：

```text
3e18174bd110502d8b8baecd67c12027a8b2520a
```

## 为什么必须新增 P3-B1

当前 workflow 的 paid job 输入门完整，但最终主动 `exit 1`。当前 P3-B 模块能生成 freeze 和 fake rehearsal，却没有真实 paid executor。

因此 P3-B0 是 readiness freeze，不是可付费执行终态。

## P3-B1 范围

必须继承：

- 4 个任务、8 个 run；
- 原 treatment 顺序；
- 模型、Prompt、Provider、endpoint；
- evaluator、Pricing；
- request ceiling=40/run；
- retry=0；
- fallback=false；
- strict serial；
- parent cap `CNY 292.945921`；
- child cap `CNY 36.618240`。

必须新增：

- 正式 paid CLI；
- workflow 调用真实 executor；
- authorization acknowledgement；
- dispatch token / run ID 持久防重；
- 每 run reservation / settlement；
- Provider Usage；
- Usage 缺失保守结算；
- 真实 task workspace；
- evaluator；
- Candidate；
- V3 raw Artifact；
- 8-run summary；
- 4-pair merge；
- ledger closure；
- paid Artifact 上传。

## P3-B1 出口门

- paid job 不再无条件 `exit 1`；
- PR CI 中 paid job 仍 skipped；
- fake Provider 下走同一真实 executor 路径；
- duplicate / second dispatch 拒绝；
- child / parent cap 生效；
- request ceiling 生效；
- Usage missing 时 fail-closed；
- Artifact / pair / ledger 负向测试通过；
- Provider / Usage / charge 为 0；
- Secret 值未读取；
- CI 全绿；
- review 无阻塞；
- 不自动合并。

## P3-B 付费授权门

P3-B1 合并后，先做一次只读 post-merge 核验，再由用户单独批准：

```text
exactly one paid workflow
exactly 8 task-runs
no retry
no rerun
no continuation
no second dispatch
no P4
```

## P3-B 预期结果

- 8 个 run 的终态或明确 fail-closed 停止点；
- raw Agent request；
- Candidate；
- V3 events / summary / final.patch；
- evaluator report；
- Usage / charge；
- ledger；
- active_reservation=null；
- 4 个 V2/V3 pair；
- resolved / unresolved / infrastructure_error；
- L4 行为差异；
- 是否进入 P4。

## P4 进入条件

- AdvicePresentInRequest=100%；
- 无 venv/site-packages 污染；
- 无历史 resolved 回退；
- 无账本或协议死锁；
- 至少 1 个结果改善，或 2 个预注册近端行为改善；
- 费用与 Usage 可解释。

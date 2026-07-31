# CodePaceX 后续整体工作方案

> **方案版本**：v1.3.0
> **生成时间**：2026-07-31 00:40（UTC+8）
> **状态**：当前有效
> **语言**：中文
> **上一版本**：`CodePaceX_后续整体工作方案_v1.2.2_20260730_2359.md`
> **变更性质**：MINOR——在 P3-A 与 P3-B 之间正式新增 P3-B0 post-merge rebind 阶段。

---

# 0. 总路线

```text
P0   V3.0 正式收口                     已完成
P1   V3.1 Activation Fidelity          已完成
P2   zero-provider L1-L3 验收          已完成
P3-A 4×2 Pilot 冻结与 readiness        已完成
P3-B0 post-merge paid rebind           已实施，PR 待审核
P3-B 4×2 真实付费严格配对 Pilot        被阻塞
P4   8×2 fresh holdout                 被阻塞
```

当前正式 main：

```text
2794e27220d3fada3bd0fdd3a1a14ff50e3a6034
```

---

# 1. P3-B0 的目标

P3-B0 只解决“合并后的正式 Harness 如何安全执行 P3-B”：

- 新 P3-B freeze 绑定当前 main；
- 将 P3-B runner 和 workflow 自身纳入 freeze；
- 建立正式 Stage-C parent authorization；
- 建立 8 个正式 child allocations；
- 建立唯一 workflow；
- 建立唯一 dispatch token / run identity；
- 建立一次 dispatch 防护；
- 建立 strict serial 8-run runner；
- 建立完整 4-pair merge；
- 建立 ledger 和 Artifact 终态断言；
- 完成 zero-provider readiness。

不改变：

- 4 个任务；
- treatment 顺序；
- 模型；
- Prompt；
- Provider；
- evaluator；
- Pricing；
- request ceiling；
- retry / fallback；
- Agent 能力算法。

---

# 2. P3-B0 出口门

只有以下全部满足，P3-B 才能进入付费授权：

- [ ] 新 freeze 精确绑定合并后的 clean main；
- [ ] workflow checkout 与 authorization experiment commit 一致；
- [ ] 8 个 task-run identity 唯一；
- [ ] parent / child allocation 哈希闭合；
- [ ] 8 个 child cap 与 parent cap 闭合；
- [ ] 单次 dispatch 防护通过；
- [ ] paid job 默认 skipped；
- [ ] zero-provider rehearsal 走通 runner；
- [ ] Provider requests / Usage / charge = 0；
- [ ] Secret 仅 presence 检查；
- [ ] 4 pair / 8 run 完整性断言通过；
- [ ] ledger `active_reservation=null`；
- [ ] CI 全绿；
- [ ] PR review 无阻塞；
- [ ] P4 未启动。

---

# 3. P3-B 真实执行合同

未来 P3-B 必须是：

```text
exactly one paid workflow
8 task-runs
strict serial
retry=0
fallback=false
request ceiling=40/run
no automatic rerun
no continuation
no second dispatch
no checkov retry
no P4
```

建议授权草案：

```yaml
parent_cap: CNY 292.945921
child_cap_each: CNY 36.618240
spendable_total: CNY 292.945920
safety_reserve: CNY 0.000001
```

该额度必须在 P3-B0 合并后的正式 Artifact 中重新核验，之后由用户单独批准。

---

# 4. P3-B 预期结果

P3-B 应输出：

- 8/8 task-run 终态，或明确的 fail-closed 停止点；
- 每 run 的 raw Agent Artifact；
- V3 activation 事件；
- Candidate；
- evaluator 报告；
- Usage 与费用；
- 8 个 ledger identity；
- `active_reservation=null`；
- 4 个 V2/V3 pair；
- resolved / unresolved / infrastructure error；
- 行为级 L4 指标；
- 是否满足进入 P4 的门。

---

# 5. P4 的定位

P4 是 fresh holdout：

```text
8 个未参与 Goal 4、P1/P2、P3 的新任务
×
V2_CONTROL / V3_CORE
=
16 个 task-run
```

P4 需要独立的：

- task selection；
- freeze；
- readiness；
- budget；
- paid authorization；
- Artifact 审计。

P4 不能由 P3-B 自动触发。

---

# 6. 时间规划

| 工作 | 预计耗时 |
|---|---:|
| P3-B0 rebind PR | 1.5～5 小时 |
| P3-B paid workflow | 3～8 小时 |
| P3-B 审计 | 0.5～1.5 小时 |
| P4 准备 | 2～5 小时 |
| P4 paid workflow | 6～16 小时 |
| P4 审计 | 1～3 小时 |

在没有基础设施故障的情况下，从现在到 P3-B 结论通常可在 1 天内完成；到 P4 正式结论通常还需额外 1～2 天。

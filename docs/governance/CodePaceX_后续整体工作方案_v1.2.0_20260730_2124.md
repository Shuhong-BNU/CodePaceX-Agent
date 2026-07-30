# CodePaceX 后续整体工作方案

> **方案版本**：v1.2.0  
> **生成时间**：2026-07-30 21:24（UTC+8）  
> **状态**：Current  
> **上一版本**：`CodePaceX_后续整体工作方案_v1.1.1_20260730.md`  
> **变更性质**：MINOR——P1/P2 已完成，下一阶段拆分为 P3-A 零 Provider 准备与 P3-B 单次付费 Pilot；不改变 P4 的 fresh holdout 定位。  
> **当前远端基线**：`main@9e076874894ccf155d990fa8a176b2191e258652`

# 0. 当前结论

```text
P0 V3.0 正式收口                 Completed
P1 V3.1 Activation Fidelity      Completed
P2 zero-provider activation      Completed
P3 L4 paired Pilot               Not started
P4 fresh screening holdout       Blocked by P3
```

P1/P2 已证明 L1–L3：

- 模块已构造；
- 机制产物已生成；
- Advice 已进入真实请求装配链路。

尚未证明 L4：

- 模型是否利用 Evidence 改变决策；
- 是否减少契约错误；
- 是否减少回归；
- 是否提高 resolved rate。

# 1. 下一阶段拆分

## P3-A：严格配对 Pilot 准备与 zero-provider readiness

**性质**：零 Provider、无费用、一个准备 PR。

目标：

- 冻结四个 development tasks；
- 冻结 V2_CONTROL / V3_CORE treatment；
- 冻结交错执行顺序；
- 冻结模型、Prompt、Provider、evaluator、任务/base commit；
- 冻结每题 40 请求、retry=0、fallback=false；
- 生成 8 个唯一 task-run identities；
- 生成一份 parent authorization 草案和 8 个 child allocation；
- 用 fake/zero-provider 走通完整 dispatch、Agent flag、Artifact、ledger 和 result-merge 接线；
- 从冻结 Pricing 和历史 Usage 生成保守预算建议；
- 创建一个 PR，CI 通过后停止。

P3-A **不得**派发 paid workflow。

## P3-B：一次授权的 paid paired Pilot

只有 P3-A 合并并通过 post-merge zero-provider readiness 后，用户再明确授权：

```text
exactly one paid paired-Pilot workflow
8 task-runs
no automatic retry/rerun/continuation
```

终态后审计唯一 Artifact，输出 V2/V3 成对结果。

# 2. P3 冻结任务

| Pair | Task | 主要失败面 | 交错顺序 |
|---:|---|---|---|
| 1 | `beetbox__beets-5457` | Python/runtime compatibility | V2 → V3 |
| 2 | `deepset-ai__haystack-8489` | backend / behavior contract | V3 → V2 |
| 3 | `dynaconf__dynaconf-1249` | default / config surface | V2 → V3 |
| 4 | `delgan__loguru-1297` | exception / boundary contract | V3 → V2 |

总计：

```text
4 tasks × 2 treatments = 8 task-runs
```

这些题属于 development/regression set，不是 fresh holdout。

# 3. P3 主要问题

P3 只回答：

> 在同一 main、同一任务、同一模型、同一 Prompt、同一 Provider、同一 evaluator 和相同请求合同下，仅开启 V3.1 treatment，是否带来可解释的 Agent 行为或结果改善？

它不回答统计显著性，也不证明泛化。

# 4. P3 预注册指标

第一优先级：

- V3 Advice 是否确实出现在请求中；
- Agent 是否引用有效 repository evidence；
- 是否有 Hypothesis 被工具证据 reject；
- impacted-test precision；
- baseline/post differential；
- C2/C3 Candidate；
- Python/runtime compatibility 回归；
- collection/global regression；
- request-ceiling finalization。

第二优先级：

- resolved；
- unresolved；
- infrastructure error；
- requests；
- input/output/reasoning tokens；
- cost；
- V2→V3 近端失败变化；
- resolved→unresolved 回退。

# 5. P3-A zero-provider 硬门

- [ ] 8 个唯一 task-run identity；
- [ ] 4 个任务的两种 treatment 除 flag 外完全一致；
- [ ] treatment 顺序预注册；
- [ ] strict serial；
- [ ] retry=0；
- [ ] fallback=false；
- [ ] request ceiling=40；
- [ ] model / Prompt / Provider / evaluator / pricing 全部冻结；
- [ ] 8 个 child allocations 唯一且不可复用；
- [ ] Provider requests / Usage / charge = 0；
- [ ] Secret read = false；
- [ ] paid jobs 全部 skipped；
- [ ] Artifact 包含 activation、Candidate、evaluator 和 ledger 接线；
- [ ] conservative budget proposal 已生成；
- [ ] 没有 P4 或 full-20 dispatch。

任一项未通过，不得请求 P3-B 付费授权。

# 6. P3-B 进入 P4 的门

建议同时满足：

- 8 个 task-runs 全部形成可审计终态，或至多 1 个明确 infrastructure error；
- V3 适用任务 `AdvicePresentInRequest=100%`；
- venv/site-packages contamination=0；
- 至少 3/4 个 V3 task 形成 C2/C3；
- 无历史 resolved/control 发生 resolved→unresolved；
- 至少 1 个新增 resolved，或至少 2 题出现清晰、由证据支持的近端失败改善；
- 没有 Stage C 式协议死锁；
- 成本变化可解释。

未满足时回到 P1/P2，不进入 P4。

# 7. P4 保持不变

P4 为：

```text
8 fresh tasks × V2_CONTROL / V3_CORE
```

它必须使用未参与 Goal 4、失败分析、V3 设计、P2 和 P3 的新任务。

# 8. 当前工作看板

| 工作包 | 状态 | 证据 | 下一动作 |
|---|---|---|---|
| P0 | Completed | PR #72 / `ce15722…` | 无 |
| P1/P2 | Completed | PR #73 / `9e0768…` | 无 |
| P3-A | Ready | 本方案 | 创建 zero-provider 准备 PR |
| P3-B | Blocked | 需要 P3-A + 新付费授权 | 暂不派发 |
| P4 | Blocked | 需要 P3-B 信号 | 暂不设计 |

# 9. 变更记录

## v1.2.0 — 2026-07-30 21:24（UTC+8）

- 将 P1/P2 标记为 Completed；
- 新增 post-merge current snapshot；
- 将 P3 拆成 P3-A zero-provider preparation 与 P3-B paid Pilot；
- 冻结 4 个 development tasks 和交错 treatment 顺序；
- 明确 P3-A 先生成预算建议、不得自动付费；
- 保持 P4 fresh screening holdout 不变。

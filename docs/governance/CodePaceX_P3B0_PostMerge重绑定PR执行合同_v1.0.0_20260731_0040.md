# CodePaceX P3-B0 Post-Merge 重绑定 PR 执行合同

> **合同版本**：v1.0.0
> **生成时间**：2026-07-31 00:40（UTC+8）
> **状态**：可执行
> **语言**：中文
> **正式基线**：`origin/main@2794e27220d3fada3bd0fdd3a1a14ff50e3a6034`
> **性质**：zero-provider engineering only
> **禁止**：不得执行 P3-B paid Pilot

---

# 一、唯一目标

从当前正式 main 创建隔离 worktree 和一个 zero-provider PR，完成 P3-B post-merge rebind。

建议分支：

```text
codex/p3b-post-merge-rebind
```

建议 PR 标题：

```text
feat(evals): bind P3-B paired Pilot to merged main
```

---

# 二、必须继承的实验条件

不得改变：

1. `beetbox__beets-5457`：V2_CONTROL → V3_CORE
2. `deepset-ai__haystack-8489`：V3_CORE → V2_CONTROL
3. `dynaconf__dynaconf-1249`：V2_CONTROL → V3_CORE
4. `delgan__loguru-1297`：V3_CORE → V2_CONTROL

固定：

```yaml
模型: qwen3.7-max-2026-06-08
Provider: bailian-qwen37-max
协议: openai-compat
Prompt: goal3-swe-inference-prompt-v1
Evaluator commit: ad79b850f15e33992e96f03f6e97f05ddf9aa0be
数据集: SWE-bench-Live/SWE-bench-Live
split: lite
Pricing hash: a09eb6e6955b9fb68d3e011771c948f7a14b7bbca5316a2433cab099d0b643d3
strict serial: true
retry: 0
fallback: false
request ceiling: 40/run
```

---

# 三、必须新增的正式 P3-B 资产

1. P3-B paid freeze；
2. 8-run manifest；
3. treatment-order manifest；
4. 正式 Stage-C parent authorization；
5. 8 个正式 child allocations；
6. 唯一 workflow；
7. 唯一 paid job；
8. 唯一 dispatch token / run identity；
9. 防止第二次 dispatch 的验证；
10. strict serial 8-run runner；
11. raw Artifact 收集；
12. evaluator 报告收集；
13. 4-pair merge；
14. ledger 完整性；
15. 终态 Artifact schema；
16. zero-provider readiness Artifact。

新 freeze 必须包含：

- 当前合并后的 `bound_main_commit`；
- 新增 workflow / runner 自身哈希；
- 所有 runtime source hash；
- model / Prompt / Provider / evaluator / Pricing；
- task/base commit；
- authorization / allocation identity；
- 预算；
- 停止合同。

---

# 四、预算合同

使用：

```yaml
parent_cap_proposal: CNY 292.945921
child_cap_each_proposal: CNY 36.618240
spendable_total: CNY 292.945920
safety_reserve: CNY 0.000001
```

必须证明：

```text
8 × child_cap + safety_reserve = parent_cap
```

预算仍为 proposal，不是授权。

不得使用机械理论上限 `CNY 585.891840` 作为默认授权。

---

# 五、停止与 fail-closed 合同

- Provider 前先验证 child / parent / safety cap；
- 每次只能有一个 active reservation；
- Usage 不可确认时保守结算并停止整轮；
- accounting failure 停止整轮；
- active reservation failure 停止整轮；
- infrastructure failure 在账本闭合后按预注册合同决定是否继续；
- request ceiling 必须形成终态；
- 保留已导出的最佳 Candidate；
- 不 retry；
- 不 rerun；
- 不 continuation；
- 不第二次 dispatch。

---

# 六、zero-provider 验收

必须通过：

- workflow dry/readiness；
- paid job skipped；
- Secret presence only；
- Provider requests / Usage / charge = 0；
- fake transport / runner rehearsal；
- 8-run identity；
- 8 child allocations；
- parent-child budget closure；
- paired merge；
- ledger closure；
- Artifact 完整性；
- duplicate dispatch rejection；
- missing pair rejection；
- missing run rejection；
- unexpected run rejection。

---

# 七、工作区保护

原始本地 main 存在用户修改。

不得：

- reset；
- stash；
- clean；
- rebase；
- 同步；
- 覆盖用户文件。

必须从远端正式 main 建立隔离 worktree。

---

# 八、文档要求

新增长期文档全部使用中文。

在 PR 中更新：

- 当前状态快照；
- 后续整体方案；
- 总索引；
- P3-B0 rebind freeze 说明；
- 预算说明；
- 用户未来精确付费授权模板。

保留旧 P3-A Artifact，不改写历史。

---

# 九、最终停止点

创建 PR、等待 CI、处理 review 后停止。

不得：

- 合并 PR；
- 触发 paid workflow；
- 调用 Provider；
- 执行真实 task-run；
- 启动 P4；
- 创建 Tag / Release。

---

# 十、可直接发给 Codex 的指令

我明确授权：从 `origin/main@2794e27220d3fada3bd0fdd3a1a14ff50e3a6034` 创建隔离 worktree 和一个 zero-provider P3-B post-merge rebind PR。

请完整执行本合同。

本轮只新增并校验：

- P3-B paid freeze；
- 8-run manifest；
- treatment order；
- 唯一 paid workflow；
- 唯一 dispatch / second-dispatch 防护；
- strict serial 8-run runner；
- 正式 Stage-C parent authorization；
- 8 个正式 child allocations；
- parent/child budget closure；
- raw Artifact/evaluator/ledger/paired merge 终态合同；
- zero-provider readiness；
- 中文治理文档。

实验条件必须继承 P3-A，不得改变任务、顺序、模型、Prompt、Provider、evaluator、Pricing、request ceiling、retry、fallback 或 Agent 能力算法。

预算 proposal：

```text
parent cap = CNY 292.945921
8 child caps = 每个 CNY 36.618240
spendable total = CNY 292.945920
safety reserve = CNY 0.000001
```

严格禁止：

- Provider 调用；
- Secret 值读取；
- paid workflow；
- 真实 task-run；
- retry / rerun / continuation；
- 第二次 dispatch；
- P4；
- Tag / Release；
- 自动合并；
- 修改原始本地 main。

完成一个 PR并等待全部 CI 与 review 终态后停止，汇报：

- bound main；
- branch / commits / PR；
- freeze 与所有 identity hash；
- workflow 和 dispatch 参数；
- 8-run manifest；
- parent / child allocations；
- 预算闭合；
- zero-provider readiness；
- Provider / Usage / charge；
- Secret presence；
- CI / review；
- 是否建议合并；
- P3-B 是否仍 blocked。

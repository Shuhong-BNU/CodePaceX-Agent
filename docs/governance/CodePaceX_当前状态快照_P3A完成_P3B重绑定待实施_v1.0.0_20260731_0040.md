# CodePaceX 当前状态快照：P3-A 已完成，P3-B 重绑定待实施

> **文档版本**：v1.0.0
> **记录时间**：2026-07-31 00:40（UTC+8）
> **状态**：当前有效
> **语言**：中文
> **仓库**：`Shuhong-BNU/CodePaceX-Agent`
> **当前正式 main**：`2794e27220d3fada3bd0fdd3a1a14ff50e3a6034`

---

# 1. 当前阶段

```yaml
P0_V3正式收口: 已完成
P1_V3_1机制实现: 已完成
P2_L1到L3零Provider验收: 已完成
P3_A严格配对Pilot准备: 已完成
P3_B0_post_merge重绑定: 已实施，PR 待审核
P3_B真实付费配对Pilot: 被P3_B0阻塞
P4_fresh_holdout: 被P3_B阻塞
```

P3-A 通过 PR #74 合并：

```text
merge commit:
2794e27220d3fada3bd0fdd3a1a14ff50e3a6034
```

P3-A 已证明：

- 8-run manifest 可闭合；
- 4 个 pair 可严格合并；
- 真实 zero-provider rehearsal 可走完整 Agent、Artifact、evaluator 与 ledger 链路；
- Provider requests / Usage / charge 为 `0 / 0 / CNY 0`；
- Secret 未读取；
- CI 成功；
- review 已解决。

---

# 2. 为什么还不能直接执行 P3-B

P3-A 的历史 freeze 绑定：

```text
bound_main_commit:
9e076874894ccf155d990fa8a176b2191e258652
```

当前正式 main 为：

```text
2794e27220d3fada3bd0fdd3a1a14ff50e3a6034
```

并且当前仓库中尚不存在：

- 正式 P3-B paid freeze；
- 正式 Stage-C parent authorization；
- 8 个正式 child allocations；
- 唯一 P3-B paid workflow；
- P3-B 8-run paid runner；
- 单次 dispatch 防护；
- 终态 Artifact 完整性断言。

因此，P3-A freeze 只能作为历史 readiness 证据，不能直接被当作 P3-B paid freeze。

---

# 3. 当前冻结实验条件

以下实验条件可从 P3-A 原样继承：

```yaml
模型: qwen3.7-max-2026-06-08
Provider: bailian-qwen37-max
协议: openai-compat
Prompt: goal3-swe-inference-prompt-v1
Prompt系统哈希: f43af3f8…
Evaluator: SWE-bench-Live@ad79b850…
数据集: SWE-bench-Live/SWE-bench-Live
split: lite
Pricing哈希: a09eb6e…
strict_serial: true
retry: 0
fallback: false
每run请求上限: 40
```

顺序：

1. `beetbox__beets-5457`：V2_CONTROL → V3_CORE
2. `deepset-ai__haystack-8489`：V3_CORE → V2_CONTROL
3. `dynaconf__dynaconf-1249`：V2_CONTROL → V3_CORE
4. `delgan__loguru-1297`：V3_CORE → V2_CONTROL

---

# 4. 当前预算草案

```yaml
历史预期费用: CNY 74.592144
保守预算草案: CNY 292.945920
机械理论上限: CNY 585.891840
建议未来parent授权草案: CNY 292.945921
建议8个child_cap: 每个CNY 36.618240
可实际消费合计: CNY 292.945920
安全预留: CNY 0.000001
```

这些仍是草案，不是当前付费授权。

---

# 5. 下一步

下一步只实施：

> P3-B0：zero-provider post-merge rebind PR。

该 PR 必须新增并冻结：

- `bound_main_commit=2794e272…` 的 P3-B freeze；
- 正式 parent authorization 结构；
- 8 个正式 child allocations；
- 唯一 P3-B paid workflow；
- 单次 dispatch 防护；
- 8-run paid runner；
- paired merge；
- ledger 终态断言；
- Artifact 完整性断言；
- zero-provider readiness；
- 中文治理文档。

P3-B0 不得调用 Provider，也不得执行真实 task-run。

---

# 6. 时间预估

## P3-B0 重绑定 PR

- Codex 实现与测试：约 1～3 小时；
- GitHub CI：约 10～40 分钟；
- 如出现 review 修复：额外约 30～90 分钟。

通常可在半天内完成。

## P3-B 真实付费配对 Pilot

- GitHub 严格串行执行 8 个 task-run：约 3～8 小时；
- Artifact 下载、完整性与 V2/V3 对照审计：约 30～90 分钟；
- 从授权到正式结论：通常约 4～10 小时。

这是工程估计，不是 SLA。网络、Provider 延迟、请求数和 evaluator 安装会显著影响时间。

## P4 fresh holdout

P4 预计为 8 个新任务 × 2 treatment = 16 个 task-run：

- 新任务筛选、冻结、readiness 和 PR：约 2～5 小时；
- 严格串行 paid run：约 6～16 小时；
- Artifact 审计与正式结论：约 1～3 小时；
- 通常需要约 1～2 个自然日完成。

P4 大致是 P3-B 的 2 倍运行量，并额外包含 fresh task 选择和冻结成本。

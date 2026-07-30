# CodePaceX 当前状态快照：P3-A 审查修复中

> **文档版本**：v1.0.0  
> **记录时间**：2026-07-30 22:50（UTC+8）  
> **状态**：当前有效  
> **仓库**：`Shuhong-BNU/CodePaceX-Agent`  
> **当前 PR**：#74  
> **当前分支**：`codex/p3a-paired-pilot-readiness`

---

# 1. 当前远端状态

```yaml
主分支基线: 9e076874894ccf155d990fa8a176b2191e258652
P3-A 分支提交: bd41d86765ee8a85667c3c3176e355d563f41cb8
PR: 74
PR 状态: OPEN
可合并状态: true
草稿状态: false
CI:
  macOS: 成功
  Ubuntu: 成功
P3-B: 未启动
Provider 请求: 0
Provider Usage: 0
Provider 费用: CNY 0
Secret 读取: false
```

## 重要纠正

虽然 PR #74 当前 CI 成功且 GitHub 显示可合并，但自动代码审查新增了 3 条尚未解决的意见：

1. **P1：真实 zero-provider 接线没有被实际演练。**
2. **P2：`freeze_sha256` 与实际冻结文件字节哈希不一致。**
3. **P2：paired-result 合并可能接受缺少整组 pair 的不完整结果。**

因此，当前不能把 P3-A 标记为 Completed，也不能合并 PR #74。

当前准确状态应为：

```text
P3-A 初版实现：完成
P3-A 初版 CI：通过
P3-A 代码审查：未通过
P3-A 审查修复：待执行
P3-B paid Pilot：继续阻塞
```

# 2. 已完成的工作

P3-A 初版已经完成：

- 4 个任务 × 2 个 treatment 的顺序冻结；
- 8 个唯一 task-run identity；
- strict serial；
- request ceiling = 40；
- retry = 0；
- fallback = false；
- 模型、Provider、Prompt、evaluator、Pricing 身份冻结；
- parent authorization 草案；
- 8 个 child allocation 草案；
- paired-result 基础合并逻辑；
- zero-provider readiness 初版；
- readiness Artifact 初版；
- expected / conservative / hard-cap 预算草案；
- v1.2.0 治理文档；
- PR #74 和初版 CI。

# 3. 尚未完成的工作

## 3.1 P1 阻塞：真实接线演练

当前 readiness 主要验证静态冻结、合成记录和 treatment flag 推导，但没有完整经过：

```text
真实 dispatch 入口
→ Agent treatment flag 传播
→ 真实 request assembly
→ zero-provider recording transport
→ 原始 V3 Artifact 生成与校验
→ evaluator 接线
→ paid-gate ledger 的零 Provider 闭合
→ paired result merge
```

修复后，只有真实演练 Artifact 存在且 ledger 闭合，才允许写：

```text
passed_zero_provider_readiness
```

## 3.2 P2 阻塞：冻结哈希语义不一致

需要区分：

```text
冻结 JSON 文件字节 SHA-256
冻结对象 canonical SHA-256
```

正式 `freeze_sha256` 应绑定实际写出的冻结文件字节。

如仍需 canonical hash，应使用独立字段，例如：

```text
freeze_canonical_sha256
```

## 3.3 P2 阻塞：不完整 pair 可被接受

paired merge 必须强制要求：

- 恰好 8 个冻结 task-run；
- 恰好 4 个 pair；
- 每个 pair 恰好包含一个 `V2_CONTROL`；
- 每个 pair 恰好包含一个 `V3_CORE`；
- 不允许缺失、重复或额外 task-run。

# 4. 当前预算状态

当前预算仅为草案：

```text
expected: CNY 74.592144
conservative: CNY 292.9459200
mechanical hard-cap proposal: CNY 585.891840
safety reserve: CNY 0.000001
```

这些数字目前：

- 不是用户授权；
- 不是可立即使用的 paid cap；
- 不能触发 P3-B；
- 需要在 P3-A 审查修复和合并后再次核验推导方式。

特别是机械 hard cap 明显高于 expected 和 conservative，应在付费授权前解释其用途，区分：

1. 理论绝对暴露上限；
2. 实际建议授权上限；
3. 工作流 fail-closed 上限。

# 5. 当前工作区边界

原始本地 `main` 保留用户修改，已知状态：

```text
ahead 1
behind 42
```

不得 reset、stash、clean、rebase、自动同步或覆盖用户修改。

# 6. 最近一步

最近一步不是合并 PR，也不是授权 P3-B。

最近一步是：

> 在 PR #74 的同一分支中修复 3 条代码审查意见，补充定向测试，重新生成冻结与 readiness Artifact，重新运行 CI，并解决全部 review thread。

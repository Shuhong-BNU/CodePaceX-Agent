# CodePaceX 后续整体工作方案

> **方案版本**：v1.2.1  
> **生成时间**：2026-07-30 22:50（UTC+8）  
> **状态**：当前有效  
> **上一版本**：`CodePaceX_后续整体工作方案_v1.2.0_20260730_2124.md`  
> **变更性质**：PATCH——不改变 P0→P4 总路线，只修正 P3-A 当前状态并增加 PR #74 审查修复门。

# 0. 当前阶段

| 阶段 | 当前状态 |
|---|---|
| P0：V3.0 正式收口 | 已完成 |
| P1：V3.1 Activation Fidelity | 已完成 |
| P2：zero-provider 激活验收 | 已完成 |
| P3-A：严格配对 Pilot 准备 | 审查修复中 |
| P3-B：一次付费严格配对 Pilot | 被 P3-A 阻塞 |
| P4：fresh screening holdout | 被 P3-B 阻塞 |

当前远端正式能力基线：

```text
main@9e076874894ccf155d990fa8a176b2191e258652
```

P3-A 当前 PR：

```text
PR #74
head bd41d86765ee8a85667c3c3176e355d563f41cb8
```

# 1. P3-A 当前评价

P3-A 初版已经具备：

- 冻结任务与 treatment 顺序；
- 8-run manifest；
- parent / child allocation 草案；
- paired result schema；
- zero-provider readiness 初版；
- budget proposal；
- PR 和 CI。

但当前仍有 3 个未解决的审查问题：

```text
P1：readiness 未实际演练完整运行接线
P2：freeze_sha256 未绑定冻结文件字节
P2：paired merge 未强制完整 4 pair / 8 run
```

因此：

```text
CI 通过 ≠ P3-A 完成
mergeable = true ≠ 应当立即合并
```

# 2. P3-A 修复阶段

## 2.1 修复真实 zero-provider 演练

readiness 必须实际经过：

```text
冻结 manifest
→ dispatch
→ treatment flag
→ Agent 请求装配
→ recording fake transport
→ V2/V3 Artifact
→ evaluator 接口
→ ledger 闭合
→ paired merge
```

只有 rehearsal run records、raw treatment Artifacts、closed ledger、zero Provider counters、paired merge result 和 readiness Artifact 均存在，才能判定通过。

## 2.2 修复冻结哈希

正式字段 `freeze_sha256` 必须是实际冻结文件字节 SHA-256。

可选增加 `freeze_canonical_sha256` 表示 canonical object hash。两种语义不得混用。

## 2.3 修复结果完整性

paired merge 必须严格校验预期和实际 task-run 集合，并拒绝缺失、重复和额外记录。

# 3. 修复后的验收门

PR #74 只有同时满足以下条件才可合并：

- [ ] P1 review 已修复；
- [ ] 两条 P2 review 已修复；
- [ ] 3 个 review thread 全部解决；
- [ ] 真实 zero-provider rehearsal 通过；
- [ ] rehearsal ledger 闭合；
- [ ] Provider requests / Usage / charge = 0；
- [ ] Secret read = false；
- [ ] `freeze_sha256` 与实际文件一致；
- [ ] 4 pair / 8 run 完整性强校验；
- [ ] 缺失、重复、额外记录的负向测试通过；
- [ ] macOS CI 成功；
- [ ] Ubuntu CI 成功；
- [ ] paid jobs 全部 skipped；
- [ ] 工作树干净；
- [ ] 不启动 P3-B。

# 4. PR #74 合并后

合并后只做一次只读 post-merge 核验。只有确认 P3-A Completed 后，才进入 P3-B 授权准备。

# 5. P3-B 付费授权前还需要做的事

必须区分 expected、conservative、theoretical/mechanical cap 和 authorized hard cap。当前 `CNY 585.891840` 不能自动视为建议授权额。

PR #74 合并后，应输出中文预算说明，包括每个 task/treatment 的 expected 与 conservative、parent cap、child cap、safety reserve、fail-closed 行为及达到 cap 后的终态。

# 6. 版本记录

## v1.2.1 — 2026-07-30 22:50（UTC+8）

- 将 P3-A 从 Ready 修正为“审查修复中”；
- 增加 PR #74 三条 review 阻塞；
- 增加真实 zero-provider rehearsal 门；
- 增加 freeze 文件哈希语义；
- 增加 4 pair / 8 run 强完整性校验；
- 将预算继续标记为草案；
- 明确当前不得合并、不得授权 P3-B。

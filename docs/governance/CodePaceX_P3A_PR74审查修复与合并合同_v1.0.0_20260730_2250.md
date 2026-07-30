# CodePaceX P3-A PR #74 审查修复与合并合同

> **合同版本**：v1.0.0  
> **生成时间**：2026-07-30 22:50（UTC+8）  
> **状态**：可执行  
> **目标 PR**：#74  
> **目标分支**：`codex/p3a-paired-pilot-readiness`  
> **当前 head**：`bd41d86765ee8a85667c3c3176e355d563f41cb8`

# 1. 唯一目标

在现有 PR #74 和现有分支中修复 3 条未解决的自动代码审查意见。

不得创建新 PR，不得启动 P3-B。

# 2. 必须修复的审查意见

## 2.1 P1：实际演练真实接线

必须新增或复用真实 zero-provider rehearsal，使其经过：

```text
正式 P3-A manifest
→ 正式 dispatch/runner 入口
→ treatment flag 传播
→ Agent request assembly
→ recording fake transport
→ V2_CONTROL / V3_CORE 原始 Artifact
→ evaluator 适配入口
→ paid-gate ledger 零 Provider 闭合
→ paired result merge
```

`passed_zero_provider_readiness` 必须依赖 rehearsal 执行成功、raw Artifact 存在、Artifact 校验通过、ledger closed、active reservation 为空、Provider requests/Usage/charge 为零以及 paired merge 完整。

## 2.2 P2：冻结文件哈希

`freeze_sha256` 必须来自写入磁盘的 `p3a-paired-pilot-freeze.json` 文件字节。

如仍需 canonical object hash，新增 `freeze_canonical_sha256`。

## 2.3 P2：完整 4 pair / 8 run 校验

paired merge 必须要求：

```text
expected task-runs = actual task-runs
pair count = 4
run count = 8
每个 pair treatments = {V2_CONTROL, V3_CORE}
```

必须拒绝缺失整个 pair、缺失单个 treatment、重复 task-run、重复 treatment、额外 task-run 和 task/pair key 不匹配。

# 3. 必须增加的测试

至少增加：

1. 没有执行 rehearsal 时 readiness 失败；
2. rehearsal 没有 raw Artifact 时失败；
3. rehearsal ledger 未关闭时失败；
4. Provider counter 非零时失败；
5. `freeze_sha256` 等于实际文件字节哈希；
6. canonical hash 与 file hash 使用不同字段；
7. 缺失整组 pair 被拒绝；
8. 缺失一个 treatment 被拒绝；
9. duplicate 被拒绝；
10. unexpected task-run 被拒绝；
11. 完整 4 pair / 8 run 成功；
12. V2_CONTROL 不携带 V3 Advice；
13. V3_CORE 携带 V3 activation schema。

# 4. 文档要求

从本轮开始，新增长期文档使用中文。

在同一 PR 中加入或更新中文当前状态快照、中文后续整体方案 v1.2.1、中文总索引 v1.2.1 和中文 P3-A 审查修复记录。

旧英文历史 Snapshot 不修改，只标记为历史截面。

# 5. 验证与合并门

完成后运行 P3-A 定向测试、controlled-Pilot 回归、V3 activation 回归、paired merge 负向测试、freeze hash 测试、完整本地套件、`git diff --check`、secret scan 和 Markdown 相对链接检查。

只有 3 条 review 全部修复、review threads 全部 resolved、CI 全绿、Provider/Usage/charge=0、Secret read=false、paid jobs skipped、P3-B 未启动且工作树干净时，才建议合并。

修复完成后停止，不自动合并。

# 6. 可直接发给 Codex 的指令

```text
请继续现有 P3-A 目标，但不要合并 PR #74。

当前 PR #74 虽然 CI 成功且可合并，但自动代码审查存在 3 条未解决意见：
1. P1：zero-provider readiness 没有实际演练 Agent flag、dispatch、原始 Artifact、evaluator 和 ledger 接线；
2. P2：freeze_sha256 没有绑定实际冻结 JSON 文件字节；
3. P2：paired-result merge 可能接受缺失整个 pair 的不完整结果。

我明确授权你继续使用现有隔离 worktree、分支
codex/p3a-paired-pilot-readiness
和 PR #74，修改、测试、提交并推送审查修复。
不要创建新 PR。

请完整执行我上传的：
CodePaceX_P3A_PR74审查修复与合并合同_v1.0.0_20260730_2250.md

核心要求：
- 真实 zero-provider rehearsal 必须经过正式 dispatch/runner、treatment flag、Agent request assembly、recording fake transport、原始 V2/V3 Artifact、evaluator 接口、ledger 闭合和 paired merge；
- 只有 rehearsal Artifact 存在、校验通过且 ledger 闭合，才能标记 readiness passed；
- freeze_sha256 改为实际冻结文件字节哈希；canonical hash 如需保留必须独立命名；
- paired merge 必须严格要求冻结 manifest 中完整的 4 pair / 8 run，并拒绝缺失、重复和额外记录；
- 补齐正向和负向测试；
- 更新后的长期文档全部使用中文；
- 将中文 v1.2.1 当前快照、整体方案、总索引和审查修复记录加入本 PR。

严格禁止 Provider 调用、Secret 读取、paid workflow、P3-B、P4、自动 retry/rerun/continuation、checkov-6893 补跑、Tag、Release、自动合并 PR，以及修改原始本地 main。

完成推送后等待新 CI 终态，解决 3 个 review thread，然后停止并汇报。
```

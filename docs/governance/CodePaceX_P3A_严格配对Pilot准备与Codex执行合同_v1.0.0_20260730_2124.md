# CodePaceX P3-A 严格配对 Pilot 准备与 Codex 执行合同

> **版本**：v1.0.0  
> **生成时间**：2026-07-30 21:24（UTC+8）  
> **状态**：Ready to execute  
> **远端基线**：`origin/main@9e076874894ccf155d990fa8a176b2191e258652`  
> **性质**：zero-provider preparation only  
> **禁止**：本任务不得 dispatch paid workflow，不得启动 P3-B。

# 一、任务

准备一次：

```text
4 tasks × 2 treatments
V2_CONTROL vs V3_CORE
```

的严格配对 Pilot。

本任务只做：

- contract freeze；
- treatment ordering；
- identities / allocation；
- zero-provider readiness；
- Artifact 接线；
- conservative budget proposal；
- 单一 PR。

# 二、冻结任务与顺序

1. `beetbox__beets-5457`：V2 → V3
2. `deepset-ai__haystack-8489`：V3 → V2
3. `dynaconf__dynaconf-1249`：V2 → V3
4. `delgan__loguru-1297`：V3 → V2

必须严格串行，共 8 个唯一 task-run。

# 三、固定条件

- bound main：合并后的最新 clean `origin/main`；
- 相同 task/base commit/problem statement；
- 相同模型；
- 相同 Prompt；
- 相同 Provider/endpoint；
- 相同 official evaluator；
- 相同工具与 Permission；
- request ceiling：40；
- retry：0；
- fallback：false；
- 每个 task-run 独立 allocation；
- treatment 外条件必须一致。

模型、Prompt、Provider、evaluator 和 Pricing 必须从仓库现有正式冻结配置中读取，不得凭记忆填写。

# 四、P3-A 交付

1. pre-registered Pilot contract；
2. 8-run manifest；
3. treatment-order manifest；
4. parent authorization draft；
5. 8 个 child allocation draft；
6. zero-provider readiness；
7. readiness Artifact；
8. budget proposal；
9. 一个 PR；
10. CI 终态。

预算建议必须从冻结 Pricing 和 Goal 4/V3 历史 Usage 推导，并给出 expected、conservative、hard-cap proposal 和 safety reserve。

# 五、P3-A 硬门

- 8/8 task-runs 唯一；
- pair 内除 treatment 外完全一致；
- 顺序与本文一致；
- Provider requests/Usage/charge = 0；
- Secret read = false；
- paid jobs skipped；
- V2_CONTROL 不携带 V3 Advice；
- V3_CORE 携带 Advice / activation schema；
- Artifact 能表达 paired comparison；
- ledger 和 allocations 可闭合；
- 无自动 retry、rerun、continuation；
- P4 未启动。

# 六、工作区保护

本地原始 main 保留用户修改，已知为 ahead 1、behind 42。

不得 reset、stash、clean、rebase、同步或覆盖原始 main。

从远端 `main@9e076874894ccf155d990fa8a176b2191e258652` 或执行时最新无冲突的 `origin/main` 创建隔离 worktree。

建议分支：

```text
codex/p3-paired-pilot-readiness
```

# 七、可直接发给 Codex 的指令

```markdown
请执行 CodePaceX P3-A：4×2 严格配对 Pilot 的 zero-provider 准备。

当前 P1/P2 已通过 PR #73 合并：
origin/main@9e076874894ccf155d990fa8a176b2191e258652

请以我上传的：
- CodePaceX_后续整体工作方案_v1.2.0_20260730_2124.md
- CURRENT_SNAPSHOT_P1P2_COMPLETED_P3_PENDING_20260730_2124.md
- CodePaceX_P3A_严格配对Pilot准备与Codex执行合同_v1.0.0_20260730_2124.md

为当前 P3-A 合同，并结合仓库现有 governance、V3.1 snapshot 和 activation replay 执行。

本地原始 main 存在用户修改，禁止 reset、stash、clean、rebase、同步或覆盖。
必须从远端 main 创建隔离 worktree 和分支。

本轮唯一目标是 P3-A：
冻结并 zero-provider 验证 4 tasks × 2 treatments 的 paired Pilot。

任务和交错顺序：
1. beetbox__beets-5457：V2_CONTROL → V3_CORE
2. deepset-ai__haystack-8489：V3_CORE → V2_CONTROL
3. dynaconf__dynaconf-1249：V2_CONTROL → V3_CORE
4. delgan__loguru-1297：V3_CORE → V2_CONTROL

严格固定：
- strict serial
- request ceiling=40
- retry=0
- fallback=false
- 相同模型、Prompt、Provider、evaluator、task/base commit
- 8 个唯一 task-run identities
- 一个 parent authorization draft
- 8 个唯一 child allocation drafts

从仓库正式冻结配置读取 model、Prompt、Provider、evaluator 和 Pricing，不要臆造值。

完成：
- pre-registered contract
- 8-run manifest
- treatment-order manifest
- paired Artifact schema/merge
- zero-provider readiness
- readiness Artifact
- conservative budget proposal
- 最小测试与文档
- 一个 PR
- CI 终态

严格禁止：
- 真实 Provider 请求
- 读取 Secret
- paid workflow
- 自动 retry/rerun/continuation
- 补跑 checkov-6893
- 修改 V3.1 能力算法
- 启动 P3-B
- 启动 P4
- Tag / Release
- 自动合并 PR

即使 readiness 全部通过，也必须停止并汇报预算建议，等待新的明确 paid 授权。
```

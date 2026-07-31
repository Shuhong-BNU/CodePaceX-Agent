# 给 Codex：P3-B-R1 生产适配层参数形状的零 Provider 修复

用户授权你完成 **P3-B-R1 零 Provider 工程修复、测试、PR、CI、合并前验收和中文文档更新**。本任务不包含任何新的付费授权，不允许 dispatch paid workflow。

## 一、事实基线

```text
Repository：Shuhong-BNU/CodePaceX-Agent
基线 origin/main：e9e51c17a2b2b522b6967d7ae3f9497cd89d3531
失败 GitHub Run：30609517826
Paid job：91088973117
Internal run ID：p3b-paid-20260731t061100z-e9e51c17-627c0223
Artifact ID：8784886341
Artifact digest：sha256:7d6bfa6d48562642a3b49bcf3ac3eff00319ead4ad33497fb2ba8e3973a200e9
Provider requests / Usage / charge：0 / 0 / CNY 0
active_reservation：null
```

原 acknowledgement、dispatch token、internal run ID 和原付费授权均已消耗，严禁复用。

## 二、已确认根因

只读审计已经确认，根因不是环境元数据缺失：

- 正式提交 `e9e51c17...` 的环境合同包含 `beetbox__beets-5457` 及其余 3 个 P3-B 唯一任务；
- `evals/evaluation_v2/p3b_paid_executor.py` 约第 156 行已经按任务 ID 取出单个环境合同；
- 约第 158 行将该单项对象传给共享执行器；
- `evals/evaluation_v2/full_replay.py` 约第 1032 行仍把参数视作“按任务 ID 索引的完整环境合同映射”；
- 共享执行器再次用 `beetbox__beets-5457` 索引，因此抛出 `KeyError`。

精确根因：

```text
production_adapter_argument_shape_mismatch
```

测试漏检原因：

```text
现有 paid coordinator 测试使用 recording_fake_task_executor，
绕开了 _real_task_executor → _full_task_executor 生产适配路径。
```

不要再以“补 registry / 补任务环境元数据 / canonicalization”为默认修复方向。

## 三、唯一目标

从精确 `origin/main@e9e51c17a2b2b522b6967d7ae3f9497cd89d3531` 创建隔离 branch/worktree，用最小改动修复 P3-B 调用共享 full replay 执行器时的参数层级，并补足真实生产适配路径的零 Provider 回归。

## 四、实现要求

### 1. 最小修复

优先修改调用方：

```text
p3b_paid_executor._real_task_executor
```

让其向：

```text
full_replay._full_task_executor
```

传入该共享接口原本要求的 **完整环境合同映射**，而不是已提前索引的单项合同。

原则上不要修改 `_full_task_executor` 的共享接口合同。只有代码和现有调用方证明 caller-only 修复不足时，才允许最小调整共享接口，并必须在回执中解释。

禁止：

- 新建第二套环境 registry；
- 为 4 个任务硬编码环境；
- 修改任务、顺序、treatment、模型、Prompt、Provider、endpoint、evaluator、Pricing 或预算；
- Agent 新能力和无关重构。

### 2. 防回归测试

新增或改造测试，必须真正经过：

```text
P3-B coordinator
→ _real_task_executor
→ _full_task_executor
→ environment mapping[instance_id]
→ Provider initialization boundary
```

不能只继续使用 `recording_fake_task_executor`。

至少断言：

1. 传入 `_full_task_executor` 的环境对象是完整 mapping；
2. `beetbox__beets-5457` 不再触发二次索引错误；
3. 4 个唯一任务全部通过相同路径；
4. 8 个冻结 task-run 全部到达 Provider 初始化边界前；
5. 任一环境键真实缺失时仍 fail-closed，并报告缺失 instance；
6. Provider transport hard-disabled；
7. Provider requests / Usage / charge = `0 / 0 / CNY 0`；
8. Secret 值未读取；
9. Run `30609517826` 与 Artifact `8784886341` 保持不可变。

测试可以使用记录型 provider 或边界 stub，但不得替换掉 `_real_task_executor` 与 `_full_task_executor` 之间的真实适配。

### 3. Zero-provider readiness

更新 P3-B readiness，使其在干净 Ubuntu 环境中对 8 个 task-run 走真实生产适配路径，允许 checkout、workspace 和 bootstrap 前置准备，但必须在 Provider transport 前停止。

逐 run 至少记录：

- ordinal；
- instance ID；
- treatment；
- adapter path exercised；
- environment mapping lookup；
- workspace/bootstrap/evaluator identity；
- Provider transport reached=false；
- terminal preflight status。

成功门：

```text
8/8 production-adapter preflight passed
4/4 unique instances passed
Provider requests / Usage / charge = 0 / 0 / CNY 0
active_reservation = null
```

### 4. 身份同步

代码、runtime、workflow 或 readiness 变化后，按仓库既有机制同步必要的：

- blob SHA；
- freeze byte / canonical SHA；
- readiness identity；
- 防回归测试身份。

不得绕过身份绑定，也不得生成新的可执行 acknowledgement、dispatch token 或 run ID。

### 5. 中文文档

在 `docs/governance/` 更新：

- 当前状态快照；
- P3-B 首次 paid attempt 失败审计摘要；
- P3-B-R1 修复合同；
- 后续整体工作方案；
- 版本与文档总索引；
- 必要的上下文迁移包。

必须明确区分：

```text
历史机器标签：blocked_preflight_task_environment_missing
精确因果根因：production_adapter_argument_shape_mismatch
```

将 14:41 版本中“环境缺失 / registry 修复”的当前指导状态标记为 Superseded，但不要删除历史文件。

## 五、Git 与 PR

- 不使用、同步、reset、stash、clean 或覆盖用户原始本地 main；
- 从精确 origin/main 创建隔离 worktree；
- 一个直接根因用一个集中式 PR 收口；
- 普通 commit、push、PR、CI 可连续完成；
- paid job 在 PR/CI 中必须 skipped；
- 不自动合并；
- 不启动 P4；
- 不 dispatch 任何 paid workflow。

## 六、出口门

最终回执必须包含：

```text
root cause
exact code change
changed files
branch / commit / PR
production adapter test evidence
4/4 unique instance result
8/8 task-run preflight result
Ubuntu/macOS CI
P3-B zero-provider readiness Run ID / Artifact ID / digest
Provider requests / Usage / charge
active_reservation
paid job skipped
Secret value read=false
new freeze/readiness identities
review status
```

全部满足后只允许写：

```text
P3-B-R1_READY_FOR_MERGE
```

不得写：

```text
P3-B ready for paid rerun
```

新的 paid attempt 仍必须经过：PR 合并、post-merge 只读核验、用户全新明确授权、全新 acknowledgement、全新 dispatch token、全新 internal run ID。

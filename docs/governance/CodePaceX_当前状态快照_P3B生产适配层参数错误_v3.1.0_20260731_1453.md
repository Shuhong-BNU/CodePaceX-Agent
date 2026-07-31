# CodePaceX 当前状态快照：P3-B 生产适配层参数错误

> 文档版本：v3.1.0
> 生成时间：2026-07-31 14:53（UTC+8）
> 状态：Superseded（历史根因快照；现由 P3-B-R4 输入合同加固记录替代）
> 语言：中文

## 0. 一句话结论

P3-B 首次付费 workflow 已按唯一授权 dispatch 并终态关闭，但在第一个任务进入 Provider 之前，生产适配层把“单个环境合同”误当作“完整环境合同映射”传给共享执行器，导致二次按任务 ID 索引并抛出 `KeyError`。正式实验数据仍为 `0/8`、配对结果 `0/4`、费用 `CNY 0`。

## 1. 当前权威身份

```yaml
repository: Shuhong-BNU/CodePaceX-Agent
formal_main: e9e51c17a2b2b522b6967d7ae3f9497cd89d3531
github_run: 30609517826
paid_job: 91088973117
internal_run_id: p3b-paid-20260731t061100z-e9e51c17-627c0223
dispatch_token_sha256: 61f70abb7f7eb48a970b81af1122778afbe04ae0dec741ff11eb2de69883eea3
artifact_id: 8784886341
artifact_digest: sha256:7d6bfa6d48562642a3b49bcf3ac3eff00319ead4ad33497fb2ba8e3973a200e9
historical_machine_status: blocked_preflight_task_environment_missing
corrected_root_cause: production_adapter_argument_shape_mismatch
formal_task_runs: 0/8
paired_results: 0/4
provider_requests: 0
usage: 0
charge_cny: 0
active_reservation: null
p4: blocked
```

## 2. 根因纠正

### 历史机器分类

Artifact 和首次回执保留：

```text
blocked_preflight_task_environment_missing
```

该值是当时机器终态标签，不删除、不改写。

### 当前精确根因

根因不是环境元数据缺失。正式 `main@e9e51c17...` 的环境合同包含 4 个 P3-B 唯一任务。

真实错误链：

```text
p3b_paid_executor._real_task_executor
→ 先按 instance_id 取出单个环境合同
→ 将单项合同传给 full_replay._full_task_executor
→ 共享执行器仍把参数视为完整映射
→ 再次执行 environments[instance_id]
→ KeyError: 'beetbox__beets-5457'
```

关键位置：

- `evals/evaluation_v2/p3b_paid_executor.py`：约第 156～158 行；
- `evals/evaluation_v2/full_replay.py`：约第 1032 行。

精确分类：

```text
blocked_preflight_production_adapter_argument_shape_mismatch
```

## 3. 测试为什么漏检

现有 paid coordinator 测试使用：

```text
recording_fake_task_executor
```

因此绕开了生产路径：

```text
_real_task_executor
→ _full_task_executor
```

测试证明了 coordinator 编排形状，却没有验证真实适配层的参数合同。

## 4. 当前进度

```text
P0～P3-A：已完成
P3-B0～P3-B3：已完成
P3-B-A1：已执行并终态阻塞
P3-B-R1 根因审计：已完成
P3-B-R1 代码修复：未开始
P3-B-R1 测试/PR/CI：未开始
P3-B-A2：未授权
P4：未开始
```

按阶段交付计，P3-B-R1 当前完成了“根因审计”这一项；实现、回归、CI、文档和 PR 验收仍待完成。

## 5. 当前唯一合法任务

执行 **P3-B-R1 生产适配层零 Provider 修复**：

1. 让 `_real_task_executor` 向共享执行器传入完整环境合同映射；
2. 增加覆盖 `_real_task_executor → _full_task_executor` 的零 Provider 回归测试；
3. 证明 4 个唯一任务、8 个 treatment run 均越过该适配层；
4. 在 Provider transport 前硬停止；
5. 更新 freeze/readiness/runtime 身份和中文治理文档；
6. 一个集中式 PR 收口；
7. 不自动合并，不创建新 paid identity。

## 6. 严格禁止

- 复用原 acknowledgement、dispatch token 或 internal run ID；
- retry、rerun、continuation、fallback 或第二次 dispatch；
- 读取 Secret 值；
- 修改任务、顺序、treatment、模型、Prompt、Provider、evaluator、Pricing 或预算；
- 启动 P4；
- 修改用户原始本地 main；
- 覆盖 Run `30609517826` 或 Artifact `8784886341`。

# CodePaceX P3-B 首次付费执行失败审计摘要

> 版本：v1.1.0  
> 生成时间：2026-07-31 14:53（UTC+8）  
> 状态：当前有效  
> 性质：历史失败审计与根因更正

## 1. 执行结果

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

唯一 paid dispatch 已自然终态，未重试、未 continuation、未第二次 dispatch。失败发生在第一个任务、Provider 初始化之前。

## 2. 不可变历史事实

- GitHub Run：`30609517826`
- Paid job：`91088973117`
- Artifact：`8784886341`
- Provider requests / Usage / charge：`0 / 0 / CNY 0`
- `active_reservation=null`
- 没有 Candidate、evaluator report、V2/V3 raw Artifact、terminal record 或 paired result
- 原单次授权和原三项执行身份均已消耗

## 3. 根因审计更正

### 早期表述

```text
任务环境预检缺失
```

### 精确结论

环境合同数据并不缺失。错误发生在 P3-B 到 full replay 的生产适配层：

1. P3-B 适配器已经从完整映射中取出单个任务环境合同；
2. 适配器把该单项对象传给共享 full replay 执行器；
3. 共享执行器的接口合同要求完整映射；
4. 共享执行器再次按 `instance_id` 索引；
5. 单项对象不具备该顶层任务键，触发 `KeyError`。

因此，本次失败属于：

```text
production adapter argument-shape mismatch
```

不是：

- 环境 registry 缺少任务；
- 任务名 canonicalization 错误；
- Provider 失败；
- 模型能力失败；
- V2 或 V3 unresolved。

## 4. readiness 漏洞

既有 coordinator 测试注入 `recording_fake_task_executor`，未经过真实 `_real_task_executor → _full_task_executor` 适配路径。

直接教训：

> 编排测试通过不等于生产适配层参数合同已被验证。

## 5. 修复原则

最小修复优先放在调用方适配层：

```text
_real_task_executor 传入完整 environment contracts mapping
```

共享 `_full_task_executor` 的既有接口合同原则上保持不变。只有仓库证据证明调用方修复不足时，才允许调整共享接口。

必须补充生产路径零 Provider 回归测试，而不是再添加仅使用 fake executor 的 coordinator 测试。

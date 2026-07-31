# CodePaceX 版本与文档总索引

> 版本：v1.9.0  
> 生成时间：2026-07-31 14:53（UTC+8）  
> 状态：当前有效  
> 语言：中文

## 1. 当前状态

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

## 2. 当前必读

1. `CodePaceX_当前状态快照_P3B生产适配层参数错误_v3.1.0_20260731_1453.md`
2. `CodePaceX_P3B首次付费执行失败审计摘要_v1.1.0_20260731_1453.md`
3. `CodePaceX_P3BR1生产适配层零Provider修复合同_v1.1.0_20260731_1453.md`
4. `CodePaceX_后续整体工作方案_v1.9.0_20260731_1453.md`
5. `给Codex_P3BR1生产适配层零Provider修复提示词_20260731_1453.md`
6. `CodePaceX_上下文迁移包_GPT_24_20260731_1453.md`

继续保留：

- `CodePaceX_L4配对实验与FreshHoldout说明_v1.0.0_20260731_1400.md`
- `CodePaceX_文档版本与命名规则_v1.0.0_20260731_1330.md`

## 3. 已被替代但保留的入口

以下文件不得再指导当前修复：

- `CodePaceX_当前状态快照_P3B首次付费执行预检阻塞_v3.0.0_20260731_1441.md`
- `CodePaceX_P3BR1任务环境预检零Provider修复合同_v1.0.0_20260731_1441.md`
- `CodePaceX_后续整体工作方案_v1.8.0_20260731_1441.md`
- `CodePaceX_版本与文档总索引_v1.8.0_20260731_1441.md`
- `给Codex_P3BR1任务环境预检零Provider修复提示词_20260731_1441.md`
- `CodePaceX_上下文迁移包_GPT_23_20260731_1441.md`

这些文档对历史状态仍有效，但其“环境缺失 / registry 修复”方向已被只读根因审计纠正。

## 4. 当前决策

- P3-B-A1 已关闭；
- P3-B-R1 根因已定位；
- 下一步仅做生产适配层零 Provider 修复；
- 不自动合并；
- 不创建新 paid identity；
- 新 paid attempt 必须重新授权；
- P4 保持阻塞。

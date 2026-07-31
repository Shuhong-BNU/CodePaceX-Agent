# CodePaceX 后续整体工作方案：P3-B-A2 失败后

> 版本：v2.2.0  
> 状态：当前有效  
> 适用范围：A2 fail-closed 后的停机边界

## 已确认事实

P3-B-A2 的唯一 dispatch 为 Run `30620506129`，在 `validate_paid_inputs` 的 acknowledgement 前缀校验处失败。失败发生在 workspace、ledger、task-run 和 Provider transport 之前，因此 Provider requests / Usage / charge 为 `0 / 0 / CNY 0`，`active_reservation=null`，没有 Artifact。

## 停止合同

当前只保留失败日志和中文治理记录。不得 retry、rerun、continuation、fallback、自动补跑、修复后自行 dispatch、生成新的 readiness run、修改 PR #81 之外的代码或启动 P4。本次一次性 acknowledgement、dispatch token 和 internal run ID 不得复用。

## 新授权后的必要步骤

只有用户再次明确授权后，才可重新进行一次全新的只读身份核验，并由用户/授权文本提供新的 acknowledgement、dispatch token 和 internal run ID。核验必须重新确认 formal main、PR 状态、workflow/executor/gate SHA、freeze、allocation、authorization、预算、Secret metadata 和历史 Artifact 保留；任一不匹配继续 fail-closed。

本次失败根因 `authorization_acknowledgement_contract_prefix_mismatch` 不得被误记为 Provider 或环境缺失问题。历史机器标签 `blocked_preflight_task_environment_missing` 与 R1 根因 `production_adapter_argument_shape_mismatch` 继续保留为独立历史记录。

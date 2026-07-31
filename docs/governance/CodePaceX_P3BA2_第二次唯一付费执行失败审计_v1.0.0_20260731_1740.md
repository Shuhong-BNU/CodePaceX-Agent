# CodePaceX P3-B-A2 第二次且唯一一次付费执行失败审计

> 版本：v1.0.0  
> 状态：已完成  
> 结论：fail-closed，未进入任何 task-run 或 Provider transport

## Dispatch 身份

```yaml
repository: Shuhong-BNU/CodePaceX-Agent
bound_main: 4a5b66787f1d7ab0619b86eb3c9fb7f0f7b6cd72
workflow: .github/workflows/p3b-paired-pilot.yml
run_id: 30620506129
run_attempt: 1
paid_job: 91123686219
paid_job_status: failure
zero_provider_job: 91123686696
zero_provider_job_status: skipped
internal_run_id: p3b-paid-v2-20260731t091733z-4a5b6678-8976a0b3
dispatch_token_sha256: 5c3adb27ce23fe6044b810f03636d718ecef7fc2540984478196bacd086b87b2
```

这是本次授权允许的唯一 A2 dispatch；没有 retry、rerun、continuation、fallback 或第二次 dispatch。

## 失败位置与精确原因

workflow 的 shell gate 已通过 `paid_execution=true`、parent cap、Secret presence 和所有冻结 SHA 校验。随后 `evals/evaluation_v2/p3b_paid_executor.py:101-102` 的 `validate_paid_inputs` fail-closed：

```text
provided acknowledgement prefix: P3B_PAID_AUTHORIZATION_V2:
required code prefix: P3B_PAID_AUTHORIZATION:
error: paid execution requires the explicit P3-B authorization acknowledgement
```

因此本次失败分类为 `authorization_acknowledgement_contract_prefix_mismatch`。这不是 Provider authentication、环境映射、workspace、evaluator、预算或生产适配层错误；PR #79 修复的 `production_adapter_argument_shape_mismatch` 仍保持有效。

## 终态证据

- executor 在身份校验阶段退出，未创建 paid Artifact、workspace、ledger 或 task record。
- Run Artifact API `total_count=0`；上传步骤因 artifact 目录不存在而失败。
- 已开始 task-run：`0/8`；Candidate、V2/V3 Artifact、evaluator report：均未生成。
- Provider initialization/transport：未到达；Provider requests / Usage / charge：`0 / 0 / CNY 0`。
- `active_reservation=null`；没有 ledger reservation 或 settlement。
- `BAILIAN_API_KEY` metadata presence=`true`；Secret value read=`false`。
- 历史 Run `30609517826`、Artifact `8784886341` 及 digest 未被覆盖。

本次授权 acknowledgement、dispatch token 和 internal run ID 已消耗，不得复用。任何后续 paid attempt 都必须等待用户新的明确授权，并生成全新三项身份；在此之前不得修改代码、修复后自行 dispatch、启动 P4 或创建新的 readiness run。

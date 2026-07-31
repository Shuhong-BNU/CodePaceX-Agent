# CodePaceX P3-B-R2 Post-merge 零 Provider 核验报告

> 版本：v1.0.0  
> 状态：已完成  
> 性质：合并后零 Provider 核验

## 合并身份

```yaml
pr: 79
merge_method: merge_commit
pr_head: ff7a2026d0ebed37927fb8d132e6d849118c83d6
merge_commit: e26ae967fdc10f4129e87d752d4c489c1a72d96e
formal_main: e26ae967fdc10f4129e87d752d4c489c1a72d96e
main_ci_run: 30614341942
main_ci_result: success
```

## Readiness 身份

```yaml
readiness_run: 30614539848
readiness_job: 91104588742
paid_job: 91104589478
artifact_id: 8786805132
artifact_digest: sha256:66803badbfee630c5838143484d332c15aaf329de24134cfbba3a2e66069b712
workflow_blob: 5638f16553c79dd2d6dcb208f0fce07e962319e5
paid_executor_blob: d8c48f2c543b0c5f552fff6d977206dea34ae7e1
paid_gate_blob: 52d04353bb4df96ad07f53986e9f234ef9e271fc
freeze_byte_sha256: b3b531a2d13458e899c383d6c26fecc360651d7f69babc58bb3f1b56de323f3e
freeze_canonical_sha256: 748839c2f01e009b7e6105ce0b6f74d79edbe2a4f9936719d935579b976bca7f
readiness_sha256: 3f0f41da163f4357d8f1c6710758e6718f55fa52115b1a937e1116dc16ae63c4
allocation_hash: 58e3e967736fe4335a8882fa21b69fd755946da28aa04d3f88babb8cb2c25fff
authorization_hash: b9dfe904ad3b4728b00f2109459e99ac6464e1cd8311b74ee3ef3a882b310d51
```

## 核验结果

- 4/4 唯一实例通过同一生产适配路径。
- 8/8 冻结 task-run 记录 `provider_initialization_boundary_reached=true` 与 `provider_transport_reached=false`。
- Provider requests / Usage / charge：`0 / 0 / CNY 0`；`active_reservation=null`；Secret value read=`false`。
- workflow dispatch 使用 `paid_execution=false`；paid job skipped；没有 acknowledgement、dispatch token 或 internal run ID 被生成或复用。
- 历史 paid Run `30609517826` 与 Artifact `8784886341` 的 ID、digest 和终态保持不变。

该报告只证明 R1 修复在 main 上完成 zero-provider 验收，不构成新的付费授权。

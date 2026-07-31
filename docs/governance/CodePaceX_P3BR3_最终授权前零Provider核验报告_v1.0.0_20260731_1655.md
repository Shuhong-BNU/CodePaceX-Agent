# CodePaceX P3-B-R3 最终付费授权前零 Provider 核验报告

> 版本：v1.0.0  
> 状态：已完成  
> 性质：文档 PR #80 合并后的最终只读核验  
> 付费执行：未触发

## 结论

PR #80 已按锁定 Head 普通合并。最终 main 上的 CI 和 P3-B zero-provider readiness 均成功，4 个唯一实例和 8 个冻结 task-run 均经过真实生产适配路径，在 Provider transport 前停止。该结果不构成付费授权。

最终出口状态：`P3-B_READY_FOR_EXPLICIT_PAID_AUTHORIZATION_V2`

## 合并与 CI 身份

```yaml
repository: Shuhong-BNU/CodePaceX-Agent
pr: 80
pr_head: 2def2a8b3d8d54a1a9001af8a6c0e5cf4d44c409
base_before_merge: e26ae967fdc10f4129e87d752d4c489c1a72d96e
merge_method: merge_commit
merge_commit: 4a5b66787f1d7ab0619b86eb3c9fb7f0f7b6cd72
formal_main: 4a5b66787f1d7ab0619b86eb3c9fb7f0f7b6cd72
main_ci_run: 30617653901
main_ci_result: success
main_ci_jobs: ubuntu=91114491234, macos=91114491238
```

PR #80 的变更范围为 6 个 `docs/governance/*.md` 中文 Markdown 文件，未包含代码、workflow、freeze、runtime、pricing、budget、allocation 或实验合同变更。

## Readiness 身份

```yaml
readiness_run: 30617786424
readiness_job: 91114919739
paid_job: 91114920221
paid_job_status: skipped
artifact_id: 8788083500
artifact_name: p3b-zero-provider-readiness-30617786424
artifact_digest: sha256:1f61874b787929cd303e907499488b80cf3f65564992285a75a362dafadaf7e7
workflow_blob: 1bdd9fc1980eb91493fdcb13d47dacfc2a2554f41ba7433554a629c50f7fd098
paid_executor_blob: 55ba2f218b3bebdb6e060b3c38d48b3143bc416ff3d00040a11bba34cccbd2ac
paid_gate_blob: 8399579370ec7cfee1a2eb1b72638068665b100fe3b09bcc4135642731f942dc
freeze_byte_sha256: b3b531a2d13458e899c383d6c26fecc360651d7f69babc58bb3f1b56de323f3e
freeze_canonical_sha256: 748839c2f01e009b7e6105ce0b6f74d79edbe2a4f9936719d935579b976bca7f
readiness_sha256: 3f0f41da163f4357d8f1c6710758e6718f55fa52115b1a937e1116dc16ae63c4
allocation_hash: 58e3e967736fe4335a8882fa21b69fd755946da28aa04d3f88babb8cb2c25fff
authorization_hash: b9dfe904ad3b4728b00f2109459e99ac6464e1cd8311b74ee3ef3a882b310d51
```

## 适配路径与零 Provider 结果

readiness 使用 `paid_execution=false`，执行器链为：

```text
p3b_paid_executor._real_task_executor
  -> full_replay._full_task_executor
  -> control_canary._live_task_executor
  -> Provider initialization boundary
```

生产适配预检结果为 `4/4` 唯一实例、`8/8` 冻结 task-run；每条记录均为 `provider_initialization_boundary_reached=true`、`provider_transport_reached=false`，且 `provider_transport_hard_disabled=true`。环境映射查找包含 20 个实例键，缺失键仍 fail-closed。

| ordinal | instance_id | treatment | task-run |
| ---: | --- | --- | --- |
| 1 | `beetbox__beets-5457` | `V2_CONTROL` | `p3b-01-beetbox__beets-5457-V2_CONTROL` |
| 2 | `beetbox__beets-5457` | `V3_CORE` | `p3b-02-beetbox__beets-5457-V3_CORE` |
| 3 | `deepset-ai__haystack-8489` | `V3_CORE` | `p3b-03-deepset-ai__haystack-8489-V3_CORE` |
| 4 | `deepset-ai__haystack-8489` | `V2_CONTROL` | `p3b-04-deepset-ai__haystack-8489-V2_CONTROL` |
| 5 | `dynaconf__dynaconf-1249` | `V2_CONTROL` | `p3b-05-dynaconf__dynaconf-1249-V2_CONTROL` |
| 6 | `dynaconf__dynaconf-1249` | `V3_CORE` | `p3b-06-dynaconf__dynaconf-1249-V3_CORE` |
| 7 | `delgan__loguru-1297` | `V3_CORE` | `p3b-07-delgan__loguru-1297-V3_CORE` |
| 8 | `delgan__loguru-1297` | `V2_CONTROL` | `p3b-08-delgan__loguru-1297-V2_CONTROL` |

每个实例记录了 workspace/bootstrap/evaluator identity；模型、Provider、endpoint 和 evaluator 均沿用冻结合同，未作变更。

```yaml
model: qwen3.7-max-2026-06-08
provider: bailian-qwen37-max
endpoint: https://llm-ipge9fy38w648m28.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
official_evaluator_commit: ad79b850f15e33992e96f03f6e97f05ddf9aa0be
provider_requests: 0
usage: 0
charge_cny: 0
active_reservation: null
secret_value_read: false
```

## 预算与授权边界

```yaml
parent_cap_cny: 292.945921
spendable_cny: 292.945920
safety_reserve_cny: 0.000001
child_count: 8
child_cap_each_cny: 36.618240
allocation_status: formal_proposal_not_authorized
active_p3b_paid_jobs: 0
prior_real_p3b_paid_dispatch_count: 1
```

当前 readiness 只生成并验证零成本 proposal/ledger；没有生成新的 acknowledgement、dispatch token 或 internal run ID，也没有复用历史付费身份。

`BAILIAN_API_KEY` repository Secret metadata 存在（metadata `updated_at=2026-07-28T14:43:56Z`）；本次只读取名称和 metadata，Secret value read=`false`。

## 历史保留与后续边界

历史 Run `30609517826`、paid job `91088973117`、Artifact `8784886341` 及 digest `sha256:7d6bfa6d48562642a3b49bcf3ac3eff00319ead4ad33497fb2ba8e3973a200e9` 保持不变。历史机器标签 `blocked_preflight_task_environment_missing` 仅用于历史记录；精确因果根因是 `production_adapter_argument_shape_mismatch`，已由 PR #79 的 caller-adapter 修复解决。

后续任何 paid attempt 都必须先获得用户全新的明确授权，再生成全新的 acknowledgement、dispatch token 和 internal run ID。不得 retry、rerun、fallback、启动 P4 或复用上述身份。

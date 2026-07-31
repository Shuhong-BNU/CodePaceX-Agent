# CodePaceX 当前状态快照：P3-B-R3 最终授权前核验完成

> 文档版本：v3.3.0  
> 生成时间：2026-07-31 16:55（UTC+8）  
> 状态：当前有效  
> 语言：中文

## 当前结论

PR #80 已合并到正式 main。新 main 的 Ubuntu/macOS CI 成功，P3-B zero-provider readiness 成功，4/4 唯一实例和 8/8 冻结 task-run 均在 Provider transport 前停止。当前仅达到最终付费授权前门槛，不代表已获得付费授权。

```yaml
repository: Shuhong-BNU/CodePaceX-Agent
formal_main: 4a5b66787f1d7ab0619b86eb3c9fb7f0f7b6cd72
merged_pr: 80
merge_commit: 4a5b66787f1d7ab0619b86eb3c9fb7f0f7b6cd72
main_ci_run: 30617653901
post_merge_readiness_run: 30617786424
post_merge_readiness_artifact: 8788083500
post_merge_readiness_digest: sha256:1f61874b787929cd303e907499488b80cf3f65564992285a75a362dafadaf7e7
provider_requests: 0
usage: 0
charge_cny: 0
active_reservation: null
paid_job: skipped
secret_value_read: false
p4: blocked
```

历史机器标签仍为 `blocked_preflight_task_environment_missing`；它不是当前精确因果根因。当前根因是 `production_adapter_argument_shape_mismatch`，由 PR #79 修复。

## 不可变身份

```yaml
workflow_blob: 1bdd9fc1980eb91493fdcb13d47dacfc2a2554f41ba7433554a629c50f7fd098
paid_executor_blob: 55ba2f218b3bebdb6e060b3c38d48b3143bc416ff3d00040a11bba34cccbd2ac
paid_gate_blob: 8399579370ec7cfee1a2eb1b72638068665b100fe3b09bcc4135642731f942dc
freeze_byte_sha256: b3b531a2d13458e899c383d6c26fecc360651d7f69babc58bb3f1b56de323f3e
freeze_canonical_sha256: 748839c2f01e009b7e6105ce0b6f74d79edbe2a4f9936719d935579b976bca7f
readiness_sha256: 3f0f41da163f4357d8f1c6710758e6718f55fa52115b1a937e1116dc16ae63c4
allocation_hash: 58e3e967736fe4335a8882fa21b69fd755946da28aa04d3f88babb8cb2c25fff
authorization_hash: b9dfe904ad3b4728b00f2109459e99ac6464e1cd8311b74ee3ef3a882b310d51
```

## 当前边界

- `BAILIAN_API_KEY` 只核验 repository Secret metadata presence，值未读取、未打印、未导出。
- 当前 active P3-B paid jobs 为 `0`；历史真实 P3-B paid dispatch count 为 `1`。
- 没有新的 acknowledgement、dispatch token 或 internal run ID。
- P4、retry、rerun、fallback 和任何 paid workflow dispatch 继续禁止。

后续 paid attempt 必须经过用户全新明确授权和全新身份生成流程。

# CodePaceX P3-B-R5：锁定 Head 合并与 Post-merge 零 Provider 核验

> 文档版本：v1.0.0
> 记录时间：2026-07-31 20:35（UTC+8）
> 状态：R4 已合并；仍未获得新的 paid authorization
> 语言：中文

## 锁定审阅与合并

- PR #82：`MERGED`，普通 merge；没有 squash、rebase、force push。
- 锁定 head：`b280a1abc7bca9d2ff6dfc7236a76f757a9fde8f`。
- 锁定 base：`4a5b66787f1d7ab0619b86eb3c9fb7f0f7b6cd72`。
- merge commit / 当前 formal main：`844d1098f2f62119947afe1b2dda33d04d50cc6c`。
- 合并前变更：29 files，698 additions / 161 deletions；`git diff --check` 通过。
- PR #81：`CLOSED`、`mergedAt=null`，保持 superseded，未合并。

R5 锁定审阅再次确认：没有模型、Prompt、Provider、Evaluator、Pricing、任务/配对顺序、预算或 P4 改动；仓库中没有真实 acknowledgement、dispatch token、internal run ID 或 Secret 值。

## Post-merge 核验结果

### Main CI

- GitHub Actions main CI Run：30630775458，`success`。
- Ubuntu job：91156341297，`success`。
- macOS job：91156340888，`success`。

### Exact paid-input validate-only

在 formal main `844d1098f2f62119947afe1b2dda33d04d50cc6c` 的干净 detached checkout 中，用只用于测试、不可执行且不可复用的 canonical bundle 运行：

```text
python -m evals.evaluation_v2.p3b_paid_executor --validate-inputs-only
```

- `validate_paid_inputs()` 已真实执行。
- `HEAD == expected_main_sha == 844d1098f2f62119947afe1b2dda33d04d50cc6c`。
- 成功产物：`paid-input-preflight.json`，SHA-256=`c7c662935521a0c840a074ca0c8e67ea7dfe47b2b9d48b018fda301134eb5964`。
- 没有创建 workspace、ledger、reservation 或 Provider Client。
- Provider reached=false；Secret value read=false；Provider requests / Usage / charge=`0 / 0 / CNY 0`；active_reservation=null。

未生成、显示或复用真实 acknowledgement、dispatch token 或 internal run ID；也没有 dispatch paid workflow。

### Zero-provider readiness

P3-B workflow 只由 pull request 或显式 `workflow_dispatch` 触发，main merge 不会自动触发该 workflow。为遵守“不得 dispatch paid workflow”边界，本轮没有手动 dispatch；改在上述 exact-main clean checkout 运行与 workflow 相同的 committed readiness 测试和 freeze 验证：

- `tests/test_p3b_post_merge_rebind.py`：7 passed。
- 生产适配 preflight：8/8 frozen task-run passed，4/4 unique instances passed。
- Provider transport hard-disabled，所有 record 的 `provider_transport_reached=false`。
- paid job=skipped；Provider requests / Usage / charge=`0 / 0 / CNY 0`；Secret value read=false；active_reservation=null。

历史 Run `30620506129` 保持不变：它是 A2 的 fail-closed 失败记录，0/8 task-run、Artifact=0、Provider requests / Usage / charge=`0 / 0 / CNY 0`。

## 当前身份

```text
workflow_content_sha256=0a48f655f8a6cb12347e5c794bbc6b321284c258f3b27f1672d0e0db0860eab6
paid_executor_content_sha256=5eca4015570f9f9744bd4fbe4d4a6ba667baa0c29731bd47a83864396585bb46
paid_gate_content_sha256=8399579370ec7cfee1a2eb1b72638068665b100fe3b09bcc4135642731f942dc
freeze_base_commit=2794e27220d3fada3bd0fdd3a1a14ff50e3a6034
freeze_sha256=4c1e4468b2685c198a1eeed03e607963d3514daaa17a6068fa7b4c832d9054bd
freeze_canonical_sha256=f4d20dce7246f4dc825cd540abf80c983302e77e0b58707497bd412e37f0ad48
readiness_sha256=d5e5c0c685617fc73f0bb29200a01f63388ea87ab00a8642b1464f1c081b2484
allocation_hash=58e3e967736fe4335a8882fa21b69fd755946da28aa04d3f88babb8cb2c25fff
authorization_hash=b9dfe904ad3b4728b00f2109459e99ac6464e1cd8311b74ee3ef3a882b310d51
```

## 决策边界

R5 只完成 R4 合并与零 Provider post-merge 核验，不构成 paid rerun 授权。后续若考虑 A3，必须由用户重新作出明确授权，并提供全新、未使用的 acknowledgement、dispatch token 和 internal run ID；先通过 validate-only，才可能讨论独立的 paid dispatch。P4 继续阻塞。

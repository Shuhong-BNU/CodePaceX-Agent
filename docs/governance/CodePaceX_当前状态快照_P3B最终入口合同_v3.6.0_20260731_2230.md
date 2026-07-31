# CodePaceX 当前状态快照：P3-B 最终 Paid 入口合同

> 文档版本：v3.6.0
> 生成时间：2026-07-31 22:30（UTC+8）
> 状态：集中式零 Provider PR 待审核；未获新的 paid authorization
> 语言：中文

## 当前结论

R5/A3 预检确认了两个入口合同缺口：旧 `canonical_paid_input_bundle()` 只组装调用方提供的 identity，且 executor 无法证明 validate-only 与 paid execution 使用同一原始 bundle bytes。本轮在不改动模型、任务、Provider、预算或 P4 的前提下收口这两个缺口。

- 唯一 identity 入口是 `generate_paid_input_bundle()`；调用方不能提供 acknowledgement 前缀、token 或 run ID。
- 测试仅生成 `identity_mode=test-only` 身份；不会提交或显示未来可执行 A3 identity。
- bundle 是固定字段顺序的 UTF-8 compact JSON，只有一个末尾 LF；未知字段、重复键、字段重排、非 canonical bytes 均 fail-closed。
- `final_input_bundle_sha256` 始终对文件原始 bytes 计算。workflow 解码一次，不再重新序列化；validate-only 和 paid executor 各自在打开文件后重新计算并核验该 SHA。

P3-B 仍处于 `blocked_pending_new_explicit_paid_authorization`。本轮不 dispatch paid workflow，不读 Secret 值，不建立 workspace/ledger/reservation，不调用 Provider，P4 继续阻塞。

## 保留事实

历史机器标签 `blocked_preflight_task_environment_missing`、精确因果根因 `production_adapter_argument_shape_mismatch` 与 A2 近端根因 `authorization_acknowledgement_protocol_prefix_mismatch` 均为不可互换的历史事实。当前新增根因是 `canonical_identity_generation_and_bundle_byte_binding_missing`。其后续修复审计确认了 `canonical_identity_random_alphabet_contract_mismatch`：`token_urlsafe()` 可生成 `-` 或 `_` 首字符，但 P3-B 安全身份正则要求首字符为字母或数字。当前集中式修复改为 `secrets.choice` 的显式安全首字符/后续字符字母表；不放宽正则、不改变 canonical bytes 或 bundle SHA 绑定。

历史失败 Run 30620506129 保持不可变：0/8 task-run、Artifact=0、Provider requests / Usage / charge=`0 / 0 / CNY 0`、active_reservation=null。

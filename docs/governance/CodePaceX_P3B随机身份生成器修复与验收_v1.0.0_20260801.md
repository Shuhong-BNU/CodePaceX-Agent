# CodePaceX P3-B 随机身份生成器修复与验收

> 状态：零 Provider 修复已实现，等待集中式 PR 审阅与合并

## 根因

`generate_paid_input_bundle()` 曾将 `secrets.token_urlsafe(18)` 直接作为身份 suffix。该 API 允许 `-` 或 `_` 出现在首字符，而 `_SAFE_RUN_IDENTITY` 要求首字符必须是 ASCII 字母或数字，形成 `canonical_identity_random_alphabet_contract_mismatch`，导致生成阶段概率性失败。

## 修复合同

生成器现在使用密码学安全的 `secrets.choice`：首字符来自 `A-Z a-z 0-9`，其余字符来自 `A-Z a-z 0-9 . _ -`。正则、schema_version=2、identity_mode、canonical JSON 字节规则及 `final_input_bundle_sha256` 绑定均未改变。acknowledgement、dispatch token 和 run ID 仍只能由 canonical generator 生成。

## 验收范围

- test-only 生成身份首字符与完整 suffix 均满足 `_SAFE_RUN_IDENTITY`；
- authorized 仅保留测试 fixture/零 Provider seam，不生成未来真实 A3 身份；
- 可注入随机 chooser 覆盖 `-`、`_`、`.` 边界；
- canonical bundle 原始 bytes 与 validate-only/paid executor SHA 绑定回归保持通过；
- Provider requests / Usage / charge 保持 `0 / 0 / CNY 0`，Secret value read=false，paid job skipped。

本地验收已通过 generator/paid-executor 与 P3-B rebind 定向测试（`31 passed`）。重生成 readiness 记录 4/4 唯一实例、8/8 task-run、Provider transport hard-disabled、`0 / 0 / CNY 0`、`active_reservation=null` 和 paid job skipped。完整 CI 与锁定 Head 审阅须在集中式 PR 创建后完成。

## 后续 A3

本修复不构成 paid authorization。只有用户新的明确授权、全新 generator identity 和一次性 validate-only 全部通过后，才可讨论 A3；本轮不 dispatch paid workflow，不调用 Provider，不启动 P4。

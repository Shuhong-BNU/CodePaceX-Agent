# CodePaceX P3-B 上下文迁移包 GPT-28

## 当前状态

正式基线为 `1743b2c29deff60681c158ecabc4ea01393baf5f`。本轮修复集中处理 `canonical_identity_random_alphabet_contract_mismatch`：生成器不再把 `token_urlsafe()` 的随机首字符直接交给 `_SAFE_RUN_IDENTITY` 校验。

## 已完成与限制

修复使用显式安全字母表和 `secrets.choice`，未修改 schema、canonical bytes、bundle SHA、预算、任务、Provider 或 evaluator。测试仅使用 test-only 或确定性 fixture；没有生成或保存真实 A3 identity。PR #83 保持 OPEN，不合并。

## 出口

需在 CI 与 post-merge zero-provider readiness 中确认定向/完整测试、exact-main validate-only、bundle SHA 一致、4/4 unique instances、8/8 task-runs、`0 / 0 / CNY 0`、`active_reservation=null`、Secret value read=false，且 paid job skipped。满足后停止并等待新的明确 A3 授权。

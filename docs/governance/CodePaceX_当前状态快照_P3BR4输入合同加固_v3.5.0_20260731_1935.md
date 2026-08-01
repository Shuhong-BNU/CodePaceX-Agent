# CodePaceX 当前状态快照：P3-B-R4 输入合同加固

> 文档版本：v3.5.0
> 生成时间：2026-07-31 19:35（UTC+8）
> 状态：已由 PR #82 普通合并；R5 post-merge 核验通过；未获新的付费授权
> 语言：中文

## 当前结论

P3-B-R4 修复的对象是 paid entrypoint 的输入合同，而不是 Provider、任务环境或生产适配层。R4 提供单一 canonical input bundle、零 Provider validate-only、精确 main SHA 绑定、shell-safe 传递、入口失败 Artifact、最小 Secret scope 与跨 Run 固定并发组。

    formal_main_at_a2: 4a5b66787f1d7ab0619b86eb3c9fb7f0f7b6cd72
    a2_failed_run: 30620506129
    a2_paid_job: 91123686219
    a2_task_runs: 0/8
    a2_artifact_count: 0
    a2_provider_requests_usage_charge: 0 / 0 / CNY 0
    a2_active_reservation: null
    r4_status: merged_post_merge_zero_provider_verified
    p4: blocked

历史机器标签 blocked_preflight_task_environment_missing 保留为第一次失败的机器标签；其精确因果根因是已修复的 production_adapter_argument_shape_mismatch。A2 的近端根因是 authorization_acknowledgement_protocol_prefix_mismatch；更高层根因是实际 workflow inputs 未曾在 one-time dispatch 前通过 validate_paid_inputs()。三者不可互相替代。

## R4 零 Provider 证据

- production-adapter preflight：8/8 冻结 task-run、4/4 唯一实例均到达 Provider 初始化边界前；Provider transport hard-disabled。
- Provider requests / Usage / charge：0 / 0 / CNY 0；active_reservation=null；Secret value read=false；paid job=skipped。
- 新 freeze SHA-256：4c1e4468b2685c198a1eeed03e607963d3514daaa17a6068fa7b4c832d9054bd；canonical SHA-256：f4d20dce7246f4dc825cd540abf80c983302e77e0b58707497bd412e37f0ad48；readiness SHA-256：d5e5c0c685617fc73f0bb29200a01f63388ea87ab00a8642b1464f1c081b2484。

没有生成、显示或复用真实 acknowledgement、dispatch token、internal run ID；没有 dispatch paid workflow。

## R5 合并后状态

PR #82 已在锁定 head `b280a1abc7bca9d2ff6dfc7236a76f757a9fde8f` 下普通合并，merge commit / 当前 formal main 为 `844d1098f2f62119947afe1b2dda33d04d50cc6c`。main CI Run 30630775458 的 Ubuntu / macOS 均成功。exact-main validate-only 通过，并输出脱敏 `paid-input-preflight.json`；本地同构 zero-provider readiness 的 8/8 task-run、4/4 unique instances 均通过。详情见 `CodePaceX_P3BR5锁定Head合并与PostMerge零Provider核验_v1.0.0_20260731_2035.md`。

## 授权前边界

R4 不构成 paid rerun 授权。下一次 paid attempt 仍必须依次满足：R4 PR 合并、post-merge 只读核验、用户新的明确授权、用户提供全新 acknowledgement/token/run identity，随后先通过 validate-only；P4 继续阻塞。

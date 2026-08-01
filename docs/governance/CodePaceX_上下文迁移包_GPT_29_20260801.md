# CodePaceX 上下文迁移包 GPT-29：P3-B A4 Gate 修复与 A5 条件执行

## 当前基线

- A4 前 formal main：`adcf8483e77bd45d8aee3e16cbc5d086c7d05050`。
- A4 Run `30655334724` 已消耗且失败，不可 retry 或复用。
- PR #83 必须保持 OPEN、Head 不变。

## 已确认链路

外层 `p3b_paid_executor.run_paid_executor()` 创建的 P3-B `PaidRunGate` 已使用 `allow_descendant_head=true`。真实子 Agent 在 `ProviderRequestBudget.from_environment()` 重新创建 gate 时缺失该语义，错误地要求 authorization commit 与 current HEAD 完全相等。A4 因此在 Provider transport 前失败；随后 terminal collector 的 raw Artifact 检查遮蔽了原始错误。

## 阶段一修复

- bridge 环境显式携带只允许 `0`/`1` 的 descendant commit-binding 合同；全局默认仍严格；
- child gate 继承父 gate 的明确合同，非祖先仍 fail-closed；
- terminal record 保留 primary error，raw Artifact 缺失为 secondary error；
- 已启动 task-run 写 terminal record 和顶层 summary，不伪造 request/evaluator evidence；
- 代码身份变化后已按 `write_artifacts()` 重建 freeze/readiness identity。

## 已完成本地证据

`tests/test_p3b_paid_executor.py`、`tests/test_p3b_post_merge_rebind.py`、`tests/test_paid_gate.py`、`tests/test_evaluation_v2_control_canary.py` 合计 `106 passed`。其中 production-path test 真实经过 P3-B coordinator、`_real_task_executor`、`_full_task_executor`、child budget bridge 和 Provider initialization boundary，覆盖 8/8 task-run；transport hard-disabled。

## 严格下一步

完成阶段一 PR、CI、locked-head ordinary merge 和 post-merge readiness 前，严禁生成 A5 identity 或 dispatch。全部门通过后才按用户已给出的条件授权生成一次全新 A5 bundle；验证失败即停止，不修复后在同一授权内 dispatch。P4 不启动。

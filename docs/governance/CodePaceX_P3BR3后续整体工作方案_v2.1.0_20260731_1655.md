# CodePaceX 后续整体工作方案：P3-B-R3 后

> 版本：v2.1.0  
> 状态：当前有效  
> 适用范围：最终付费授权前及用户后续明确授权之后

## 已完成项

1. PR #79 已修复 P3-B production adapter 参数层级错误，并完成 main 与 post-merge 零 Provider 验收。
2. PR #80 已合并中文治理文档。
3. 最终 main CI `30617653901` 和 readiness `30617786424` 成功，4/4 唯一实例、8/8 task-run 全部在 Provider transport 前停止。

## 当前停止点

当前只允许保留 zero-provider readiness 证据：Provider requests / Usage / charge 为 `0 / 0 / CNY 0`，`active_reservation=null`，paid job skipped。预算字段仍是 `formal_proposal_not_authorized`，不构成付费授权。

## 下一阶段的必要前置

只有用户再次明确授权 P3-B paid execution 后，才可以在合并后的 formal main 上创建一次性 acknowledgement、dispatch token 和 internal run ID，并逐项核对：

- `formal_main`、freeze byte/canonical SHA、allocation hash、authorization hash 与用户授权输入一致；
- parent cap、spendable total、safety reserve 和 8 个 child cap 完整闭合；
- 只执行冻结的 8 个 task-run，顺序、treatment、model、Prompt、Provider、endpoint、Evaluator、Pricing 和 budget 不得改变；
- paid workflow 的唯一入口、单次 dispatch guard、ledger settlement 和 active reservation 均可审计；
- 任一合同不匹配、Secret metadata 缺失或 Provider transport 前置条件失败时 fail-closed。

## 明确禁止

在新的明确授权之前不得 dispatch paid workflow、retry、rerun、continuation、fallback、复用历史身份、启动 P4、创建 Tag/Release 或扩大任务范围。`blocked_preflight_task_environment_missing` 只保留为历史机器标签；当前根因记录为 `production_adapter_argument_shape_mismatch`。

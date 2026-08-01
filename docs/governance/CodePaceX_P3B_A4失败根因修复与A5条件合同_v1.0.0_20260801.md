# CodePaceX P3-B A4 失败根因修复与 A5 条件合同

> 状态：阶段一零 Provider 修复完成本地验证，等待集中式 PR、CI、锁定 Head 审阅与普通合并。A5 尚未解锁，未生成 A5 identity，未 dispatch。

## A4 不可变事实

- formal main：`adcf8483e77bd45d8aee3e16cbc5d086c7d05050`。
- A4 Run `30655334724`、paid job `91238227880` 已失败并消耗；不得 retry、rerun、continuation 或复用其 identity。
- paid Artifact `8803119161` 的 digest 为 `sha256:b6d8659693965fce13b206d1ec78eaa904808c88286e72bdcd5bb30d5ef4d6ea`。
- preflight Artifact `8803110787` 的 digest 为 `sha256:cecbdca55ed6ad32a1c5fe3ea5430efeb485738926bf54bb0ef0f99d107bc37a`。
- 仅启动第一个 V2 task-run；没有 terminal record、paired result、模型响应或 Provider request。账本 `spent_cny=0`、`active_reservation=null`。

## 根因矩阵

| 层级 | A4 行为 | 结论 |
| --- | --- | --- |
| 外层 P3-B gate | 以 `allow_descendant_head=true` 构造 | 正确允许 frozen authorization commit 为 execution main 的祖先 |
| 子 Agent budget bridge | 由环境变量重新构造 `PaidRunGate`，遗漏该语义 | 原始根因：`budget_authorization_commit_binding_mismatch` |
| Provider transport | 子 gate 初始化即失败 | 未到达 transport；requests / Usage / charge 为 `0 / 0 / CNY 0` |
| Artifact collector | raw Artifact 不完整即抛 `P3-B paid executor lacks required raw Artifact` | 掩盖性 secondary error，不是原始失败 |

历史机器标签、P3-B-R1 参数层级错误、A2 acknowledgement 前缀错误与本次根因是不同事实，互不替代。

## 最小修复合同

`provider_request_budget_environment()` 现在显式传递 `CODEPACEX_BUDGET_ALLOW_DESCENDANT_HEAD`，值只能是 `0` 或 `1`。`ProviderRequestBudget.from_environment()` 只按该明确合同重建 child gate。`PaidRunGate` 的全局默认仍为严格 `allow_descendant_head=false`；未显式启用的任何现有调用方不改变行为。

P3-B paid executor 的 terminal record 改为记录：

- `primary_error`：对 A4 原始 gate 失败保留 `budget_authorization_commit_binding_mismatch` 与非敏感消息摘要；
- `raw_artifacts_complete` 和 `missing_raw_artifacts`；
- `secondary_errors`：将缺少 raw Artifact 单列而不覆盖 primary error；
- 顶层 `p3b-paid-execution-summary.json`：已启动 task-run 即使在 Provider 前失败也会写出，且不伪造 `agent-request-record.json`、Candidate 或 evaluator report。

冻结 authorization commit 必须是 execution main 的 Git 祖先；canonical bundle 的 `expected_main_sha` 仍必须精确等于 workflow checkout 的 HEAD。非祖先、SHA 漂移、freeze/allocation/pricing 漂移仍 fail-closed。预算、模型、Prompt、Provider、任务顺序和 evaluator 未修改。

## A5 条件

A5 不是当前操作。仅当此修复 PR 的 locked Head 保持不变、普通 merge 后的新 formal main 通过 Ubuntu/macOS Main CI、exact-main validate-only、8/8 hard-disabled production rehearsal、4/4 paired merge 与所有零 Provider 门时，才可使用该次明确条件授权生成一次全新 A5 bundle 并进行一次 dispatch。任何阶段一 blocker 使 A5 授权失效；不得用 A4 或历史 identity 补跑。

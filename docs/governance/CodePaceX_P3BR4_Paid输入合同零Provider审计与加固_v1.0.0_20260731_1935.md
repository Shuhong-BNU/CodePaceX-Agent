# CodePaceX P3-B-R4：Paid 输入合同零 Provider 审计与加固

> 版本：v1.0.0
> 状态：待 PR 审核
> 范围：只覆盖 P3-B paid entrypoint；不含新的 paid authorization

## 审计矩阵

| 项目 | R4 结论 | 分类 | 加固结果 |
| --- | --- | --- | --- |
| workflow inputs | A2 传入 V2 acknowledgement 前缀，而 executor 需要 canonical prefix | 已确认错误 / immediate blocker | canonical_paid_input_bundle() 为仓库单一真源；workflow 与 CLI 共用 bundle |
| validate_paid_inputs() | 真实 workflow inputs 未先走该入口 | 高层根因 / immediate blocker | validate-only 走同一函数并输出 JSON evidence |
| acknowledgement 语法 | prefix-only 检查允许人工猜测与不安全后缀 | 潜在安全风险 | REQUIRED_ACKNOWLEDGEMENT_PREFIX 加严格 suffix；空格、引号、换行拒绝 |
| dispatch token / run ID | 原校验未明确字符白名单 | 潜在安全风险 | ASCII 字符白名单与最短长度 fail-closed |
| freeze / allocation / authorization / budget | freeze 和 allocation 已绑定；cap 固定 | 已确认有效 | validate-only 继续逐项校验，漂移生成 failure Artifact |
| main binding | 只依赖历史祖先关系不足 | immediate blocker | workflow checkout expected_main_sha；executor 要求 HEAD 等于该 SHA 且 GitHub SHA 一致 |
| shell quoting | workflow 曾把 inputs 拼入单引号 | 潜在安全风险 | 输入只经 step env 写入 JSON bundle，再作为安全引用路径传入 Python |
| Secret scope | BAILIAN_API_KEY 曾在 paid job 层可见 | 潜在安全风险 | 仅 Provider-capable 最后 step 持有 Secret；其他 step 只有布尔 presence |
| concurrency | Artifact-local guard 不跨 Run | 已确认限制 / 治理歧义 | 固定 group p3b-paid-execution-global-v1；文档明确 Artifact-local 边界 |
| entry failure Artifact | A2 未建立 Artifact 即失败 | immediate blocker | validate-only 先创建 artifact root；输入失败写脱敏 preflight-failure.json |
| readiness 同构 | readiness 未证明 actual paid input entrypoint | 已确认缺口 | 真实 workflow bundle 经 CLI validate-only；另保留生产 adapter 8-run preflight |

## 实现合同

evals/evaluation_v2/p3b_paid_executor.py 暴露 REQUIRED_ACKNOWLEDGEMENT_PREFIX 与 canonical_paid_input_bundle()。validate-only 不创建 workspace、Provider client、ledger reservation 或 paid task record；成功写 paid-input-preflight.json，失败写 preflight-failure.json。两个文件只含 input hash、main/freeze/allocation identity、错误分类与零 Provider 字段；不得含 Secret、token 或 acknowledgement 原文。

workflow 的 paid job 仅在 workflow_dispatch、main ref 和完整输入下出现；它 checkout 精确 expected_main_sha，先运行 validate-only，且只有 final Provider-capable step receives BAILIAN_API_KEY。PR 路径的 paid job 仍 skipped。

## 绑定与命名

R4 不再将文件内容 SHA-256 称为 Git blob。统一字段为：

- workflow_content_sha256=0a48f655f8a6cb12347e5c794bbc6b321284c258f3b27f1672d0e0db0860eab6
- paid_executor_content_sha256=5eca4015570f9f9744bd4fbe4d4a6ba667baa0c29731bd47a83864396585bb46
- paid_gate_content_sha256=8399579370ec7cfee1a2eb1b72638068665b100fe3b09bcc4135642731f942dc
- freeze_base_commit=2794e27220d3fada3bd0fdd3a1a14ff50e3a6034
- execution_main_head=null（本地 zero-provider readiness 未执行 paid checkout）

## 回归与零 Provider 验证

定向测试覆盖 canonical acknowledgement、V2 近似错误、小写、前后空格、引号、换行、main/freeze/allocation/cap 漂移、token/run ID 白名单、失败 Artifact、CLI bundle、zero Provider 边界与 workflow Secret/concurrency 规则。生产适配 preflight 覆盖 8 个 run 的 P3-B coordinator 到 real adapter、shared full replay、environment mapping、Provider initialization boundary。

本轮不 dispatch paid workflow，不调用 Provider，不启动 P4，不生成新的真实授权输入。PR #81 的 A2 中文失败审计已吸收到本文件和当前状态快照；PR #81 应标记为 superseded/关闭，不得合并。
